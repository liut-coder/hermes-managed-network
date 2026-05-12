from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import time
from importlib.metadata import PackageNotFoundError, version as package_version
import shutil
import shlex
import socket
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer

from .components import ComponentManifest, load_builtin_components
from .deploy import render_deploy_plan_json, render_deploy_status_json
from .discovery import discover_services_from_file
from .docs_generate import load_registry_and_generate_docs
from .docs_sync import (
    DEFAULT_SERVER_DOC_ROOT,
    DEFAULT_SERVICE_DOC_ROOT,
    build_docs_sync_plan_from_path,
    parse_rename_host_args,
    render_docs_sync_plan_json,
)
from .docs import (
    DEFAULT_DOCS_ROOT,
    DEFAULT_SERVICE_ROOT,
    ServiceDoc,
    generate_docs,
    write_server_doc,
    write_server_index,
    write_domain_index,
    write_runbook_index,
    write_service_doc,
    write_service_index,
)
from .executor import PlaybookExecutor, SSHExecutionError, classify_ssh_failure, run_ssh_task, ssh_target_details_for_node, ssh_target_for_node
from .inspect import collect_local_inventory, inventory_to_json
from .inventory import NodeRegistry
from .orchestrator import OrchestratorService
from .playbook import Playbook
from .platforms import NodeRuntimeProfile, ServiceManager, classify_capabilities, probe_from_facts, render_service_manager_installer
from .network import NetworkNodeRecord, NetworkProviderError, NetworkSyncResult, get_network_provider
from .network_acl import dispatch_approved_network_acl_apply, sha256_text
from .service_registry import DEFAULT_SERVICE_REGISTRY_PATH
from .storage import SQLiteStore, ServiceRecord
from .uptime import render_uptime_plan_json
from .approval_gateway import (
    ApprovalGatewayClientConfig,
    ApprovalGatewayHttpApiClient,
    TelegramApprovalGatewayClient,
    poll_once,
    process_telegram_callbacks,
)
from .approval_notifications import build_approval_card
from .telegram_gateway import HttpGatewayApiClient, TelegramBotApiClient, poll_once as telegram_poll_once
from .tokens import JoinTokenStore
from .version import current_version_info

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()
DEFAULT_PLAYBOOK_DIR = Path("playbooks")
INSTALL_URL = "https://raw.githubusercontent.com/liut-coder/hermes-managed-network/main/install.sh"
SERVICE_NAME = "hermes-managed-network.service"

app = typer.Typer(
    help=(
        "Hermes 托管组网主控命令行\n\n"
        "示例：\n"
        "  hmn                       打开控制台\n"
        "  hmn wake                  接入新机器\n"
        "  hmn node list             查看节点\n"
        "  hmn node confirm          自动确认 pending 节点\n"
        "  hmn node status           查看节点详情\n"
        "  hmn node doctor           检查节点登记状态\n"
        "  hmn node heartbeat-command 生成节点心跳命令\n"
        "  hmn node rotate-fingerprint 轮换节点指纹\n"
        "  hmn node install-heartbeat 安装节点心跳/worker\n"
        "  hmn node worker-status     查看节点 worker 安装状态\n"
        "  hmn network status         查看网络 provider 状态\n"
        "  hmn network sync           同步 Headscale 节点映射\n"
        "  hmn network preauth-key create 生成 Headscale 接入 key\n"
        "  hmn network node tags set 设置节点 tag（审批）\n"
        "  hmn task run              下发任务（worker/ssh）\n"
        "  hmn task list             查看任务队列\n"
        "  hmn task ssh-run-next     执行下一个 SSH 任务\n"
        "  hmn orchestrator enqueue  加入托管调度队列\n"
        "  hmn orchestrator worker register 登记调度 worker\n"
        "  hmn orchestrator worker update   更新 worker 状态\n"
        "  hmn orchestrator status   查看调度状态\n"
        "  hmn orchestrator tick     执行单轮调度\n"
        "  hmn orchestrator report   输出简短报告\n"
        "  hmn approval list         查看待审批操作\n"
        "  hmn approval-gateway poll-once --client telegram 发送一次审批通知\n"
        "  hmn component list        查看可用组件\n"
        "  hmn component plan        生成组件执行计划\n"
        "  hmn component apply       记录组件期望状态\n"
        "  hmn component verify      独立验证组件\n"
        "  hmn component uninstall   卸载组件\n"
        "  hmn monitor status        查看节点监控闭环\n"
        "  hmn backup plan           生成备份 dry-run 计划\n"
        "  hmn backup run            创建本地归档和 checksum\n"
        "  hmn backup verify         验证备份归档 checksum\n"
        "  hmn backup status         查看最近备份状态\n"
        "  hmn audit list            查看审计\n"
        "  hmn token create          创建 token\n"
        "  hmn version               查看版本\n"
        "  hmn update                输出更新命令\n"
        "  hmn doctor                巡检主控安装状态\n"
        "  hmn uninstall             查看卸载命令"
    ),
    invoke_without_command=True,
)
token_app = typer.Typer(help="管理一次性节点接入令牌")
node_app = typer.Typer(help="管理已登记节点")
playbook_app = typer.Typer(help="运行本地 playbook")
audit_app = typer.Typer(help="查看审计事件")
task_app = typer.Typer(help="下发和查看节点任务")
orchestrator_app = typer.Typer(help="全托管调度器")
orchestrator_worker_app = typer.Typer(help="管理调度 worker")
approval_app = typer.Typer(help="管理高风险操作审批")
component_app = typer.Typer(help="管理按需加载组件")
monitor_app = typer.Typer(help="监控节点健康闭环")
backup_app = typer.Typer(help="备份 dry-run/manifest/checksum 闭环")
network_app = typer.Typer(help="管理网络 provider 与 Headscale 同步")
approval_gateway_app = typer.Typer(help="运行多客户端审批网关")
telegram_gateway_app = typer.Typer(help="运行 Telegram 审批网关（兼容旧命令）")
docs_app = typer.Typer(help="生成机器/服务资产文档")
docs_sync_app = typer.Typer(help="生成集中 docs-sync dry-run 计划")
service_app = typer.Typer(help="管理服务资产登记")
inspect_app = typer.Typer(help="盘点节点资产")
discover_app = typer.Typer(help="从盘点结果发现服务")
uptime_app = typer.Typer(help="生成 Uptime Kuma dry-run 规划")
deploy_app = typer.Typer(help="生成部署计划与状态聚合")
app.add_typer(token_app, name="token")
app.add_typer(node_app, name="node")
app.add_typer(network_app, name="network")
app.add_typer(playbook_app, name="playbook")
app.add_typer(audit_app, name="audit")
app.add_typer(task_app, name="task")
app.add_typer(orchestrator_app, name="orchestrator")
orchestrator_app.add_typer(orchestrator_worker_app, name="worker")
app.add_typer(approval_app, name="approval")
app.add_typer(component_app, name="component")
app.add_typer(monitor_app, name="monitor")
app.add_typer(backup_app, name="backup")
app.add_typer(approval_gateway_app, name="approval-gateway")
app.add_typer(telegram_gateway_app, name="telegram-gateway")
app.add_typer(docs_app, name="docs")
docs_app.add_typer(docs_sync_app, name="sync")
app.add_typer(service_app, name="service")
app.add_typer(inspect_app, name="inspect")
app.add_typer(discover_app, name="discover")
app.add_typer(uptime_app, name="uptime")
app.add_typer(deploy_app, name="deploy")


def _default_db() -> Path:
    env_db = os.environ.get("HMN_DB")
    if env_db:
        return Path(env_db).expanduser()
    master_env = _read_master_env()
    if master_env.get("HMN_DB"):
        return Path(master_env["HMN_DB"]).expanduser()
    return DEFAULT_DB


def _store(db: Path | None) -> SQLiteStore:
    return SQLiteStore(db or _default_db())


def _shell_quote(value: str) -> str:
    return shlex.quote(value)


def _render_join_command(token_value: str, master_url: str, user: str, safe: bool) -> str:
    base_url = master_url.rstrip("/")
    if safe:
        return (
            "tmp=$(mktemp) && curl -fsSL {url}/scripts/join.sh -o \"$tmp\" && "
            "sha256sum \"$tmp\" && sudo HERMES_JOIN_TOKEN={token} HERMES_MASTER_URL={url} HERMES_USER={user} bash \"$tmp\""
        ).format(
            token=_shell_quote(token_value),
            url=_shell_quote(base_url),
            user=_shell_quote(user),
        )
    return (
        "sudo HERMES_JOIN_TOKEN={token} HERMES_MASTER_URL={url} HERMES_USER={user} "
        "bash -s < <(curl -fsSL {url}/scripts/join.sh)"
    ).format(
        token=_shell_quote(token_value),
        url=_shell_quote(base_url),
        user=_shell_quote(user),
    )


def _parse_labels(labels_csv: str) -> list[str]:
    return [label.strip() for label in labels_csv.split(",") if label.strip()]


def _ensure_builtin_components(store: SQLiteStore) -> dict[str, ComponentManifest]:
    components = load_builtin_components()
    for component in components.values():
        store.save_component(component)
    return components


def _parse_key_values(items: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter("--set 需要 KEY=VALUE 格式")
        key, value = item.split("=", 1)
        values[key] = value
    return values


def _component_plan(
    component: ComponentManifest,
    *,
    node_id: str,
    config: dict[str, str],
    action: str = "plan",
    mutating: bool = False,
) -> dict[str, object]:
    return {
        "component_id": component.id,
        "version": component.version,
        "node_id": node_id,
        "action": action,
        "risk": component.risk,
        "driver": component.drivers.get("default", ""),
        "config": config,
        "playbooks": component.playbooks,
        "mutating": mutating,
        "next_action": "approve/apply" if action == "plan" else "verify",
    }


def _load_component_for_node(store: SQLiteStore, component_id: str, node_id: str) -> tuple[ComponentManifest, object]:
    _ensure_builtin_components(store)
    component = store.load_component(component_id)
    if component is None:
        raise typer.BadParameter(f"未知组件: {component_id}")
    node = store.load_node(node_id)
    if node is None or node.status != "managed":
        raise typer.BadParameter(f"节点不可用或未 managed: {node_id}")
    return component, node


def _component_action_requires_approval(risk: str, *, mutating: bool) -> bool:
    return mutating and risk in {"high", "critical"}


def _request_component_action_approval(
    store: SQLiteStore,
    component: ComponentManifest,
    *,
    node_id: str,
    action: str,
    plan: dict[str, object],
    config: dict[str, str] | None = None,
    result: dict[str, object] | None = None,
):
    run = store.record_component_run(
        component_id=component.id,
        node_id=node_id,
        action=action,
        risk=component.risk,
        status="pending_approval",
        plan=plan,
        result=result or {"machine_changed": False, "approval_required": True},
    )
    approval = store.create_approval_request(
        subject_type="component_run",
        subject_id=run.run_id,
        action=f"component.{action}",
        risk=component.risk,
        requested_by="hmn",
        details={
            "action": action,
            "run_id": run.run_id,
            "component_id": component.id,
            "node_id": node_id,
            "config": config or {},
            "version": component.version,
            "driver": str(component.drivers.get("default", "")),
        },
    )
    return run, approval


def _read_master_env(path: Path = Path("/etc/hermes-managed-network/master.env")) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _first_non_loopback_ip() -> str:
    try:
        output = subprocess.check_output(["hostname", "-I"], text=True, timeout=2).strip()
        for item in output.split():
            if item and not item.startswith("127.") and ":" not in item:
                return item
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _default_master_url() -> str:
    public_url = os.environ.get("HMN_PUBLIC_URL")
    if public_url:
        return public_url.rstrip("/")
    env = _read_master_env()
    host = os.environ.get("HMN_HOST") or env.get("HMN_HOST") or "127.0.0.1"
    port = os.environ.get("HMN_PORT") or env.get("HMN_PORT") or "8765"
    if host in {"0.0.0.0", "::", ""}:
        host = _first_non_loopback_ip()
    return f"http://{host}:{port}"


def _default_node_hostname(store: SQLiteStore) -> str:
    return f"node-server{len(store.list_nodes()) + 1}"


def _show_menu() -> None:
    typer.echo("HMN 快速菜单")
    typer.echo("1. hmn wake                         接入新机器")
    typer.echo("2. hmn node list                    查看节点")
    typer.echo("3. hmn node confirm                 确认 pending 节点")
    typer.echo("4. hmn node status                  查看节点详情")
    typer.echo("5. hmn node doctor                  检查节点")
    typer.echo("6. hmn node heartbeat-command       生成心跳命令")
    typer.echo("7. hmn node install-heartbeat       安装心跳/worker")
    typer.echo("8. hmn node worker-status           查看 worker 状态")
    typer.echo("9. hmn network status                查看网络状态")
    typer.echo("10. hmn network sync                 同步 Headscale 映射")
    typer.echo("11. hmn network preauth-key create   生成接入 key")
    typer.echo("12. hmn network node tags set        设置节点 tag（审批）")
    typer.echo("13. hmn task run                     下发任务（worker/ssh）")
    typer.echo("14. hmn task list                    查看任务")
    typer.echo("    hmn task ssh-run-next            执行下一个 SSH 任务")
    typer.echo("15. hmn orchestrator enqueue         加入托管队列")
    typer.echo("    hmn orchestrator worker register 登记调度 worker")
    typer.echo("    hmn orchestrator worker update   更新 worker 状态")
    typer.echo("    hmn orchestrator status          查看调度状态")
    typer.echo("    hmn orchestrator tick            执行单轮调度")
    typer.echo("    hmn orchestrator report          输出简短报告")
    typer.echo("16. hmn approval list                查看审批")
    typer.echo("    hmn telegram-gateway poll-once   发送审批通知")
    typer.echo("17. hmn component list               查看组件")
    typer.echo("18. hmn component status             查看组件状态")
    typer.echo("19. hmn component apply              记录组件状态")
    typer.echo("20. hmn component verify             独立验证组件")
    typer.echo("21. hmn component uninstall          卸载组件")
    typer.echo("22. hmn monitor status               查看监控闭环")
    typer.echo("23. hmn backup plan                  生成备份 dry-run 计划")
    typer.echo("24. hmn backup run                   创建本地归档/checksum")
    typer.echo("    hmn backup verify                验证备份归档")
    typer.echo("25. hmn backup status                查看备份状态")
    typer.echo("26. hmn audit list                   查看审计")
    typer.echo("27. hmn token create                 创建 token")
    typer.echo("    hmn token list / expire / revoke 管理 token")
    typer.echo("28. hmn version                      查看版本")
    typer.echo("29. hmn update                       更新主控")
    typer.echo("30. hmn uninstall                    卸载主控")
    typer.echo("")
    typer.echo("示例：")
    typer.echo("  hmn wake")
    typer.echo("  hmn node confirm")
    typer.echo("  hmn node status")
    typer.echo("  hmn node doctor")
    typer.echo("  hmn node heartbeat-command")
    typer.echo("  hmn node install-heartbeat")
    typer.echo("  hmn node worker-status")
    typer.echo("  hmn network node tags set --node node1 --tag tag:worker")
    typer.echo("  hmn task run 'uptime'")
    typer.echo("  hmn task run 'uptime' --executor ssh --wait")
    typer.echo("  hmn task list")
    typer.echo("  hmn task ssh-run-next")
    typer.echo("  hmn orchestrator enqueue --title '巡检服务' --scope ops --priority 5")
    typer.echo("  hmn orchestrator worker register --id miskrobot --transport bridge --label standby")
    typer.echo("  hmn orchestrator worker update --id miskrobot --status offline")
    typer.echo("  hmn orchestrator status")
    typer.echo("  hmn orchestrator tick")
    typer.echo("  hmn orchestrator report")
    typer.echo("  hmn telegram-gateway poll-once")
    typer.echo("  hmn component list")
    typer.echo("  hmn component plan reverse-proxy --node node1 --set domain=example.com --set upstream=http://127.0.0.1:3000")
    typer.echo("  hmn component plan forwarder --node node1 --set listen=tcp://0.0.0.0:8443 --set target=tcp://10.0.0.10:443")
    typer.echo("  hmn component apply reverse-proxy --node node1 --set domain=example.com")
    typer.echo("  hmn component verify reverse-proxy --node node1")
    typer.echo("  hmn component uninstall reverse-proxy --node node1")
    typer.echo("  hmn monitor status --node node1")
    typer.echo("  hmn backup plan --node node1 --include /srv/app")
    typer.echo("  hmn backup run --node node1 --include /srv/app --output-dir ./hmn-backups")
    typer.echo("  hmn backup verify --node node1")
    typer.echo("  hmn backup status --node node1")
    typer.echo("  hmn audit list")
    typer.echo("  hmn token list")
    typer.echo("  hmn token expire")
    typer.echo("  hmn token revoke <TOKEN>")
    typer.echo("  hmn version")
    typer.echo("帮助：hmn <command> --help")


def _show_interactive_menu(db: Path | None = None) -> None:
    db = db or _default_db()
    while True:
        typer.echo("")
        typer.echo("HMN 控制台")
        typer.echo("1) hmn wake          接入新机器")
        typer.echo("2) hmn node list     查看节点")
        typer.echo("3) hmn node confirm  确认节点")
        typer.echo("4) hmn node status   节点详情")
        typer.echo("5) hmn node doctor   检查节点")
        typer.echo("6) hmn node heartbeat-command  心跳命令")
        typer.echo("7) hmn node install-heartbeat  安装心跳/worker")
        typer.echo("8) hmn node worker-status     worker 状态")
        typer.echo("9) hmn network status 查看网络状态")
        typer.echo("10) hmn network sync  同步 Headscale 映射")
        typer.echo("11) hmn network preauth-key create 生成接入 key")
        typer.echo("12) hmn network node tags set  设置节点 tag（审批）")
        typer.echo("13) hmn task run      下发任务")
        typer.echo("14) hmn task list    查看任务")
        typer.echo("    hmn task ssh-run-next  执行 SSH 任务")
        typer.echo("15) hmn component list   查看组件")
        typer.echo("16) hmn component status 组件状态")
        typer.echo("17) hmn component apply  记录组件状态")
        typer.echo("18) hmn component verify 独立验证组件")
        typer.echo("19) hmn component uninstall 卸载组件")
        typer.echo("20) hmn audit list   查看审计")
        typer.echo("21) hmn monitor status 查看监控闭环")
        typer.echo("22) hmn backup plan   备份 dry-run 计划")
        typer.echo("23) hmn backup run    创建本地归档/checksum")
        typer.echo("    hmn backup verify 验证备份归档")
        typer.echo("24) hmn backup status 查看备份状态")
        typer.echo("25) hmn token create 创建 token")
        typer.echo("    hmn token list / expire / revoke 管理 token")
        typer.echo("26) hmn version      查看版本")
        typer.echo("27) hmn update       更新主控")
        typer.echo("28) hmn uninstall    卸载主控")
        typer.echo("q) quit              退出")
        choice = typer.prompt("选择编号或命令", default="1")
        normalized = choice.strip().lower()
        if normalized in {"1", "wake", "hmn wake"}:
            wake(db=db, master_url=None)
            return
        if normalized in {"2", "node", "nodes", "node list", "hmn node list"}:
            list_nodes(db=db, now=None)
            return
        if normalized in {"3", "confirm", "node confirm", "hmn node confirm"}:
            confirm_node(node_id=None, bundle=["observe"], db=db)
            return
        if normalized in {"4", "status", "node status", "hmn node status"}:
            status_node(node_id=None, db=db, now=None)
            return
        if normalized in {"5", "doctor", "node doctor", "hmn node doctor"}:
            doctor_node(node_id=None, db=db, no_ssh_check=False)
            return
        if normalized in {"6", "heartbeat", "heartbeat-command", "node heartbeat-command", "hmn node heartbeat-command"}:
            heartbeat_command(node_id=None, master_url=None, db=db)
            return
        if normalized in {"7", "install-heartbeat", "node install-heartbeat", "hmn node install-heartbeat"}:
            install_heartbeat(node_id=None, master_url=None, endpoint=[], service_manager=ServiceManager.SYSTEMD, db=db)
            return
        if normalized in {"8", "worker", "worker status", "node worker-status", "hmn node worker-status"}:
            worker_status(node_id=None, db=db)
            return
        if normalized in {"9", "network", "network status", "hmn network status"}:
            network_status(json_output=False)
            return
        if normalized in {"10", "network sync", "hmn network sync"}:
            network_sync(db=db)
            return
        if normalized in {"11", "network preauth-key", "network preauth-key create", "hmn network preauth-key create"}:
            node_id = typer.prompt("节点 ID/hostname", default="node1")
            network_preauth_key_create(node_id=node_id, tag=[], reusable=False, ephemeral=False, expiration=None, db=db)
            return
        if normalized in {"12", "network node tags set", "hmn network node tags set"}:
            node_id = typer.prompt("节点 ID", default="node1")
            tags_raw = typer.prompt("目标 tags（逗号分隔）", default="tag:worker")
            network_node_tags_set(node_id=node_id, tag=_parse_labels(tags_raw), db=db)
            return
        if normalized in {"13", "task run", "hmn task run"}:
            command = typer.prompt("任务命令", default="uptime")
            create_task_command(command=command, node_id=None, risk="low", db=db)
            return
        if normalized in {"14", "task", "task list", "hmn task list"}:
            list_task_commands(db=db)
            return
        if normalized in {"15", "component", "component list", "hmn component list"}:
            list_components(db=db)
            return
        if normalized in {"component status", "hmn component status"}:
            component_status(node_id=None, db=db)
            return
        if normalized in {"apply", "component apply", "hmn component apply"}:
            component = typer.prompt("组件 ID", default="forwarder")
            node_id = typer.prompt("节点 ID", default="node1")
            apply_component(component_id=component, node_id=node_id, set_values=[], db=db)
            return
        if normalized in {"18", "verify", "component verify", "hmn component verify"}:
            component = typer.prompt("组件 ID", default="forwarder")
            node_id = typer.prompt("节点 ID", default="node1")
            verify_component(component_id=component, node_id=node_id, db=db)
            return
        if normalized in {"19", "uninstall component", "component uninstall", "hmn component uninstall"}:
            component = typer.prompt("组件 ID", default="forwarder")
            node_id = typer.prompt("节点 ID", default="node1")
            uninstall_component(component_id=component, node_id=node_id, db=db)
            return
        if normalized in {"20", "21", "monitor", "monitor status", "hmn monitor status"}:
            monitor_status(node_id=None, db=db)
            return
        if normalized in {"22", "backup", "backup plan", "hmn backup plan"}:
            node_id = typer.prompt("节点 ID", default="node1")
            include_raw = typer.prompt("备份路径（逗号分隔）", default="/srv")
            backup_plan(node_id=node_id, include=_parse_labels(include_raw), output_dir=Path("./hmn-backups"), db=db)
            return
        if normalized in {"23", "backup run", "hmn backup run"}:
            node_id = typer.prompt("节点 ID", default="node1")
            include_raw = typer.prompt("备份路径（逗号分隔）", default="/srv")
            backup_run(node_id=node_id, include=_parse_labels(include_raw), output_dir=Path("./hmn-backups"), db=db)
            return
        if normalized in {"24", "backup status", "hmn backup status"}:
            backup_status(node_id=None, db=db)
            return
        if normalized in {"backup verify", "hmn backup verify"}:
            backup_verify(node_id=None, manifest_path=None, db=db)
            return
        if normalized in {"16", "20", "26", "audit", "audit list", "hmn audit list"}:
            list_audit_events(limit=50, json_output=False, db=db)
            return
        if normalized in {"17", "21", "25", "token", "token create", "hmn token create"}:
            create_token(trust_level="B", label=[], ttl_minutes=30, db=db)
            return
        if normalized in {"token list", "hmn token list"}:
            list_tokens(db=db)
            return
        if normalized in {"token expire", "hmn token expire"}:
            expire_tokens(db=db)
            return
        if normalized in {"22", "26", "version", "hmn version"}:
            version()
            return
        if normalized in {"23", "27", "update", "hmn update"}:
            update()
            return
        if normalized in {"24", "28", "uninstall", "hmn uninstall"}:
            uninstall()
            return
        if normalized in {"q", "quit", "exit"}:
            typer.echo("再见~")
            return
        typer.echo("未知选项，请重试。")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Hermes 托管组网主控命令行。"""
    if ctx.invoked_subcommand is None:
        _show_interactive_menu()


@app.command("menu")
def menu(plain: bool = typer.Option(False, "--plain", help="只打印快捷命令，不进入交互。")) -> None:
    if plain:
        _show_menu()
    else:
        _show_interactive_menu()


@app.command("version")
def version() -> None:
    """查看当前 hmn 版本。"""
    info = current_version_info()
    typer.echo(f"hmn version {info.package_version}")
    typer.echo(f"api version {info.api_version}")
    typer.echo(f"worker protocol {info.worker_protocol_version}")


@app.command("update")
def update() -> None:
    """输出主控更新命令。"""
    typer.echo("更新命令：")
    typer.echo(f"curl -fsSL {INSTALL_URL} | sudo bash")


def _status_line(label: str, ok: bool, detail: str = "") -> str:
    status = "OK" if ok else "WARN"
    suffix = f" - {detail}" if detail else ""
    return f"{label}: {status}{suffix}"


def _doctor_url_status(label: str, url: str) -> str:
    if url.strip().lower() in {"skip", "none", "off"}:
        return f"{label}: SKIP"
    try:
        from urllib import request

        with request.urlopen(url, timeout=5) as response:
            ok = 200 <= int(response.status) < 300
        return _status_line(label, ok, url)
    except Exception as exc:
        return _status_line(label, False, f"{url} ({exc})")


def _backup_status_from_manifest(manifest_values: dict[str, str], fallback_backup_dir: Path) -> tuple[bool, str, str]:
    backup_dir = Path(manifest_values.get("HMN_BACKUP_DIR") or str(fallback_backup_dir))
    stamp = manifest_values.get("HMN_LAST_BACKUP_STAMP", "")
    checks: list[Path] = []
    for key in ["BACKUP_DB", "BACKUP_ENV", "BACKUP_CONFIG", "BACKUP_METADATA"]:
        value = manifest_values.get(key, "")
        if value:
            checks.append(Path(value))
    if not checks and stamp:
        checks = [
            backup_dir / f"control-plane.{stamp}.db",
            backup_dir / f"master.{stamp}.env",
            backup_dir / f"config.{stamp}.yaml",
            backup_dir / f"metadata.{stamp}.env",
        ]
    ok = bool(checks) and all(path.exists() for path in checks)
    detail = ", ".join(str(path) for path in checks) if checks else str(backup_dir)
    rollback_command = manifest_values.get("ROLLBACK_COMMAND") or manifest_values.get("HMN_ROLLBACK_HINT") or "按 upgrade-manifest.env 恢复备份后重启服务"
    return ok, detail, rollback_command


@app.command("doctor")
def doctor_install(
    etc_dir: Path = typer.Option(Path("/etc/hermes-managed-network"), "--etc-dir", help="HMN 配置目录"),
    service_dir: Path = typer.Option(Path("/etc/systemd/system"), "--service-dir", help="systemd unit 目录"),
    backup_dir: Path = typer.Option(Path("/var/backups/hermes-managed-network"), "--backup-dir", help="升级备份目录"),
    skip_systemd: bool = typer.Option(False, "--skip-systemd", help="跳过 systemctl 在线状态检查。"),
    health_url: str | None = typer.Option(None, "--health-url", help="/healthz URL；skip/none/off 跳过"),
    version_url: str | None = typer.Option(None, "--version-url", help="/api/v1/version URL；skip/none/off 跳过"),
    log_file: Path | None = typer.Option(None, "--log-file", help="读取最近日志的文件；测试/离线巡检使用"),
) -> None:
    """巡检主控安装、升级和回滚 readiness。"""
    typer.echo("生产巡检")
    master_env_path = etc_dir / "master.env"
    master_env = _read_master_env(master_env_path)
    typer.echo(_status_line("master.env", bool(master_env), str(master_env_path)))

    db_path = Path(master_env.get("HMN_DB", "/var/lib/hermes-managed-network/control-plane.db"))
    typer.echo(_status_line("database path", db_path.parent.exists(), str(db_path)))
    typer.echo(_status_line("database file", db_path.exists(), str(db_path)))

    control_service = service_dir / SERVICE_NAME
    typer.echo(_status_line("control plane service", control_service.exists(), str(control_service)))

    approval_env = etc_dir / "approval-gateway.env"
    legacy_telegram_env = etc_dir / "telegram-gateway.env"
    approval_service = service_dir / "hermes-managed-network-approval-gateway.service"
    approval_configured = approval_env.exists() or legacy_telegram_env.exists() or approval_service.exists()
    typer.echo(_status_line("approval gateway service", not approval_configured or approval_service.exists(), str(approval_service)))

    headscale_env = etc_dir / "headscale.env"
    config_yaml = etc_dir / "config.yaml"
    typer.echo(_status_line("headscale config", headscale_env.exists() or config_yaml.exists(), str(config_yaml if config_yaml.exists() else headscale_env)))

    host = master_env.get("HMN_HOST", "127.0.0.1")
    port = master_env.get("HMN_PORT", "8765")
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    base_url = f"http://{host}:{port}"
    typer.echo(_doctor_url_status("healthz", health_url or f"{base_url}/healthz"))
    typer.echo(_doctor_url_status("api version", version_url or f"{base_url}/api/v1/version"))

    manifest = etc_dir / "upgrade-manifest.env"
    manifest_values = _read_master_env(manifest)
    typer.echo(_status_line("upgrade manifest", manifest.exists(), str(manifest) if manifest.exists() else "will be created by installer upgrade"))
    backup_ok, backup_detail, rollback_command = _backup_status_from_manifest(manifest_values, backup_dir)
    typer.echo(_status_line("upgrade backup", backup_ok, backup_detail))
    typer.echo(f"rollback command: {rollback_command}")

    if not skip_systemd:
        for unit in [SERVICE_NAME, "hermes-managed-network-approval-gateway.service"]:
            if unit == "hermes-managed-network-approval-gateway.service" and not approval_configured:
                continue
            result = subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False)
            typer.echo(_status_line(f"systemd {unit}", result.returncode == 0))

    typer.echo("最近日志提示")
    if log_file is not None and log_file.exists():
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        for line in lines:
            typer.echo(line)
    elif not skip_systemd:
        result = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", "6", "--no-pager"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.stdout:
            typer.echo(result.stdout.rstrip())
    else:
        typer.echo("SKIP")

    typer.echo("修复建议：")
    typer.echo("- 升级：hmn update")
    typer.echo("- 失败后查看：journalctl -u hermes-managed-network.service -n 80 --no-pager")
    typer.echo("- 回滚：执行 rollback command 指引，恢复 DB/env/config/metadata 后重启服务")


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _rollback_plan_from_manifest(
    manifest_values: dict[str, str],
    etc_dir: Path,
    backup_dir: Path,
    stamp: str | None,
) -> list[tuple[str, Path, Path]]:
    selected_stamp = stamp or manifest_values.get("HMN_LAST_BACKUP_STAMP", "")
    source_backup_dir = Path(manifest_values.get("HMN_BACKUP_DIR") or backup_dir).expanduser()
    if stamp:
        source_backup_dir = backup_dir.expanduser()
    if not selected_stamp:
        raise typer.BadParameter("缺少备份 stamp；请传 --stamp 或检查 upgrade-manifest.env")

    current_env = _read_master_env(etc_dir / "master.env")
    if stamp:
        backup_env_path = source_backup_dir / f"master.{selected_stamp}.env"
        backup_db_path = source_backup_dir / f"control-plane.{selected_stamp}.db"
        backup_config_path = source_backup_dir / f"config.{selected_stamp}.yaml"
        backup_metadata_path = source_backup_dir / f"metadata.{selected_stamp}.env"
    else:
        backup_env_path = Path(manifest_values.get("BACKUP_ENV") or source_backup_dir / f"master.{selected_stamp}.env")
        backup_db_path = Path(manifest_values.get("BACKUP_DB") or source_backup_dir / f"control-plane.{selected_stamp}.db")
        backup_config_path = Path(manifest_values.get("BACKUP_CONFIG") or source_backup_dir / f"config.{selected_stamp}.yaml")
        backup_metadata_path = Path(manifest_values.get("BACKUP_METADATA") or source_backup_dir / f"metadata.{selected_stamp}.env")
    backup_env = _read_master_env(backup_env_path) if backup_env_path.exists() else {}
    db_target = Path(current_env.get("HMN_DB") or backup_env.get("HMN_DB") or "/var/lib/hermes-managed-network/control-plane.db")

    return [
        ("database", backup_db_path, db_target),
        ("master.env", backup_env_path, etc_dir / "master.env"),
        ("config.yaml", backup_config_path, etc_dir / "config.yaml"),
        ("metadata", backup_metadata_path, etc_dir / f"metadata.{selected_stamp}.env"),
    ]


def _validate_rollback_plan(plan: list[tuple[str, Path, Path]], etc_dir: Path, backup_dir: Path) -> list[tuple[str, Path, Path]]:
    raw_plan = [(label, source.expanduser(), target.expanduser()) for label, source, target in plan]
    safe_plan = [(label, _safe_resolve(source), _safe_resolve(target)) for label, source, target in raw_plan]
    safe_etc = _safe_resolve(etc_dir)
    safe_backup = _safe_resolve(backup_dir)
    db_roots = [_safe_resolve(Path("/var/lib/hermes-managed-network"))]
    if safe_etc != _safe_resolve(Path("/etc/hermes-managed-network")):
        db_roots.append(safe_etc.parent.parent)

    errors: list[str] = []
    for (label, raw_source, raw_target), (_, source, target) in zip(raw_plan, safe_plan):
        if not raw_source.exists():
            errors.append(f"{label}: source missing: {raw_source}")
            continue
        if raw_source.is_symlink():
            errors.append(f"{label}: source is symlink: {raw_source}")
        if not source.is_file():
            errors.append(f"{label}: source is not a regular file: {source}")
        if not _path_is_relative_to(source, safe_backup):
            errors.append(f"{label}: source outside backup dir: {source}")
        if raw_target.exists() and raw_target.is_symlink():
            errors.append(f"{label}: target is symlink: {raw_target}")
        if raw_target.parent.exists() and raw_target.parent.is_symlink():
            errors.append(f"{label}: target parent is symlink: {raw_target.parent}")
        if label == "database":
            if not any(_path_is_relative_to(target, root) for root in db_roots):
                roots = ", ".join(str(root) for root in db_roots)
                errors.append(f"{label}: target outside allowed data dirs ({roots}): {target}")
        elif not _path_is_relative_to(target, safe_etc):
            errors.append(f"{label}: target outside etc dir: {target}")
    if errors:
        typer.echo("回滚安全校验失败：")
        for error in errors:
            typer.echo(f"- {error}")
        raise typer.Exit(1)
    return safe_plan


def _copy_file_atomically(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    dir_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        dir_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        dir_flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        dir_flags |= os.O_CLOEXEC
    temp_name = f".{target.name}.hmn-rollback.{secrets.token_hex(8)}.tmp"
    source_fd = os.open(source, source_flags)
    dir_fd = os.open(target.parent, dir_flags)
    temp_fd: int | None = None
    try:
        source_stat = os.fstat(source_fd)
        temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            temp_flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            temp_flags |= os.O_CLOEXEC
        temp_fd = os.open(temp_name, temp_flags, source_stat.st_mode & 0o777, dir_fd=dir_fd)
        with os.fdopen(source_fd, "rb", closefd=False) as source_file, os.fdopen(temp_fd, "wb", closefd=False) as temp_file:
            shutil.copyfileobj(source_file, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.fchmod(temp_fd, source_stat.st_mode & 0o777)
        try:
            os.fchown(temp_fd, source_stat.st_uid, source_stat.st_gid)
        except PermissionError:
            pass
        os.rename(temp_name, target.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except Exception:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        os.close(dir_fd)
        os.close(source_fd)


def _render_rollback_confirm_command(stamp: str | None, etc_dir: Path, backup_dir: Path, skip_systemd: bool) -> str:
    parts = ["hmn", "rollback"]
    if stamp:
        parts.extend(["--stamp", stamp])
    parts.extend(["--etc-dir", str(etc_dir), "--backup-dir", str(backup_dir)])
    if skip_systemd:
        parts.append("--skip-systemd")
    parts.append("--yes")
    return " ".join(_shell_quote(part) for part in parts)


@app.command("rollback")
def rollback(
    stamp: str | None = typer.Option(None, "--stamp", help="要恢复的备份 stamp；默认读取 upgrade-manifest.env。"),
    etc_dir: Path = typer.Option(Path("/etc/hermes-managed-network"), "--etc-dir", help="HMN 配置目录"),
    backup_dir: Path = typer.Option(Path("/var/backups/hermes-managed-network"), "--backup-dir", help="升级备份目录"),
    yes: bool = typer.Option(False, "--yes", help="确认执行恢复。默认只打印计划。"),
    skip_systemd: bool = typer.Option(False, "--skip-systemd", help="跳过 systemctl stop/start。"),
) -> None:
    """按 upgrade manifest 恢复升级前 DB/env/config/metadata。"""
    manifest = etc_dir / "upgrade-manifest.env"
    manifest_values = _read_master_env(manifest)
    plan = _rollback_plan_from_manifest(manifest_values, etc_dir, backup_dir, stamp)
    safe_plan = _validate_rollback_plan(plan, etc_dir, backup_dir)

    typer.echo("回滚计划")
    for label, source, target in safe_plan:
        typer.echo(f"- {label}: {source} -> {target}")

    if not yes:
        typer.echo("DRY RUN：未修改任何文件。")
        typer.echo("确认执行：" + _render_rollback_confirm_command(stamp, etc_dir, backup_dir, skip_systemd))
        return

    if not skip_systemd:
        stop_result = subprocess.run(["systemctl", "stop", SERVICE_NAME], check=False)
        if stop_result.returncode != 0:
            typer.echo(f"停止服务失败，已中止回滚：systemctl stop {SERVICE_NAME}")
            raise typer.Exit(stop_result.returncode)

    for _, source, target in safe_plan:
        _copy_file_atomically(source, target)

    if not skip_systemd:
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        start_result = subprocess.run(["systemctl", "start", SERVICE_NAME], check=False)
        if start_result.returncode != 0:
            typer.echo(f"启动服务失败，请手动排查：systemctl start {SERVICE_NAME}")
            raise typer.Exit(start_result.returncode)

    typer.echo("已恢复备份")
    typer.echo("建议执行：hmn doctor")


@app.command("uninstall")
def uninstall(
    yes: bool = typer.Option(False, "--yes", help="确认执行卸载。默认只显示卸载命令。"),
    keep_data: bool = typer.Option(True, "--keep-data/--purge-data", help="保留数据库和配置。"),
) -> None:
    """显示或执行主控卸载。"""
    commands = [
        f"systemctl disable --now {SERVICE_NAME} || true",
        f"rm -f /etc/systemd/system/{SERVICE_NAME}",
        "systemctl daemon-reload || true",
        "rm -f /usr/local/bin/hmn /usr/local/bin/hmn-server",
        "rm -rf /opt/hermes-managed-network",
    ]
    if not keep_data:
        commands.extend(["rm -rf /etc/hermes-managed-network", "rm -rf /var/lib/hermes-managed-network"])
    if not yes:
        typer.echo("卸载命令：")
        typer.echo("sudo bash -lc " + _shell_quote(" && ".join(commands)))
        typer.echo("")
        typer.echo("确认要由 hmn 直接执行时，使用：hmn uninstall --yes")
        typer.echo("如需同时删除数据库和配置：hmn uninstall --yes --purge-data")
        return
    if os.geteuid() != 0:
        raise typer.BadParameter("执行卸载需要 root，请使用 sudo hmn uninstall --yes")
    for command in commands:
        typer.echo(f"执行: {command}")
        subprocess.run(command, shell=True, check=False)
    typer.echo("卸载完成")


@app.command("wake")
def wake(
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    master_url: str | None = typer.Option(None, "--master-url", help="主控 URL；默认自动读取 HMN_PUBLIC_URL 或安装配置"),
    network: str | None = typer.Option(None, "--network", help="同时生成网络 provider 接入信息；当前支持 headscale"),
) -> None:
    """交互式生成节点一次性接入脚本。"""
    store = _store(db)
    default_hostname = _default_node_hostname(store)
    default_master_url = (master_url or _default_master_url()).rstrip("/")
    hostname = typer.prompt("要接入的机器 hostname", default=default_hostname)
    address = typer.prompt("机器 IP/地址，可留空", default="")
    selected_master_url = typer.prompt("主控 URL", default=default_master_url)
    trust_level = typer.prompt("信任级别 A/B/C", default="B").upper()
    labels_csv = typer.prompt("标签，逗号分隔", default="worker")
    user = typer.prompt("节点系统用户", default="hermes")
    ttl_minutes = typer.prompt("token 有效期分钟", default=30, type=int)
    labels = _parse_labels(labels_csv)
    network_tags: list[str] = []
    if isinstance(network, str) and network:
        if network.lower() != "headscale":
            raise typer.BadParameter("--network 当前仅支持 headscale")
        network_tags = _parse_labels(typer.prompt("Headscale ACL tags，逗号分隔", default="tag:worker"))

    token_store = JoinTokenStore()
    token = token_store.create(
        trust_level=trust_level,
        labels=labels,
        ttl=timedelta(minutes=ttl_minutes),
    )
    store.save_token(token)
    store.record_audit(
        event_type="token",
        subject_type="join_token",
        subject_id=token.value,
        action="create_for_wake",
        outcome="ok",
        details={
            "hostname": hostname,
            "address": address,
            "trust_level": token.trust_level,
            "labels": token.labels,
            "ttl_minutes": ttl_minutes,
        },
    )
    typer.echo("")
    typer.echo("唤醒脚本已生成")
    typer.echo(f"机器: {hostname}")
    typer.echo(f"地址: {address}")
    typer.echo(f"信任级别: {trust_level}")
    typer.echo("请复制下面这条命令到目标机器执行：")
    if isinstance(network, str) and network:
        try:
            provider = _require_network_provider()
            preauth = provider.create_preauth_key(
                node_id=hostname,
                tags=network_tags,
                reusable=False,
                ephemeral=False,
                expiration=None,
            )
        except NetworkProviderError as exc:
            typer.echo(f"Headscale 接入 key 生成失败: {exc}")
            raise typer.Exit(1) from exc
        store.record_audit(
            event_type="network",
            subject_type="headscale_preauth_key",
            subject_id=hostname,
            action="preauth_key/create_for_wake",
            outcome="ok",
            details={"provider": provider.provider_name, "hostname": hostname, "tags": preauth.tags},
        )
        typer.echo("Headscale 接入 key 已生成")
        typer.echo(preauth.key)
        endpoint = getattr(provider, "endpoint", "")
        typer.echo("先执行网络接入：")
        typer.echo(f"sudo tailscale up --login-server={endpoint} --authkey={preauth.key}")
        typer.echo("再执行 HMN 接入：")
    typer.echo(_render_join_command(token.value, selected_master_url, user, safe=True))


def _require_network_provider():
    provider = get_network_provider()
    if provider is None:
        typer.echo("未配置 network provider。请配置 HMN_CONFIG 或 /etc/hermes-managed-network/config.yaml。")
        raise typer.Exit(1)
    return provider


def _network_node_matches_hmn(network_node: NetworkNodeRecord, hmn_node) -> bool:
    names = {network_node.hostname, network_node.provider_node_id}
    return hmn_node.hostname in names or hmn_node.node_id in names


def _sync_network_nodes(store: SQLiteStore) -> NetworkSyncResult:
    provider = _require_network_provider()
    network_nodes = provider.list_nodes()
    hmn_nodes = store.list_nodes()
    linked = 0
    updated = 0
    unmatched: list[str] = []
    matched_hmn_ids: set[str] = set()
    for network_node in network_nodes:
        target = next(
            (node for node in hmn_nodes if node.node_id not in matched_hmn_ids and _network_node_matches_hmn(network_node, node)),
            None,
        )
        if target is None:
            unmatched.append(network_node.hostname or network_node.provider_node_id)
            continue
        matched_hmn_ids.add(target.node_id)
        linked += 1
        before = (
            target.network_provider,
            target.network_node_id,
            target.network_ip,
            tuple(target.network_tags),
            target.network_online,
        )
        target.network_provider = provider.provider_name
        target.network_node_id = network_node.provider_node_id
        target.network_ip = network_node.ip
        target.network_tags = list(network_node.tags)
        target.network_online = bool(network_node.online)
        after = (
            target.network_provider,
            target.network_node_id,
            target.network_ip,
            tuple(target.network_tags),
            target.network_online,
        )
        if before != after:
            updated += 1
            store.save_node(target)
    result = NetworkSyncResult(provider=provider.provider_name, linked=linked, updated=updated, unmatched=unmatched)
    store.record_audit(
        event_type="network",
        subject_type="provider",
        subject_id=provider.provider_name,
        action="sync",
        outcome="ok",
        details={"linked": linked, "updated": updated, "unmatched": unmatched},
    )
    return result


@network_app.command("status")
def network_status(json_output: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    try:
        provider = _require_network_provider()
        status = provider.status()
    except NetworkProviderError as exc:
        typer.echo(f"network status failed: {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps({
            "provider": status.provider,
            "configured": status.configured,
            "endpoint": status.endpoint,
            "node_count": status.node_count,
            "online_count": status.online_count,
            "nodes": [node.__dict__ for node in status.nodes],
        }, ensure_ascii=False))
        return
    typer.echo(f"provider: {status.provider}")
    typer.echo(f"endpoint: {status.endpoint}")
    typer.echo(f"nodes: {status.node_count}")
    typer.echo(f"online: {status.online_count}")
    for node in status.nodes:
        tags = ",".join(node.tags) if node.tags else "-"
        typer.echo(f"{node.provider_node_id}	{node.hostname}	{node.ip or '-'}	online={node.online}	tags={tags}")


@network_app.command("sync")
def network_sync(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    try:
        result = _sync_network_nodes(_store(db))
    except NetworkProviderError as exc:
        typer.echo(f"network sync failed: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"provider: {result.provider}")
    typer.echo(f"linked: {result.linked}")
    typer.echo(f"updated: {result.updated}")
    if result.unmatched:
        typer.echo("unmatched: " + ", ".join(result.unmatched))
    else:
        typer.echo("unmatched: -")


def _dispatch_approved_network_tag_update(store: SQLiteStore, approval_id: str) -> bool:
    approval = store.load_approval_request(approval_id)
    if approval is None or approval.status != "approved":
        return False
    if approval.subject_type != "network_node" or approval.action != "network.tags.set":
        return False
    details = approval.details
    node_id = str(details.get("node_id") or approval.subject_id)
    provider_node_id = str(details.get("provider_node_id") or "")
    requested_tags = [str(tag) for tag in details.get("requested_tags", [])]
    if not node_id or not provider_node_id:
        return False
    node = store.load_node(node_id)
    if node is None or node.status != "managed":
        return False
    provider = _require_network_provider()
    try:
        provider.set_node_tags(provider_node_id, requested_tags)
    except NetworkProviderError as exc:
        store.record_audit(
            event_type="network",
            subject_type="network_node",
            subject_id=node_id,
            action="tags/update",
            outcome="failed",
            details={
                "node_id": node_id,
                "provider_node_id": provider_node_id,
                "old_tags": list(node.network_tags),
                "requested_tags": requested_tags,
                "approval_id": approval.approval_id,
                "error": str(exc),
            },
        )
        raise
    old_tags = list(node.network_tags)
    node.network_tags = requested_tags
    node.network_provider = node.network_provider or provider.provider_name
    node.network_node_id = provider_node_id
    store.save_node(node)
    store.record_audit(
        event_type="network",
        subject_type="network_node",
        subject_id=node_id,
        action="tags/update",
        outcome="ok",
        details={
            "node_id": node_id,
            "provider_node_id": provider_node_id,
            "old_tags": old_tags,
            "requested_tags": requested_tags,
            "approval_id": approval.approval_id,
            "provider": provider.provider_name,
        },
    )
    return True


network_node_app = typer.Typer(help="管理网络节点")
network_tags_app = typer.Typer(help="管理网络节点 tag")
network_acl_app = typer.Typer(help="管理 Headscale ACL 文件审批")
network_app.add_typer(network_node_app, name="node")
network_node_app.add_typer(network_tags_app, name="tags")
network_app.add_typer(network_acl_app, name="acl")


@network_acl_app.command("plan")
def network_acl_plan(
    current: Path = typer.Option(..., "--current", help="当前 Headscale ACL 文件路径"),
    proposed: Path = typer.Option(..., "--proposed", help="拟应用 Headscale ACL 文件路径"),
    reload_command: str = typer.Option("systemctl reload headscale", "--reload-command", help="审批通过后执行的 reload 命令"),
    verify_command: str = typer.Option("headscale nodes list", "--verify-command", help="审批通过后执行的验证命令"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    current_path = current.expanduser()
    proposed_path = proposed.expanduser()
    if not proposed_path.exists():
        typer.echo(f"拟应用 ACL 文件不存在: {proposed_path}")
        raise typer.Exit(1)
    old_text = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
    new_text = proposed_path.read_text(encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(current_path),
            tofile=str(proposed_path),
        )
    )
    if not diff:
        typer.echo("ACL 无变化，无需审批。")
        return
    approval = _store(db).create_approval_request(
        subject_type="network_acl",
        subject_id=str(current_path),
        action="network.acl.apply",
        risk="critical",
        requested_by="hmn",
        details={
            "provider": "headscale",
            "current_path": str(current_path),
            "proposed_path": str(proposed_path),
            "diff": diff,
            "old_sha256": sha256_text(old_text),
            "new_sha256": sha256_text(new_text),
            "reload_command": reload_command,
            "verify_command": verify_command,
        },
    )
    typer.echo(f"需要审批: {approval.approval_id}")
    typer.echo("未写入 ACL；审批通过后才会 apply / reload / verify。")
    typer.echo(diff)
    raise typer.Exit(1)


@network_tags_app.command("set")
def network_node_tags_set(
    node_id: str = typer.Option(..., "--node", help="HMN 节点 ID"),
    tag: list[str] = typer.Option([], "--tag", help="目标 tag，可重复填写"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = store.load_node(node_id)
    if node is None or node.status != "managed":
        typer.echo(f"节点不可用或未 managed: {node_id}")
        raise typer.Exit(1)
    if not node.network_node_id:
        typer.echo(f"节点缺少 network_node_id，请先运行 hmn network sync: {node_id}")
        raise typer.Exit(1)
    requested_tags = list(tag)
    approval = store.create_approval_request(
        subject_type="network_node",
        subject_id=node.node_id,
        action="network.tags.set",
        risk="high",
        requested_by="hmn",
        details={
            "node_id": node.node_id,
            "provider": node.network_provider or "headscale",
            "provider_node_id": node.network_node_id,
            "old_tags": list(node.network_tags),
            "requested_tags": requested_tags,
        },
    )
    typer.echo(f"需要审批: {approval.approval_id}")
    typer.echo("未执行 provider 写操作；审批通过后才会更新网络 tags。")
    raise typer.Exit(1)


preauth_key_app = typer.Typer(help="管理 Headscale preauth key")
network_app.add_typer(preauth_key_app, name="preauth-key")


@preauth_key_app.command("create")
def network_preauth_key_create(
    node_id: str = typer.Option("", "--node", help="HMN 节点 ID/hostname，用于记录用途"),
    tag: list[str] = typer.Option([], "--tag", help="Headscale ACL tag，可重复填写"),
    reusable: bool = typer.Option(False, "--reusable/--single-use", help="是否可复用"),
    ephemeral: bool = typer.Option(False, "--ephemeral/--persistent", help="是否临时节点"),
    expiration: str | None = typer.Option(None, "--expiration", help="过期时间，按 Headscale API 字符串传递"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    try:
        provider = _require_network_provider()
        result = provider.create_preauth_key(
            node_id=node_id,
            tags=list(tag),
            reusable=reusable,
            ephemeral=ephemeral,
            expiration=expiration,
        )
    except NetworkProviderError as exc:
        typer.echo(f"preauth key create failed: {exc}")
        raise typer.Exit(1) from exc
    _store(db).record_audit(
        event_type="network",
        subject_type="headscale_preauth_key",
        subject_id=node_id or "-",
        action="preauth_key/create",
        outcome="ok",
        details={"provider": provider.provider_name, "tags": result.tags, "reusable": result.reusable, "ephemeral": result.ephemeral},
    )
    typer.echo(result.key)


@token_app.command("create")
def create_token(
    trust_level: str = typer.Option("B", "--trust", "-t", help="信任级别：A、B 或 C"),
    label: list[str] = typer.Option([], "--label", "-l", help="给接入节点附加标签，可重复填写"),
    ttl_minutes: int = typer.Option(30, "--ttl-minutes", help="令牌有效期，单位分钟"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    token_store = JoinTokenStore()
    token = token_store.create(
        trust_level=trust_level,
        labels=list(label),
        ttl=timedelta(minutes=ttl_minutes),
    )
    store = _store(db)
    store.save_token(token)
    store.record_audit(
        event_type="token",
        subject_type="join_token",
        subject_id=token.value,
        action="create",
        outcome="ok",
        details={"trust_level": token.trust_level, "labels": token.labels, "ttl_minutes": ttl_minutes},
    )
    typer.echo(token.value)


@token_app.command("list")
def list_tokens(db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径")) -> None:
    for token in _store(db).list_tokens():
        typer.echo(f"{token.value}\t{token.status}\ttrust={token.trust_level}\tlabels={','.join(token.labels)}")


@token_app.command("expire")
def expire_tokens(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    store = _store(db)
    expired_values = store.expire_pending_tokens()
    for token_value in expired_values:
        store.record_audit(
            event_type="token",
            subject_type="join_token",
            subject_id=token_value,
            action="expire",
            outcome="ok",
            details={},
        )
    count = len(expired_values)
    noun = "token" if count == 1 else "tokens"
    typer.echo(f"expired {count} {noun}")
    for token_value in expired_values:
        typer.echo(token_value)


@token_app.command("revoke")
def revoke_token(
    token_value: str = typer.Argument(...),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    token = store.load_token(token_value)
    if token is None:
        raise typer.Exit(1)
    token.status = "revoked"
    store.save_token(token)
    store.record_audit(
        event_type="token",
        subject_type="join_token",
        subject_id=token.value,
        action="revoke",
        outcome="ok",
        details={},
    )
    typer.echo(f"revoked {token.value}")


@token_app.command("join-command")
def join_command(
    token_value: str = typer.Argument(...),
    master_url: str = typer.Option(..., "--master-url", help="主控 URL"),
    user: str = typer.Option("hermes", "--user", help="目标节点上创建/使用的系统用户"),
    safe: bool = typer.Option(False, "--safe/--unsafe", help="输出更安全的下载后执行命令"),
) -> None:
    typer.echo(_render_join_command(token_value, master_url, user, safe=safe))


def _parse_now(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _node_liveness(store: SQLiteStore, node_id: str, *, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    event = _latest_heartbeat_event(store, node_id)
    if event is None:
        return {
            "state": "offline",
            "reason": "missing_heartbeat",
            "last_heartbeat": "",
            "age_seconds": None,
        }
    age_seconds = int((now - event.created_at).total_seconds())
    if event.outcome != "ok":
        state = "stale"
        reason = "heartbeat_warn"
    elif age_seconds <= 300:
        state = "online"
        reason = "fresh_heartbeat"
    elif age_seconds <= 900:
        state = "stale"
        reason = "stale_heartbeat"
    else:
        state = "offline"
        reason = "heartbeat_timeout"
    return {
        "state": state,
        "reason": reason,
        "last_heartbeat": event.created_at.isoformat(),
        "age_seconds": age_seconds,
    }


def _record_liveness_audit(store: SQLiteStore, node_id: str, liveness: dict[str, object]) -> None:
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=node_id,
        action="liveness",
        outcome=str(liveness["state"]),
        details={
            "reason": liveness["reason"],
            "last_heartbeat": liveness["last_heartbeat"],
            "age_seconds": liveness["age_seconds"],
        },
    )


@docs_app.command("server")
def docs_server(
    node_id: str = typer.Argument(..., help="节点 ID"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    output_root: Path = typer.Option(DEFAULT_DOCS_ROOT, "--output-root", help="文档根目录"),
) -> None:
    try:
        path = write_server_doc(_store(db), node_id, output_root)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(str(path))


@docs_app.command("index")
def docs_index(
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    output_root: Path = typer.Option(DEFAULT_DOCS_ROOT, "--output-root", help="文档根目录"),
) -> None:
    path = write_server_index(_store(db), output_root)
    typer.echo(str(path))


@docs_app.command("generate")
def docs_generate(
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    output_root: Path = typer.Option(DEFAULT_DOCS_ROOT, "--output-root", help="机器文档根目录"),
    service_root: Path = typer.Option(DEFAULT_SERVICE_ROOT, "--service-root", help="服务文档根目录"),
    runbook_root: Path | None = typer.Option(None, "--runbook-root", help="Runbook 源目录"),
    registry: Path | None = typer.Option(None, "--registry", help="service registry JSON 路径"),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="service registry 文档输出目录"),
) -> None:
    if registry is not None:
        generated_dir = load_registry_and_generate_docs(registry, output_dir or Path("docs"))
        typer.echo(str(generated_dir))
        return
    result = generate_docs(_store(db), output_root, service_root, runbook_root)
    typer.echo(f"生成机器文档: {result.server_count}")
    if result.service_index:
        typer.echo(f"已刷新服务索引: {result.service_index}")
    if result.domain_index:
        typer.echo(f"已刷新域名索引: {result.domain_index}")
    if result.runbook_index:
        typer.echo(f"已刷新 Runbook 索引: {result.runbook_index}")
    for path in result.paths:
        typer.echo(str(path))


@docs_app.command("service")
def docs_service(
    service_id: str = typer.Argument(..., help="服务 ID"),
    service_root: Path = typer.Option(DEFAULT_SERVICE_ROOT, "--service-root", help="服务文档根目录"),
    title: str | None = typer.Option(None, "--title", help="服务标题"),
    node: str = typer.Option("", "--node", help="所属节点/机器"),
    url: str = typer.Option("", "--url", help="对外 URL"),
    port: str = typer.Option("", "--port", help="监听端口"),
    summary: str = typer.Option("", "--summary", help="服务简介"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    from_registry: bool = typer.Option(False, "--from-registry", help="从服务登记表生成"),
) -> None:
    if from_registry:
        service = _store(db).load_service_record(service_id)
        if service is None:
            typer.echo(f"service not found: {service_id}", err=True)
            raise typer.Exit(1)
        title = title or service.name
        node = node or service.node_id
        url = url or service.health_check_url or (f"https://{service.domains[0]}" if service.domains else "")
        port = port or (",".join(str(item) for item in service.ports))
        summary = summary or f"kind={service.kind} runtime={service.runtime or '-'}"
    path = write_service_doc(
        ServiceDoc(
            service_id=service_id,
            title=title or service_id,
            node=node,
            url=url,
            port=port,
            summary=summary,
        ),
        service_root,
    )
    typer.echo(str(path))


@docs_app.command("service-index")
def docs_service_index(
    service_root: Path = typer.Option(DEFAULT_SERVICE_ROOT, "--service-root", help="服务文档根目录"),
) -> None:
    path = write_service_index(service_root)
    typer.echo(str(path))


@docs_app.command("domain-index")
def docs_domain_index(
    service_root: Path = typer.Option(DEFAULT_SERVICE_ROOT, "--service-root", help="服务文档根目录"),
) -> None:
    path = write_domain_index(service_root)
    typer.echo(str(path))


@docs_app.command("runbook-index")
def docs_runbook_index(
    service_root: Path = typer.Option(DEFAULT_SERVICE_ROOT, "--service-root", help="服务文档根目录"),
    runbook_root: Path | None = typer.Option(None, "--runbook-root", help="Runbook 源目录"),
) -> None:
    path = write_runbook_index(service_root, runbook_root)
    typer.echo(str(path))


@docs_sync_app.command("plan")
def docs_sync_plan(
    service_registry: Path = typer.Option(DEFAULT_SERVICE_REGISTRY_PATH, "--service-registry", help="service registry JSON 路径"),
    server_doc_root: Path = typer.Option(DEFAULT_SERVER_DOC_ROOT, "--server-doc-root", help="机器文档根目录"),
    service_doc_root: Path = typer.Option(DEFAULT_SERVICE_DOC_ROOT, "--service-doc-root", help="服务文档根目录"),
    rename_host: list[str] = typer.Option([], "--rename-host", help="主机改名映射 OLD=NEW，可重复"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """生成 docs-center 同步 dry-run 计划，不写入 /srv/files。"""
    try:
        rendered = render_docs_sync_plan_json(
            service_registry,
            server_doc_root=server_doc_root,
            service_doc_root=service_doc_root,
            rename_hosts=parse_rename_host_args(rename_host),
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(rendered)
        return
    payload = json.loads(rendered)
    typer.echo(f"docs sync plan: services={payload['service_count']} servers={payload['server_count']}")
    typer.echo(f"server_doc_root: {payload['server_doc_root']}")
    typer.echo(f"service_doc_root: {payload['service_doc_root']}")
    if payload.get("rename_actions"):
        typer.echo(f"rename_actions: {len(payload['rename_actions'])}")


@docs_sync_app.command("apply")
def docs_sync_apply(
    service_registry: Path = typer.Option(DEFAULT_SERVICE_REGISTRY_PATH, "--service-registry", help="service registry JSON 路径"),
    server_doc_root: Path = typer.Option(DEFAULT_SERVER_DOC_ROOT, "--server-doc-root", help="机器文档根目录"),
    service_doc_root: Path = typer.Option(DEFAULT_SERVICE_DOC_ROOT, "--service-doc-root", help="服务文档根目录"),
    rename_host: list[str] = typer.Option([], "--rename-host", help="主机改名映射 OLD=NEW，可重复"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    """为 docs-center 真实写入创建审批；审批前不写文件。"""
    try:
        plan = build_docs_sync_plan_from_path(
            service_registry,
            server_doc_root=server_doc_root,
            service_doc_root=service_doc_root,
            rename_hosts=parse_rename_host_args(rename_host),
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    store = _store(db)
    approval = store.create_approval_request(
        subject_type="docs_sync",
        subject_id=str(service_registry),
        action="docs.sync.apply",
        risk="high",
        requested_by="hmn",
        details={"plan": plan, "service_registry": str(service_registry)},
    )
    typer.echo(f"需要审批: {approval.approval_id}")
    typer.echo("未写入 docs-center；审批通过后才会 apply。")
    typer.echo(f"services={plan['service_count']} servers={plan['server_count']}")
    raise typer.Exit(1)


def _service_record_from_payload(payload: dict[str, object]) -> ServiceRecord:
    return ServiceRecord(
        service_id=str(payload["service_id"]),
        name=str(payload.get("name") or payload["service_id"]),
        node_id=str(payload.get("node_id", "")),
        kind=str(payload.get("kind", "unknown")),
        runtime=str(payload.get("runtime", "")),
        domains=[str(item) for item in payload.get("domains", [])],
        ports=[int(item) for item in payload.get("ports", [])],
        deploy_path=str(payload.get("deploy_path", "")),
        config_paths=[str(item) for item in payload.get("config_paths", [])],
        env_paths=[str(item) for item in payload.get("env_paths", [])],
        data_paths=[str(item) for item in payload.get("data_paths", [])],
        health_check_url=str(payload.get("health_check_url", "")),
        monitor_enabled=bool(payload.get("monitor_enabled", False)),
        docs_path=str(payload.get("docs_path", "")),
        source=str(payload.get("source", "discover")),
        status=str(payload.get("status", "active")),
        metadata=dict(payload.get("metadata", {})),
    )


def _uptime_monitor_for_service(service: ServiceRecord) -> dict[str, object] | None:
    url = service.health_check_url
    if not url and service.domains:
        url = f"https://{service.domains[0]}"
    if not url or not service.monitor_enabled:
        return None
    return {
        "service_id": service.service_id,
        "name": service.name,
        "type": "http",
        "url": url,
        "tags": ["hmn", f"service:{service.service_id}"],
    }


@discover_app.callback(invoke_without_command=True)
def discover_services(
    ctx: typer.Context,
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    from_json: Path | None = typer.Option(None, "--from-json", help="从 JSON 清单导入服务资产（dry-run 发现入口）"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if from_json is None:
        raise typer.BadParameter("需要传入 --from-json；或使用 hmn discover services --inventory ...")
    payload = json.loads(from_json.read_text(encoding="utf-8"))
    services_payload = payload.get("services", []) if isinstance(payload, dict) else payload
    store = _store(db)
    count = 0
    for item in services_payload:
        store.save_service_record(_service_record_from_payload(item))
        count += 1
    typer.echo(f"discovered services: {count}")


@inspect_app.command("node")
def inspect_node(
    local: bool = typer.Option(False, "--local", help="盘点本机资产"),
    node: str | None = typer.Option(None, "--node", help="预留远程节点 ID；当前 MVP 不执行远程 SSH"),
    output: Path | None = typer.Option(None, "--output", help="写入 inventory JSON 文件"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    if node and not local:
        typer.echo("remote inspect is reserved for a future SSH/worker provider; use --local for this MVP")
        raise typer.Exit(2)
    if not local:
        raise typer.BadParameter("当前 MVP 需要显式传入 --local")
    inventory = collect_local_inventory(node=node or "local")
    rendered = inventory_to_json(inventory)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        if not json_output:
            typer.echo(str(output))
    if json_output or not output:
        typer.echo(rendered)


@discover_app.command("services")
def discover_services_command(
    inventory: Path = typer.Option(..., "--inventory", help="inspect node 生成的 inventory JSON"),
    output: Path | None = typer.Option(None, "--output", help="写入 service registry JSON 文件"),
    json_output: bool = typer.Option(False, "--json", help="输出 registry JSON"),
) -> None:
    registry = discover_services_from_file(inventory)
    if output:
        registry.save(output)
        if not json_output:
            typer.echo(str(output))
    rendered = json.dumps(registry.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if json_output or not output:
        typer.echo(rendered)



@uptime_app.command("plan")
def uptime_plan(
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    service_registry: Path | None = typer.Option(None, "--service-registry", help="service registry JSON 路径"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    if service_registry is not None:
        rendered = render_uptime_plan_json(service_registry)
        typer.echo(rendered)
        return
    monitors = [monitor for service in _store(db).list_service_records() if (monitor := _uptime_monitor_for_service(service))]
    payload = {"provider": "uptime-kuma", "dry_run": True, "monitors": monitors}
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(f"uptime kuma dry-run monitors: {len(monitors)}")
    for monitor in monitors:
        typer.echo(f"- {monitor['service_id']} {monitor['url']}")


@uptime_app.command("sync")
def uptime_sync(
    service_registry: Path | None = typer.Option(None, "--service-registry", help="service registry JSON 路径"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    """为 Uptime Kuma upsert 创建审批；审批前不写 provider。"""
    if service_registry is not None:
        plan = json.loads(render_uptime_plan_json(service_registry))
    else:
        monitors = [monitor for service in _store(db).list_service_records() if (monitor := _uptime_monitor_for_service(service))]
        plan = {"provider": "uptime-kuma", "dry_run": True, "monitors": monitors}
    store = _store(db)
    approval = store.create_approval_request(
        subject_type="uptime_sync",
        subject_id=str(service_registry or "hmn-db-services"),
        action="uptime.sync.apply",
        risk="high",
        requested_by="hmn",
        details={"plan": plan, "service_registry": str(service_registry) if service_registry else ""},
    )
    typer.echo(f"需要审批: {approval.approval_id}")
    typer.echo("未写入 Uptime Kuma；审批通过后才会 upsert monitor。")
    typer.echo(f"create={len(plan.get('create', plan.get('monitors', [])))} update={len(plan.get('update', []))} skip={len(plan.get('skip', []))}")
    raise typer.Exit(1)


@deploy_app.command("plan")
def deploy_plan(
    service_registry: Path = typer.Option(Path("service-registry.json"), "--service-registry", help="服务登记 JSON 路径"),
    provider_fixture_dir: Path | None = typer.Option(None, "--provider-fixture-dir", help="provider fixture 目录"),
    service_id: str | None = typer.Option(None, "--service-id", help="只输出指定服务"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    rendered = render_deploy_plan_json(
        service_registry,
        service_id=service_id,
        provider_fixture_dir=provider_fixture_dir,
    )
    if json_output:
        typer.echo(rendered)
        return
    payload = json.loads(rendered)
    typer.echo(f"deploy plan services: {payload['service_count']}")


@deploy_app.command("status")
def deploy_status(
    service_registry: Path = typer.Option(Path("service-registry.json"), "--service-registry", help="服务登记 JSON 路径"),
    provider_fixture_dir: Path | None = typer.Option(None, "--provider-fixture-dir", help="provider fixture 目录"),
    service_id: str | None = typer.Option(None, "--service-id", help="只输出指定服务"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    rendered = render_deploy_status_json(
        service_registry,
        service_id=service_id,
        provider_fixture_dir=provider_fixture_dir,
    )
    if json_output:
        typer.echo(rendered)
        return
    payload = json.loads(rendered)
    typer.echo(f"deploy status services: {payload['service_count']}")


def _format_service_summary(service: ServiceRecord) -> str:
    domains = ",".join(service.domains) if service.domains else "-"
    return f"{service.service_id}\t{service.name}\t{service.node_id or '-'}\t{service.kind}\t{domains}"


def _echo_service_detail(service: ServiceRecord) -> None:
    typer.echo(f"service: {service.service_id}")
    typer.echo(f"name: {service.name}")
    typer.echo(f"node: {service.node_id or '-'}")
    typer.echo(f"kind: {service.kind}")
    typer.echo(f"runtime: {service.runtime or '-'}")
    typer.echo(f"domains: {', '.join(service.domains) if service.domains else '-'}")
    typer.echo(f"ports: {', '.join(str(port) for port in service.ports) if service.ports else '-'}")
    typer.echo(f"deploy_path: {service.deploy_path or '-'}")
    typer.echo(f"health_check_url: {service.health_check_url or '-'}")
    typer.echo(f"monitor_enabled: {service.monitor_enabled}")


@service_app.command("add")
def service_add(
    service_id: str,
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    name: str = typer.Option(..., "--name", help="服务名称"),
    node_id: str = typer.Option("", "--node", help="所属节点 ID"),
    kind: str = typer.Option("unknown", "--kind", help="服务类型"),
    runtime: str = typer.Option("", "--runtime", help="运行方式"),
    domain: list[str] = typer.Option([], "--domain", help="绑定域名，可重复"),
    port: list[int] = typer.Option([], "--port", help="暴露端口，可重复"),
    deploy_path: str = typer.Option("", "--deploy-path", help="部署路径"),
    config_path: list[str] = typer.Option([], "--config-path", help="配置文件路径，可重复"),
    env_path: list[str] = typer.Option([], "--env-path", help="环境文件路径，可重复"),
    data_path: list[str] = typer.Option([], "--data-path", help="数据目录路径，可重复"),
    health_check_url: str = typer.Option("", "--health-check-url", help="健康检查 URL"),
    monitor_enabled: bool = typer.Option(False, "--monitor-enabled", help="纳入监控规划"),
) -> None:
    service = ServiceRecord(
        service_id=service_id,
        name=name,
        node_id=node_id,
        kind=kind,
        runtime=runtime,
        domains=list(domain),
        ports=list(port),
        deploy_path=deploy_path,
        config_paths=list(config_path),
        env_paths=list(env_path),
        data_paths=list(data_path),
        health_check_url=health_check_url,
        monitor_enabled=monitor_enabled,
    )
    _store(db).save_service_record(service)
    typer.echo(f"service saved: {service_id}")


@service_app.command("list")
def service_list(
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    node_id: str = typer.Option("", "--node", help="按节点过滤"),
) -> None:
    services = _store(db).list_service_records(node_id or None)
    for service in services:
        typer.echo(_format_service_summary(service))


@service_app.command("show")
def service_show(service_id: str, db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    service = _store(db).load_service_record(service_id)
    if service is None:
        typer.echo(f"service not found: {service_id}", err=True)
        raise typer.Exit(1)
    _echo_service_detail(service)


@node_app.command("list")
def list_nodes(
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    now: str | None = typer.Option(None, "--now", help="测试/排障用：指定当前时间 ISO8601"),
) -> None:
    store = _store(db)
    current_time = _parse_now(now)
    for node in store.list_nodes():
        liveness = _node_liveness(store, node.node_id, now=current_time) if node.status == "managed" else None
        suffix = f"\tliveness={liveness['state']}" if liveness else ""
        typer.echo(f"{node.node_id}\t{node.status}\t{node.hostname}\ttrust={node.trust_level}{suffix}")
        if liveness:
            _record_liveness_audit(store, node.node_id, liveness)


@node_app.command("confirm")
def confirm_node(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 pending 节点", show_default=False),
    bundle: list[str] = typer.Option(["observe"], "--bundle", "-b", help="授予的权限包，可重复填写"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    if node_id is None:
        pending_nodes = [node for node in store.list_nodes() if node.status == "pending"]
        if len(pending_nodes) == 1:
            node_id = pending_nodes[0].node_id
            typer.echo(f"自动选择 pending 节点: {node_id} ({pending_nodes[0].hostname})")
        elif not pending_nodes:
            typer.echo("没有 pending 节点可确认。")
            raise typer.Exit(1)
        else:
            typer.echo("有多个 pending 节点，请选择：")
            for index, node in enumerate(pending_nodes, start=1):
                typer.echo(f"{index}) {node.node_id}  {node.hostname}  trust={node.trust_level}")
            choice = typer.prompt("选择编号", default="1", type=int)
            if choice < 1 or choice > len(pending_nodes):
                typer.echo("无效选择。")
                raise typer.Exit(1)
            node_id = pending_nodes[choice - 1].node_id

    node = store.load_node(node_id)
    if node is None:
        raise typer.Exit(1)
    registry = NodeRegistry({node.node_id: node})
    updated = registry.confirm(node_id, permission_bundles=list(bundle))
    if updated is None:
        raise typer.Exit(1)
    store.save_node(updated)
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=updated.node_id,
        action="confirm",
        outcome="ok",
        details={"bundles": list(bundle)},
    )
    typer.echo(f"confirmed {updated.node_id}")


def _select_managed_node(store: SQLiteStore, node_id: str | None):
    if node_id is not None:
        node = store.load_node(node_id)
        if node is None:
            typer.echo("节点不存在。")
            raise typer.Exit(1)
        return node
    managed_nodes = [node for node in store.list_nodes() if node.status == "managed"]
    if len(managed_nodes) == 1:
        node = managed_nodes[0]
        typer.echo(f"自动选择 managed 节点: {node.node_id} ({node.hostname})")
        return node
    if not managed_nodes:
        typer.echo("没有 managed 节点。请先执行 hmn node confirm。")
        raise typer.Exit(1)
    typer.echo("有多个 managed 节点，请选择：")
    for index, node in enumerate(managed_nodes, start=1):
        typer.echo(f"{index}) {node.node_id}  {node.hostname}  trust={node.trust_level}")
    choice = typer.prompt("选择编号", default="1", type=int)
    if choice < 1 or choice > len(managed_nodes):
        typer.echo("无效选择。")
        raise typer.Exit(1)
    return managed_nodes[choice - 1]


def _format_ssh_reason_cn(reason: str, stderr: str = "") -> str:
    mapping = {
        "ssh_auth": "SSH 认证失败",
        "ssh_connectivity": "SSH 网络不通",
        "timeout": "SSH 检查超时",
        "remote_command": "远端命令执行失败",
        "none": "正常",
        "unknown": "未知",
    }
    label = mapping.get(reason or "unknown", "未知")
    detail = (stderr or "").strip()
    if detail and reason in {"ssh_auth", "ssh_connectivity", "timeout", "remote_command"}:
        return f"{label}：{detail}"
    return label


def _summarize_ssh_connectivity(ssh_connectivity: dict[str, object] | None) -> dict[str, str]:
    if not ssh_connectivity:
        return {"status": "unknown", "reason": "无记录", "summary": "-"}
    if ssh_connectivity.get("skipped"):
        return {"status": "skipped", "reason": "已跳过 SSH 检查", "summary": "skipped"}
    if not ssh_connectivity.get("configured"):
        detail = str(ssh_connectivity.get("stderr") or "未配置")
        return {"status": "warn", "reason": f"SSH 未配置：{detail}", "summary": f"warn {detail}"}
    summary = f"{ssh_connectivity.get('user') or '-'}@{ssh_connectivity.get('host') or '-'}:{ssh_connectivity.get('port') or '-'}"
    if ssh_connectivity.get("reachable"):
        return {"status": "ok", "reason": "SSH 连通正常", "summary": f"ok {summary}"}
    stderr = str(ssh_connectivity.get("stderr") or "")
    exit_code = int(ssh_connectivity.get("exit_code") or 1)
    reason = classify_ssh_failure(exit_code, stderr)
    return {
        "status": "warn",
        "reason": _format_ssh_reason_cn(reason, stderr),
        "summary": f"warn {summary} {stderr or f'exit_code={exit_code}'}".strip(),
    }


def _latest_doctor_ssh_connectivity(store: SQLiteStore, node_id: str) -> dict[str, object] | None:
    for event in reversed(store.list_audit_events()):
        if event.event_type == "node" and event.subject_id == node_id and event.action == "doctor":
            details = event.details.get("ssh_connectivity")
            if isinstance(details, dict):
                return details
    return None


def _format_last_ssh_check(ssh_connectivity: dict[str, object] | None) -> str:
    return _summarize_ssh_connectivity(ssh_connectivity)["summary"]


def _render_node_status(
    node,
    liveness: dict[str, object] | None = None,
    runtime: dict[str, object] | None = None,
    ssh_connectivity: dict[str, object] | None = None,
) -> None:
    typer.echo(f"node: {node.node_id}")
    typer.echo(f"status: {node.status}")
    typer.echo(f"host: {node.hostname}")
    typer.echo(f"trust: {node.trust_level}")
    typer.echo(f"labels: {', '.join(node.labels) if node.labels else '-'}")
    typer.echo(f"addresses: {', '.join(node.addresses) if node.addresses else '-'}")
    typer.echo(f"bundles: {', '.join(node.permission_bundles) if node.permission_bundles else '-'}")
    typer.echo(f"ssh_host: {node.ssh_host or '-'}")
    typer.echo(f"ssh_user: {node.ssh_user or '-'}")
    typer.echo(f"ssh_port: {node.ssh_port}")
    typer.echo(f"network_provider: {node.network_provider or '-'}")
    typer.echo(f"network_node_id: {node.network_node_id or '-'}")
    typer.echo(f"network_ip: {node.network_ip or '-'}")
    typer.echo(f"network_tags: {', '.join(node.network_tags) if node.network_tags else '-'}")
    typer.echo(f"network_online: {'yes' if node.network_online else ('no' if node.network_provider else '-')}")
    typer.echo(f"last_ssh_check: {_format_last_ssh_check(ssh_connectivity)}")
    typer.echo(f"ssh_reason: {_summarize_ssh_connectivity(ssh_connectivity)['reason']}")
    if liveness is not None:
        typer.echo(f"liveness: {liveness['state']}")
        typer.echo(f"last_heartbeat: {liveness['last_heartbeat'] or '-'}")
    if runtime is not None:
        typer.echo(f"runtime: {runtime['runtime_profile']}")
        typer.echo(f"service_manager: {runtime['service_manager']}")


@node_app.command("status")
def status_node(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    now: str | None = typer.Option(None, "--now", help="测试/排障用：指定当前时间 ISO8601"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    liveness = _node_liveness(store, node.node_id, now=_parse_now(now))
    event = _latest_heartbeat_event(store, node.node_id)
    facts = event.details.get("facts", {}) if event else {}
    runtime = _runtime_summary_from_facts(facts if isinstance(facts, dict) else {})
    ssh_connectivity = _latest_doctor_ssh_connectivity(store, node.node_id)
    _render_node_status(node, liveness, runtime, ssh_connectivity)
    _record_liveness_audit(store, node.node_id, liveness)


@node_app.command("doctor")
def doctor_node(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    no_ssh_check: bool = typer.Option(False, "--no-ssh-check", help="跳过 SSH 连通探测"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    checks = {
        "登记状态": node.status == "managed",
        "指纹": bool(node.fingerprint),
        "信任级别": node.trust_level in {"A", "B", "C"},
        "权限包": bool(node.permission_bundles),
    }
    ssh_connectivity = {
        "configured": False,
        "reachable": False,
        "host": "",
        "user": "",
        "port": node.ssh_port,
        "exit_code": None,
        "stderr": "",
        "skipped": no_ssh_check,
    }
    ssh_check_ok = False
    if no_ssh_check:
        ssh_check_ok = True
    elif node.status == "managed":
        try:
            target = ssh_target_details_for_node(node)
            ssh_connectivity.update(
                {
                    "configured": True,
                    "host": target.host,
                    "user": target.user,
                    "port": int(target.port),
                    "target_source": target.source,
                }
            )
            completed = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-p", target.port, f"{target.user}@{target.host}", "true"],
                check=False,
                text=True,
                capture_output=True,
                timeout=8,
            )
            ssh_connectivity.update(
                {
                    "reachable": completed.returncode == 0,
                    "exit_code": completed.returncode,
                    "stderr": (completed.stderr or "").strip(),
                }
            )
        except ValueError as exc:
            ssh_connectivity["stderr"] = str(exc)
        except subprocess.TimeoutExpired:
            ssh_connectivity.update({"configured": True, "exit_code": 124, "stderr": "ssh connectivity check timed out"})
        ssh_check_ok = ssh_connectivity["configured"] and ssh_connectivity["reachable"]
    checks["SSH 连通"] = ssh_check_ok
    outcome = "ok" if all(checks.values()) else "warn"
    typer.echo(f"doctor: {node.node_id}")
    typer.echo(f"host: {node.hostname}")
    typer.echo(f"ssh_host: {node.ssh_host or '-'}")
    typer.echo(f"ssh_user: {node.ssh_user or '-'}")
    typer.echo(f"ssh_port: {node.ssh_port}")
    typer.echo(f"network_provider: {node.network_provider or '-'}")
    typer.echo(f"network_ip: {node.network_ip or '-'}")
    typer.echo(f"network_online: {'yes' if node.network_online else ('no' if node.network_provider else '-')}")
    for name, ok in checks.items():
        typer.echo(f"{name}: {'OK' if ok else 'WARN'}")
    if no_ssh_check:
        typer.echo("SSH 连通: SKIPPED --no-ssh-check")
    elif ssh_connectivity["configured"]:
        summary = f"{ssh_connectivity['user']}@{ssh_connectivity['host']}:{ssh_connectivity['port']}"
        if ssh_connectivity["reachable"]:
            typer.echo(f"SSH 连通: OK {summary}")
        else:
            detail = ssh_connectivity["stderr"] or f"exit_code={ssh_connectivity['exit_code']}"
            typer.echo(f"SSH 连通: WARN {summary} {detail}")
    else:
        typer.echo(f"SSH 连通: WARN {ssh_connectivity['stderr'] or '未配置'}")
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=node.node_id,
        action="doctor",
        outcome=outcome,
        details={"checks": checks, "ssh_connectivity": ssh_connectivity},
    )
    if outcome != "ok":
        raise typer.Exit(1)


@node_app.command("heartbeat-command")
def heartbeat_command(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    master_url: str | None = typer.Option(None, "--master-url", help="主控 URL；默认自动读取 HMN_PUBLIC_URL 或安装配置"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    url = (master_url or _default_master_url()).rstrip("/")
    payload = json.dumps({"fingerprint": node.fingerprint, "status": "ok", "facts": {}}, ensure_ascii=False)
    typer.echo("请把下面命令放到节点定时任务里执行：")
    typer.echo(
        "curl -fsS -X POST "
        + _shell_quote(f"{url}/api/v1/nodes/{node.node_id}/heartbeat")
        + " -H 'Content-Type: application/json' --data "
        + _shell_quote(payload)
    )


def _latest_heartbeat_event(store: SQLiteStore, node_id: str):
    for event in reversed(store.list_audit_events()):
        if event.event_type == "node" and event.subject_id == node_id and event.action == "heartbeat":
            return event
    return None


def _runtime_summary_from_facts(facts: dict[str, object]) -> dict[str, object]:
    probe = probe_from_facts(facts)
    profile = classify_capabilities(probe)
    return {
        "runtime_profile": str(profile.runtime),
        "service_manager": str(profile.service_manager),
        "can_report_heartbeat": profile.can_report_heartbeat,
        "can_poll_tasks": profile.can_poll_tasks,
        "can_execute_tasks": profile.can_execute_tasks,
    }



def _backup_manifest_for_paths(paths: list[str], *, dry_run: bool = True) -> dict[str, object]:
    entries = []
    total_bytes = 0
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_file():
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            size = len(data)
            total_bytes += size
            entries.append({
                "path": str(path),
                "type": "file",
                "size_bytes": size,
                "sha256": digest,
            })
        elif path.is_dir():
            count = 0
            size_sum = 0
            digest = hashlib.sha256()
            for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.is_symlink()):
                rel = child.relative_to(path).as_posix()
                data = child.read_bytes()
                file_hash = hashlib.sha256(data).hexdigest()
                digest.update(rel.encode("utf-8") + b"\0" + file_hash.encode("ascii") + b"\0")
                count += 1
                size_sum += len(data)
            total_bytes += size_sum
            entries.append({
                "path": str(path),
                "type": "directory",
                "file_count": count,
                "size_bytes": size_sum,
                "tree_sha256": digest.hexdigest(),
            })
        else:
            entries.append({"path": str(path), "type": "missing", "size_bytes": 0, "sha256": ""})
    manifest = {
        "schema": "hmn.backup.manifest.v1",
        "dry_run": dry_run,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
        "total_bytes": total_bytes,
        "restore_enabled": False,
    }
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    return manifest


def _backup_archive_paths(output_dir: Path, node_id: str) -> tuple[Path, Path]:
    stamp = int(time.time())
    return (
        output_dir / f"hmn-backup-{node_id}-{stamp}.tar.gz",
        output_dir / f"hmn-backup-{node_id}-{stamp}.manifest.json",
    )


def _record_backup_verify_result(
    store: SQLiteStore,
    *,
    node_id: str,
    result: dict[str, object],
    ok: bool,
):
    component = load_builtin_components()["backup"]
    status = "ok" if ok else "failed"
    recorded = store.record_component_run(
        component_id="backup",
        node_id=node_id,
        action="backup.verify",
        risk=component.risk,
        status=status,
        result=result,
    )
    store.record_audit(
        event_type="component.backup",
        subject_type="component",
        subject_id="backup",
        action="verify",
        outcome=status,
        details={"node_id": node_id, "run_id": recorded.run_id, **result},
    )
    return recorded


def _archive_backup_paths(paths: list[str], archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz") as tar:
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.exists():
                continue
            arcname = path.name or "backup-item"
            tar.add(path, arcname=arcname, recursive=True, filter=lambda info: None if info.issym() or info.islnk() else info)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _backup_plan_payload(node_id: str, include: list[str], output_dir: Path, driver: str = "local-archive") -> dict[str, object]:
    return {
        "component_id": "backup",
        "node_id": node_id,
        "action": "backup.dry_run",
        "driver": driver,
        "dry_run": True,
        "include": include,
        "output_dir": str(output_dir),
        "restore_enabled": False,
        "next_action": "hmn backup run",
    }

def _latest_backup_run(store: SQLiteStore, node_id: str | None = None):
    for run in store.list_component_runs():
        if run.component_id == "backup" and run.action in {"backup.plan", "backup.run"}:
            if node_id is None or run.node_id == node_id:
                return run
    return None

def _monitor_summary(store: SQLiteStore, node_id: str) -> dict[str, object]:
    event = _latest_heartbeat_event(store, node_id)
    facts = event.details.get("facts", {}) if event else {}
    if not isinstance(facts, dict):
        facts = {}
    compatible = bool(event.details.get("worker_compatible", True)) if event else False
    heartbeat_ok = event is not None and event.outcome == "ok" and compatible
    exec_enabled = bool(facts.get("exec_enabled", False))
    runtime = _runtime_summary_from_facts(facts)
    return {
        "heartbeat_seen": event is not None,
        "heartbeat_ok": heartbeat_ok,
        "heartbeat_outcome": event.outcome if event else "missing",
        "heartbeat_at": event.created_at.isoformat() if event else "",
        "worker_protocol_version": facts.get("worker_protocol_version") or "unknown",
        "worker_version": facts.get("worker_version") or "unknown",
        "worker_compatible": compatible,
        "exec_enabled": exec_enabled,
        "exec_mode": "ENABLED" if exec_enabled else "SAFE",
        "facts": facts,
        **runtime,
    }


def _facts_summary(facts: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    load = facts.get("load_average")
    if isinstance(load, dict):
        if load.get("1m") is not None:
            summary["load_1m"] = str(load.get("1m"))
        if load.get("5m") is not None:
            summary["load_5m"] = str(load.get("5m"))
        if load.get("15m") is not None:
            summary["load_15m"] = str(load.get("15m"))
    memory = facts.get("memory")
    if isinstance(memory, dict):
        total = memory.get("total_kb")
        available = memory.get("available_kb")
        if isinstance(total, (int, float)) and total > 0 and isinstance(available, (int, float)):
            summary["memory_used_percent"] = int(round(((total - available) / total) * 100))
    disk = facts.get("disk")
    if isinstance(disk, dict):
        total = disk.get("total_bytes")
        used = disk.get("used_bytes")
        if isinstance(total, (int, float)) and total > 0 and isinstance(used, (int, float)):
            summary["disk_used_percent"] = int(round((used / total) * 100))
        if disk.get("path"):
            summary["disk_path"] = str(disk.get("path"))
    uptime = facts.get("uptime")
    if isinstance(uptime, dict) and uptime.get("seconds") is not None:
        summary["uptime_seconds"] = uptime.get("seconds")
    return summary


def _monitor_health_summary(store: SQLiteStore, node_id: str, *, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    summary = _monitor_summary(store, node_id)
    event = _latest_heartbeat_event(store, node_id)
    age_seconds = int((now - event.created_at).total_seconds()) if event else None
    if event is None:
        health = "critical"
        reason = "missing_heartbeat"
    elif not summary["worker_compatible"]:
        health = "critical"
        reason = "worker_protocol_incompatible"
    elif event.outcome != "ok":
        health = "warn"
        reason = "heartbeat_warn"
    elif age_seconds is not None and age_seconds > 900:
        health = "critical"
        reason = "heartbeat_timeout"
    elif age_seconds is not None and age_seconds > 300:
        health = "warn"
        reason = "stale_heartbeat"
    else:
        health = "ok"
        reason = "fresh_heartbeat"
    facts = summary.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}
    return {
        **summary,
        "health": health,
        "reason": reason,
        "age_seconds": age_seconds,
        "heartbeat_datetime": event.created_at if event else None,
        "facts_summary": _facts_summary(facts),
    }


def _record_monitor_snapshot(store: SQLiteStore, node_id: str, summary: dict[str, object]):
    snapshot = store.record_monitor_snapshot(
        node_id=node_id,
        health=str(summary["health"]),
        reason=str(summary["reason"]),
        heartbeat_at=summary.get("heartbeat_datetime") if isinstance(summary.get("heartbeat_datetime"), datetime) else None,
        age_seconds=summary.get("age_seconds") if isinstance(summary.get("age_seconds"), int) else None,
        runtime_profile=str(summary.get("runtime_profile") or "unknown"),
        service_manager=str(summary.get("service_manager") or "unknown"),
        worker_protocol_version=str(summary.get("worker_protocol_version") or "unknown"),
        worker_version=str(summary.get("worker_version") or "unknown"),
        exec_mode=str(summary.get("exec_mode") or "SAFE"),
        facts_summary=summary.get("facts_summary") if isinstance(summary.get("facts_summary"), dict) else {},
    )
    store.record_audit(
        event_type="monitor",
        subject_type="node",
        subject_id=node_id,
        action="run-once",
        outcome=snapshot.health,
        details={
            "snapshot_id": snapshot.snapshot_id,
            "node_id": node_id,
            "health": snapshot.health,
            "reason": snapshot.reason,
            "heartbeat_at": snapshot.heartbeat_at.isoformat() if snapshot.heartbeat_at else "",
            "age_seconds": snapshot.age_seconds,
            "runtime_profile": snapshot.runtime_profile,
            "service_manager": snapshot.service_manager,
            "worker_protocol_version": snapshot.worker_protocol_version,
            "worker_version": snapshot.worker_version,
            "exec_mode": snapshot.exec_mode,
            "facts_summary": snapshot.facts_summary,
        },
    )
    return snapshot


def _monitor_counts(snapshots) -> dict[str, int]:
    return {
        "ok": sum(1 for item in snapshots if item.health == "ok"),
        "warn": sum(1 for item in snapshots if item.health == "warn"),
        "critical": sum(1 for item in snapshots if item.health == "critical"),
    }


def _echo_monitor_summary(summary: dict[str, object]) -> None:
    if summary["heartbeat_seen"]:
        typer.echo(
            f"heartbeat={'OK' if summary['heartbeat_ok'] else 'WARN'} "
            f"at={summary['heartbeat_at']}"
        )
    else:
        typer.echo("heartbeat=WARN missing")
    typer.echo(f"worker_protocol={summary['worker_protocol_version']}")
    typer.echo(f"worker_version={summary['worker_version']}")
    typer.echo(f"worker_compatible={'yes' if summary['worker_compatible'] else 'no'}")
    typer.echo(f"runtime: {summary['runtime_profile']}")
    typer.echo(f"service_manager: {summary['service_manager']}")
    typer.echo(f"exec={summary['exec_mode']}")
    facts = summary.get("facts", {})
    if not isinstance(facts, dict):
        return
    uptime = facts.get("uptime")
    if isinstance(uptime, dict) and uptime.get("seconds") is not None:
        typer.echo(f"uptime_seconds={uptime['seconds']}")
    load = facts.get("load_average")
    if isinstance(load, dict) and any(load.get(key) for key in ("1m", "5m", "15m")):
        typer.echo(f"load_average={load.get('1m', '?')}/{load.get('5m', '?')}/{load.get('15m', '?')}")
    memory = facts.get("memory")
    if isinstance(memory, dict) and any(memory.get(key) is not None for key in ("total_kb", "available_kb", "free_kb")):
        typer.echo(
            "memory_kb "
            f"total={memory.get('total_kb')} "
            f"available={memory.get('available_kb')} "
            f"free={memory.get('free_kb')}"
        )
    disk = facts.get("disk")
    if isinstance(disk, dict) and any(disk.get(key) is not None for key in ("total_bytes", "used_bytes", "free_bytes")):
        typer.echo(
            f"disk {disk.get('path', '/')} "
            f"total={disk.get('total_bytes')} "
            f"used={disk.get('used_bytes')} "
            f"free={disk.get('free_bytes')}"
        )


@monitor_app.command("run-once")
def monitor_run_once(
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
    now: str | None = typer.Option(None, "--now", help="测试/排障用：指定当前时间 ISO8601"),
) -> None:
    store = _store(db)
    current_time = _parse_now(now)
    nodes = [node for node in store.list_nodes() if node.status == "managed"]
    snapshots = []
    for node in nodes:
        summary = _monitor_health_summary(store, node.node_id, now=current_time)
        snapshot = _record_monitor_snapshot(store, node.node_id, summary)
        snapshots.append(snapshot)
    counts = _monitor_counts(snapshots)
    typer.echo(f"monitor run: nodes={len(snapshots)} ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}")
    for snapshot in snapshots:
        typer.echo(f"{snapshot.node_id}\t{snapshot.health}\t{snapshot.reason}")
    if counts["warn"] or counts["critical"]:
        raise typer.Exit(1)


@monitor_app.command("status")
def monitor_status(
    node_id: str = typer.Option(..., "--node", help="节点 ID"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    snapshot = store.latest_monitor_snapshot(node_id)
    if snapshot is None:
        node = store.load_node(node_id)
        if node is None:
            typer.echo("节点不存在。")
            raise typer.Exit(1)
        snapshot = _record_monitor_snapshot(store, node_id, _monitor_health_summary(store, node_id))
    typer.echo(f"node: {snapshot.node_id}")
    typer.echo(f"health: {snapshot.health}")
    typer.echo(f"reason: {snapshot.reason}")
    typer.echo(f"heartbeat_at: {snapshot.heartbeat_at.isoformat() if snapshot.heartbeat_at else '-'}")
    typer.echo(f"age_seconds: {snapshot.age_seconds if snapshot.age_seconds is not None else '-'}")
    typer.echo(f"runtime: {snapshot.runtime_profile}")
    typer.echo(f"service_manager: {snapshot.service_manager}")
    typer.echo(f"worker_protocol: {snapshot.worker_protocol_version}")
    typer.echo(f"worker_version: {snapshot.worker_version}")
    typer.echo(f"exec: {snapshot.exec_mode}")
    for key in ("disk_used_percent", "memory_used_percent", "load_1m", "uptime_seconds"):
        if key in snapshot.facts_summary:
            typer.echo(f"{key}: {snapshot.facts_summary[key]}")
    if snapshot.health == "critical":
        raise typer.Exit(1)


@monitor_app.command("report")
def monitor_report(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    store = _store(db)
    latest = []
    seen: set[str] = set()
    for snapshot in store.list_monitor_snapshots():
        if snapshot.node_id in seen:
            continue
        seen.add(snapshot.node_id)
        latest.append(snapshot)
    counts = _monitor_counts(latest)
    typer.echo(f"monitor report: nodes={len(latest)} ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}")
    for snapshot in sorted(latest, key=lambda item: item.node_id):
        parts = [f"{snapshot.node_id} {snapshot.health}", f"reason={snapshot.reason}"]
        if "disk_used_percent" in snapshot.facts_summary:
            parts.append(f"disk={snapshot.facts_summary['disk_used_percent']}%")
        if "memory_used_percent" in snapshot.facts_summary:
            parts.append(f"mem={snapshot.facts_summary['memory_used_percent']}%")
        if "load_1m" in snapshot.facts_summary:
            parts.append(f"load={snapshot.facts_summary['load_1m']}")
        typer.echo(" ".join(parts))
    if counts["critical"]:
        raise typer.Exit(1)


@node_app.command("worker-status")
def worker_status(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    event = _latest_heartbeat_event(store, node.node_id)
    facts = event.details.get("facts", {}) if event else {}
    if not isinstance(facts, dict):
        facts = {}
    compatible = bool(event.details.get("worker_compatible", True)) if event else False
    protocol = facts.get("worker_protocol_version") or "unknown"
    version_value = facts.get("worker_version") or "unknown"
    exec_enabled = bool(facts.get("exec_enabled", False))
    runtime = _runtime_summary_from_facts(facts)
    ssh_connectivity = _latest_doctor_ssh_connectivity(store, node.node_id)
    ssh_summary = _summarize_ssh_connectivity(ssh_connectivity)

    liveness = _node_liveness(store, node.node_id)
    typer.echo(f"worker: {node.node_id}")
    typer.echo(f"host: {node.hostname}")
    typer.echo(f"liveness: {liveness['state']}")
    if event is None:
        typer.echo("心跳: WARN 未收到")
        typer.echo("worker: WARN 未安装或未上报")
    else:
        typer.echo(f"心跳: {'OK' if event.outcome == 'ok' else 'WARN'} {event.created_at.isoformat()}")
        typer.echo("worker: OK installed/reported")
    if compatible:
        typer.echo(f"协议: OK {protocol}")
    else:
        typer.echo(f"协议: WARN {protocol} incompatible")
    typer.echo(f"版本: {version_value}")
    typer.echo(f"runtime: {runtime['runtime_profile']}")
    typer.echo(f"service_manager: {runtime['service_manager']}")
    typer.echo(f"ssh: {ssh_summary['summary']}")
    typer.echo(f"ssh_reason: {ssh_summary['reason']}")
    if exec_enabled:
        typer.echo("执行: ENABLED HMN_ENABLE_EXEC=1")
    else:
        typer.echo("执行: SAFE HMN_ENABLE_EXEC=0")
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=node.node_id,
        action="worker-status",
        outcome="ok" if event is not None and event.outcome == "ok" and compatible else "warn",
        details={
            "heartbeat_seen": event is not None,
            "worker_protocol_version": protocol,
            "worker_compatible": compatible,
            "exec_enabled": exec_enabled,
            "liveness": liveness,
            "ssh_connectivity": ssh_connectivity or {},
            "ssh_reason": ssh_summary["reason"],
            **runtime,
        },
    )
    _record_liveness_audit(store, node.node_id, liveness)
    if event is None or event.outcome != "ok" or not compatible:
        raise typer.Exit(1)


def _render_worker_installer(
    node,
    master_url: str,
    service_manager: ServiceManager = ServiceManager.SYSTEMD,
    beacon_only: bool = False,
    runtime: NodeRuntimeProfile = NodeRuntimeProfile.FULL_WORKER,
    endpoints: list[str] | None = None,
) -> str:
    url = master_url.rstrip("/")
    endpoint_values = [item.rstrip("/") for item in (endpoints or []) if item.strip()]
    if not endpoint_values:
        endpoint_values = [url]
    service_wiring = render_service_manager_installer(service_manager)
    beacon_env = "HMN_WORKER_MODE=beacon\nHMN_BEACON_ONLY=1" if beacon_only else ""
    worker_asset = "worker-lite.sh" if runtime == NodeRuntimeProfile.LITE_WORKER else "worker.sh"
    shell = "sh" if runtime == NodeRuntimeProfile.LITE_WORKER else "bash"
    endpoint_csv = ",".join(endpoint_values)
    script = f"""set -eu
install -d -m 0700 /etc/hermes-managed-network
cat >/etc/hermes-managed-network/node.env <<'EOF'
HERMES_MASTER_URL={url}
HMN_MASTER_URLS={endpoint_csv}
HERMES_NODE_ID={node.node_id}
HERMES_NODE_FINGERPRINT={node.fingerprint}
HMN_ENABLE_EXEC=0
{beacon_env}
EOF
chmod 0600 /etc/hermes-managed-network/node.env
curl -fsSL {url}/scripts/{worker_asset} -o /usr/local/bin/hmn-worker
chmod 0755 /usr/local/bin/hmn-worker
{service_wiring}"""
    return f"sudo {shell} -c " + _shell_quote(script)


@node_app.command("rotate-fingerprint")
def rotate_fingerprint_command(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    new_fingerprint: str | None = typer.Option(None, "--new-fingerprint", help="新节点指纹；省略时自动生成 sha256 指纹"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    generated = new_fingerprint or "sha256:" + hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    updated = store.rotate_node_fingerprint(
        node.node_id,
        current_fingerprint=None,
        new_fingerprint=generated,
    )
    if updated is None:
        raise typer.Exit(1)
    typer.echo(f"fingerprint rotated: {updated.node_id}")
    typer.echo("请在目标节点 /etc/hermes-managed-network/node.env 同步更新：")
    typer.echo(f"HERMES_NODE_FINGERPRINT={updated.fingerprint}")


@node_app.command("install-heartbeat")
def install_heartbeat(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    master_url: str | None = typer.Option(None, "--master-url", help="主控 URL；默认自动读取 HMN_PUBLIC_URL 或安装配置"),
    endpoint: list[str] = typer.Option([], "--endpoint", help="备用主控 endpoint，可重复填写；用于 IPv6/Headscale/relay fallback"),
    runtime: NodeRuntimeProfile = typer.Option(NodeRuntimeProfile.FULL_WORKER, "--runtime", help="worker 运行时：full-worker/lite-worker/beacon-only"),
    service_manager: ServiceManager = typer.Option(ServiceManager.SYSTEMD, "--service-manager", help="服务管理器适配器"),
    beacon_only: bool = typer.Option(False, "--beacon-only", help="安装 beacon-only 模式：只心跳，不 poll tasks，不执行命令"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    url = (master_url or _default_master_url()).rstrip("/")
    runtime_value = NodeRuntimeProfile.BEACON_ONLY if beacon_only else runtime
    if not isinstance(endpoint, (list, tuple)):
        endpoint = []
    endpoints = [item.rstrip("/") for item in endpoint if item.strip()]
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=node.node_id,
        action="install-heartbeat",
        outcome="rendered",
        details={
            "master_url": url,
            "service_manager": str(service_manager),
            "enable_exec": False,
            "runtime": str(runtime_value),
            "endpoints": endpoints or [url],
        },
    )
    typer.echo("请复制下面命令到目标节点执行，它会安装心跳/worker 定时器：")
    if beacon_only:
        typer.echo("beacon-only 模式：只 heartbeat，不会 poll tasks，不会执行命令。")
    else:
        typer.echo("默认安全模式：HMN_ENABLE_EXEC=0，不会执行下发 shell 命令。")
    typer.echo(f"runtime={runtime_value}")
    typer.echo(f"service_manager={service_manager}")
    if endpoints:
        typer.echo("endpoint fallback: " + " -> ".join(endpoints))
    typer.echo(_render_worker_installer(node, url, service_manager, beacon_only=beacon_only, runtime=runtime_value, endpoints=endpoints))


@node_app.command("revoke")
def revoke_node(
    node_id: str = typer.Argument(...),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = store.load_node(node_id)
    if node is None:
        raise typer.Exit(1)
    registry = NodeRegistry({node.node_id: node})
    updated = registry.revoke(node_id)
    store.save_node(updated)
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=updated.node_id,
        action="revoke",
        outcome="ok",
        details={},
    )
    typer.echo(f"revoked {updated.node_id}")


@playbook_app.command("run")
def run_playbook(
    file: Path = typer.Argument(..., exists=True, dir_okay=False),
    message: str = typer.Option(..., "--message", help="传给 playbook 的输入消息"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="只演练，不实际执行 shell 命令"),
) -> None:
    playbook = Playbook.load(file)
    run = PlaybookExecutor(dry_run=dry_run).run(playbook, values={"message": message})
    for result in run.results:
        typer.echo(f"{result.phase}: {result.command}")


@task_app.command("run")
def create_task_command(
    command: str = typer.Argument(..., help="要在节点上执行的 low/medium shell 命令"),
    node_id: str | None = typer.Option(None, "--node", help="节点 ID；省略时自动选择唯一 managed 节点"),
    risk: str = typer.Option("low", "--risk", help="风险级别；允许 low/medium/high/critical"),
    executor: str = typer.Option("worker", "--executor", help="执行器：worker 或 ssh"),
    wait: bool = typer.Option(False, "--wait", help="仅对 SSH 任务：创建后立即执行并等待结果"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    if executor not in {"worker", "ssh"}:
        typer.echo("执行器只允许 worker 或 ssh。")
        raise typer.Exit(1)
    if wait and executor != "ssh":
        typer.echo("--wait 目前只支持 --executor ssh。")
        raise typer.Exit(1)
    if risk in {"high", "critical"}:
        approval = store.create_approval_request(
            subject_type="task",
            subject_id="pending-task",
            action="task.run",
            risk=risk,
            requested_by="hmn",
            details={"node_id": node.node_id, "command": command, "risk": risk, "executor": executor, "created_by": "hmn"},
        )
        card = build_approval_card(approval)
        store.enqueue_notification(
            channel="telegram",
            subject_type="approval",
            subject_id=approval.approval_id,
            payload={"text": card.text, "buttons": card.buttons},
        )
        typer.echo(f"需要审批: {approval.approval_id}")
        typer.echo("已加入 approval gateway outbox: telegram")
        typer.echo(f"节点: {node.node_id}")
        typer.echo(f"命令: {command}")
        raise typer.Exit(1)
    if risk not in {"low", "medium"}:
        typer.echo("风险级别只允许 low/medium/high/critical。")
        raise typer.Exit(1)
    task = store.create_task(node_id=node.node_id, command=command, risk=risk, created_by="hmn", executor=executor)
    if wait:
        try:
            task = run_ssh_task(store, task.task_id)
        except SSHExecutionError as exc:
            typer.echo(f"SSH 执行失败: {exc}")
            raise typer.Exit(1)
        typer.echo(f"已通过 SSH 执行: {task.task_id}")
        typer.echo(f"节点: {node.node_id}")
        typer.echo(f"命令: {command}")
        typer.echo(f"退出码: {task.exit_code}")
        return
    typer.echo(f"已创建任务: {task.task_id}")
    typer.echo(f"节点: {node.node_id}")
    typer.echo(f"命令: {command}")


@task_app.command("list")
def list_task_commands(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    for task in _store(db).list_tasks():
        typer.echo(f"{task.task_id}\t{task.node_id}\t{task.status}\texecutor={task.executor}\trisk={task.risk}\t{task.command}")


@task_app.command("ssh-run-next")
def ssh_run_next(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    store = _store(db)
    candidates = [task for task in store.list_tasks() if task.executor == "ssh" and task.status == "pending"]
    if not candidates:
        store.record_audit(
            event_type="task",
            subject_type="task",
            subject_id="ssh-run-next",
            action="ssh-run-next",
            outcome="empty",
            details={"pending_ssh_tasks": 0},
        )
        typer.echo("没有待执行的 SSH 任务。")
        raise typer.Exit(1)
    task = sorted(candidates, key=lambda item: item.created_at)[0]
    try:
        completed = run_ssh_task(store, task.task_id, allow_risk={"low", "medium", "high", "critical"})
    except SSHExecutionError as exc:
        store.record_audit(
            event_type="task",
            subject_type="task",
            subject_id=task.task_id,
            action="ssh-run-next",
            outcome="failed",
            details={
                "task_id": task.task_id,
                "node_id": task.node_id,
                "status": "failed",
                "exit_code": exc.exit_code,
                "duration_ms": exc.duration_ms,
                "stdout_preview": (exc.stdout or "").strip()[:240].rstrip("\n"),
                "stderr_preview": (exc.stderr or "").strip()[:240].rstrip("\n"),
                "failure_reason": exc.failure_reason,
            },
        )
        typer.echo(f"SSH 执行失败: {exc}")
        raise typer.Exit(1)
    store.record_audit(
        event_type="task",
        subject_type="task",
        subject_id=completed.task_id,
        action="ssh-run-next",
        outcome="ok",
        details={
            "task_id": completed.task_id,
            "node_id": completed.node_id,
            "status": completed.status,
            "exit_code": completed.exit_code,
            "duration_ms": max(0, int(((completed.completed_at or completed.created_at) - (completed.started_at or completed.created_at)).total_seconds() * 1000)),
            "stdout_preview": (completed.stdout or "").strip()[:240].rstrip("\n"),
            "stderr_preview": (completed.stderr or "").strip()[:240].rstrip("\n"),
            "failure_reason": "none",
        },
    )
    typer.echo(f"已执行任务: {completed.task_id}")
    typer.echo(f"节点: {completed.node_id}")
    typer.echo(f"退出码: {completed.exit_code}")


def _orchestrator_service(db: Path | None) -> OrchestratorService:
    return OrchestratorService(_store(db))


def _fmt_orch_item(item: dict, *keys: str) -> str:
    values = [str(item.get(key, "")) for key in keys if item.get(key, "") not in {None, ""}]
    return " | ".join(values) if values else "-"


@orchestrator_app.command("enqueue")
def orchestrator_enqueue(
    title: str = typer.Option(..., "--title", help="任务标题"),
    scope: str = typer.Option("", "--scope", help="任务范围"),
    risk: str = typer.Option("low", "--risk", help="风险级别：low/medium/high/critical"),
    priority: int = typer.Option(5, "--priority", help="优先级，数字越大越先做"),
    worker_hint: str = typer.Option("", "--worker", help="建议 worker，可留空"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    if risk not in {"low", "medium", "high", "critical"}:
        typer.echo("风险级别只允许 low/medium/high/critical。")
        raise typer.Exit(1)
    task_id = _orchestrator_service(db).enqueue(
        title=title,
        scope=scope,
        risk=risk,
        priority=priority,
        worker_hint=worker_hint,
        source="cli",
    )
    typer.echo(f"已入队: {task_id}")


@orchestrator_worker_app.command("register")
def orchestrator_worker_register(
    worker_id: str = typer.Option(..., "--id", help="worker ID"),
    transport: str = typer.Option("bridge", "--transport", help="分发通道：bridge/webhook/queue/ssh"),
    status: str = typer.Option("online", "--status", help="worker 状态"),
    labels: list[str] = typer.Option([], "--label", help="worker 标签，可重复"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    _orchestrator_service(db).register_worker(
        worker_id=worker_id,
        transport=transport,
        status=status,
        labels=list(labels),
    )
    typer.echo(f"worker 已登记: {worker_id} | {status} | {transport}")


@orchestrator_worker_app.command("update")
def orchestrator_worker_update(
    worker_id: str = typer.Option(..., "--id", help="worker ID"),
    status: str = typer.Option(..., "--status", help="worker 状态"),
    transport: str | None = typer.Option(None, "--transport", help="可选：更新分发通道"),
    labels: list[str] | None = typer.Option(None, "--label", help="可选：覆盖 worker 标签，可重复"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    _orchestrator_service(db).update_worker(
        worker_id=worker_id,
        status=status,
        transport=transport,
        labels=list(labels) if labels is not None else None,
    )
    typer.echo(f"worker 已更新: {worker_id} | {status}")


@orchestrator_app.command("status")
def orchestrator_status(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    snapshot = _orchestrator_service(db).snapshot()
    typer.echo("队列:")
    for item in snapshot.get("queue", []):
        typer.echo("  " + _fmt_orch_item(item, "task_id", "status", "title", "priority"))
    if not snapshot.get("queue"):
        typer.echo("  空")
    typer.echo("worker:")
    for item in snapshot.get("workers", []):
        line = _fmt_orch_item(item, "worker_id", "status", "transport")
        labels = item.get("labels") or []
        if labels:
            line += " | labels=" + ",".join(str(label) for label in labels)
        typer.echo("  " + line)
    if not snapshot.get("workers"):
        typer.echo("  空")
    typer.echo("assignment:")
    for item in snapshot.get("assignments", []):
        typer.echo("  " + _fmt_orch_item(item, "task_id", "worker_id", "status"))
    if not snapshot.get("assignments"):
        typer.echo("  空")
    typer.echo("最近 report:")
    for item in snapshot.get("reports", [])[:5]:
        typer.echo("  " + _fmt_orch_item(item, "task_id", "status", "summary"))
    if not snapshot.get("reports"):
        typer.echo("  暂无")


@orchestrator_app.command("tick")
def orchestrator_tick(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    result = _orchestrator_service(db).tick()
    typer.echo("已分发:")
    for item in result.get("dispatched", []):
        typer.echo(f"  {item.get('task_id', '-')} -> {item.get('worker_id', '-')}")
    if not result.get("dispatched"):
        typer.echo("  无")
    typer.echo("阻塞:")
    for item in result.get("blocked", []):
        typer.echo("  " + _fmt_orch_item(item, "task_id", "reason"))
    if not result.get("blocked"):
        typer.echo("  无")
    typer.echo(f"下一步：{result.get('next', '暂无')}")


@orchestrator_app.command("report")
def orchestrator_report(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    typer.echo(_orchestrator_service(db).report())


@approval_app.command("list")
def list_approvals(
    status: str | None = typer.Option(None, "--status", help="按状态过滤：pending/approved/rejected"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    for approval in _store(db).list_approval_requests(status=status):
        typer.echo(
            f"{approval.approval_id}\t{approval.status}\trisk={approval.risk}\t{approval.action}\t{approval.subject_type}:{approval.subject_id}"
        )


@approval_app.command("show")
def show_approval(
    approval_id: str = typer.Argument(...),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    approval = _store(db).load_approval_request(approval_id)
    if approval is None:
        raise typer.Exit(1)
    typer.echo(f"approval: {approval.approval_id}")
    typer.echo(f"status: {approval.status}")
    typer.echo(f"risk: {approval.risk}")
    typer.echo(f"action: {approval.action}")
    typer.echo(f"subject: {approval.subject_type}:{approval.subject_id}")
    typer.echo(f"requested_by: {approval.requested_by}")
    if approval.decided_by:
        typer.echo(f"decided_by: {approval.decided_by}")
    typer.echo("details:")
    typer.echo(json.dumps(approval.details, ensure_ascii=False, sort_keys=True))


@approval_app.command("approve")
def approve_approval(
    approval_id: str = typer.Argument(...),
    by: str = typer.Option("operator", "--by", help="审批人"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    approval = store.resolve_approval_request(approval_id, status="approved", decided_by=by)
    if approval is None:
        raise typer.Exit(1)
    typer.echo(f"approved: {approval.approval_id}")
    if approval.subject_type == "task" and approval.action == "task.run":
        task = store.dispatch_approved_task_request(approval.approval_id)
        if task is None:
            typer.echo("未创建任务：审批详情缺少 node_id/command，或审批类型不可调度。")
            raise typer.Exit(1)
        typer.echo(f"已创建任务: {task.task_id}")
        typer.echo(f"节点: {task.node_id}")
        typer.echo(f"命令: {task.command}")
    if approval.subject_type == "network_node" and approval.action == "network.tags.set":
        try:
            dispatched = _dispatch_approved_network_tag_update(store, approval.approval_id)
        except NetworkProviderError as exc:
            typer.echo(f"网络 tags 更新失败: {exc}")
            raise typer.Exit(1) from exc
        if not dispatched:
            typer.echo("未更新网络 tags：审批详情不完整或节点不可用。")
            raise typer.Exit(1)
        typer.echo("已更新网络 tags")
    if approval.subject_type == "network_acl" and approval.action == "network.acl.apply":
        try:
            dispatched = dispatch_approved_network_acl_apply(store, approval.approval_id)
        except NetworkProviderError as exc:
            typer.echo(f"Headscale ACL 应用失败: {exc}")
            raise typer.Exit(1) from exc
        if not dispatched:
            typer.echo("未应用 Headscale ACL：审批详情不完整或文件不可用。")
            raise typer.Exit(1)
        typer.echo("已应用 Headscale ACL")
    if approval.subject_type == "component_run" and approval.action.startswith("component."):
        run = store.dispatch_approved_component_action(approval.approval_id)
        if run is None:
            typer.echo("未执行组件操作：审批详情不完整或组件动作不可调度。")
            raise typer.Exit(1)
        typer.echo(f"已执行组件操作: {run.run_id}")
        typer.echo(f"组件: {run.component_id}")
        typer.echo(f"节点: {run.node_id}")


@approval_app.command("reject")
def reject_approval(
    approval_id: str = typer.Argument(...),
    by: str = typer.Option("operator", "--by", help="审批人"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    approval = _store(db).resolve_approval_request(approval_id, status="rejected", decided_by=by)
    if approval is None:
        raise typer.Exit(1)
    typer.echo(f"rejected: {approval.approval_id}")


def _approval_gateway_settings(
    client: str,
    api_url: str | None,
    target: str | None,
    token: str | None,
) -> tuple[str, ApprovalGatewayClientConfig, str | None]:
    resolved_client = client.lower()
    resolved_api_url = (api_url or os.environ.get("HMN_API_URL") or _default_master_url()).rstrip("/")
    if resolved_client != "telegram":
        typer.echo(f"暂不支持审批客户端：{client}。当前可用：telegram。")
        raise typer.Exit(1)
    resolved_target = target or os.environ.get("HMN_APPROVAL_GATEWAY_TARGET") or os.environ.get("HMN_TELEGRAM_CHAT_ID")
    resolved_token = token or os.environ.get("HMN_APPROVAL_GATEWAY_TOKEN") or os.environ.get("HMN_TELEGRAM_BOT_TOKEN")
    if not resolved_target:
        typer.echo("缺少审批目标：请传 --target/--chat-id 或设置 HMN_APPROVAL_GATEWAY_TARGET/HMN_TELEGRAM_CHAT_ID。")
        raise typer.Exit(1)
    if not resolved_token:
        typer.echo("缺少审批客户端 token：请传 --token 或设置 HMN_APPROVAL_GATEWAY_TOKEN/HMN_TELEGRAM_BOT_TOKEN。")
        raise typer.Exit(1)
    return resolved_api_url, ApprovalGatewayClientConfig(client=resolved_client, target=resolved_target), resolved_token


def _telegram_gateway_settings(
    api_url: str | None,
    chat_id: str | None,
    token: str | None,
) -> tuple[str, str, str]:
    resolved_api_url, config, resolved_token = _approval_gateway_settings("telegram", api_url, chat_id, token)
    return resolved_api_url, config.target, str(resolved_token)


def _approval_gateway_offset_path(client: str) -> Path:
    safe_client = "".join(ch for ch in client if ch.isalnum() or ch in {"-", "_"}) or "telegram"
    return Path(f"/var/lib/hermes-managed-network/{safe_client}-gateway.update_offset")


def _read_approval_gateway_offset(client: str) -> int | None:
    path = _approval_gateway_offset_path(client)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return int(value) if value else None


def _write_approval_gateway_offset(client: str, offset: int | None) -> None:
    if offset is None:
        return
    path = _approval_gateway_offset_path(client)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset), encoding="utf-8")


@approval_gateway_app.command("poll-once")
def approval_gateway_poll_once(
    client: str = typer.Option("telegram", "--client", help="审批客户端，目前支持 telegram"),
    api_url: str | None = typer.Option(None, "--api-url", help="HMN API 地址，默认 HMN_API_URL 或本机 master URL"),
    target: str | None = typer.Option(None, "--target", help="客户端目标，例如 Telegram chat id"),
    token: str | None = typer.Option(None, "--token", help="客户端 token，Telegram 默认 HMN_TELEGRAM_BOT_TOKEN"),
) -> None:
    """拉取一次 outbox，并向指定客户端发送审批通知。"""
    resolved_api_url, config, resolved_token = _approval_gateway_settings(client, api_url, target, token)
    api_client = ApprovalGatewayHttpApiClient(resolved_api_url, client=config.client)
    gateway_client = TelegramApprovalGatewayClient(str(resolved_token))
    result = poll_once(api_client, gateway_client, config)
    callback_result = process_telegram_callbacks(
        api_client,
        gateway_client,
        offset=_read_approval_gateway_offset(config.client),
    )
    _write_approval_gateway_offset(config.client, callback_result.next_offset)
    typer.echo(
        f"sent={result.sent} failed={result.failed} callbacks={callback_result.processed} "
        f"approved={callback_result.approved} rejected={callback_result.rejected} callback_failed={callback_result.failed}"
    )
    if result.failed or callback_result.failed:
        raise typer.Exit(1)


@approval_gateway_app.command("run")
def approval_gateway_run(
    client: str = typer.Option("telegram", "--client", help="审批客户端，目前支持 telegram"),
    api_url: str | None = typer.Option(None, "--api-url", help="HMN API 地址，默认 HMN_API_URL 或本机 master URL"),
    target: str | None = typer.Option(None, "--target", help="客户端目标，例如 Telegram chat id"),
    token: str | None = typer.Option(None, "--token", help="客户端 token，Telegram 默认 HMN_TELEGRAM_BOT_TOKEN"),
    interval_seconds: int = typer.Option(10, "--interval", min=1, help="轮询间隔秒数"),
    once: bool = typer.Option(False, "--once", help="只轮询一次后退出"),
) -> None:
    """持续轮询 outbox，并向指定客户端发送审批通知。"""
    resolved_api_url, config, resolved_token = _approval_gateway_settings(client, api_url, target, token)
    api_client = ApprovalGatewayHttpApiClient(resolved_api_url, client=config.client)
    gateway_client = TelegramApprovalGatewayClient(str(resolved_token))
    while True:
        result = poll_once(api_client, gateway_client, config)
        callback_result = process_telegram_callbacks(
            api_client,
            gateway_client,
            offset=_read_approval_gateway_offset(config.client),
        )
        _write_approval_gateway_offset(config.client, callback_result.next_offset)
        typer.echo(
            f"sent={result.sent} failed={result.failed} callbacks={callback_result.processed} "
            f"approved={callback_result.approved} rejected={callback_result.rejected} callback_failed={callback_result.failed}"
        )
        if once:
            if result.failed or callback_result.failed:
                raise typer.Exit(1)
            return
        time.sleep(interval_seconds)


@telegram_gateway_app.command("poll-once")
def telegram_gateway_poll_once(
    api_url: str | None = typer.Option(None, "--api-url", help="HMN API 地址，默认 HMN_API_URL 或本机 master URL"),
    chat_id: str | None = typer.Option(None, "--chat-id", help="Telegram 目标 chat id，默认 HMN_TELEGRAM_CHAT_ID"),
    token: str | None = typer.Option(None, "--token", help="Telegram Bot token，默认 HMN_TELEGRAM_BOT_TOKEN"),
) -> None:
    """兼容旧命令：拉取一次 outbox，并向 Telegram 发送审批通知。"""
    resolved_api_url, resolved_chat_id, resolved_token = _telegram_gateway_settings(api_url, chat_id, token)
    result = telegram_poll_once(
        HttpGatewayApiClient(resolved_api_url),
        TelegramBotApiClient(resolved_token),
        chat_id=resolved_chat_id,
    )
    typer.echo(f"sent={result.sent} failed={result.failed}")
    if result.failed:
        raise typer.Exit(1)


@telegram_gateway_app.command("run")
def telegram_gateway_run(
    api_url: str | None = typer.Option(None, "--api-url", help="HMN API 地址，默认 HMN_API_URL 或本机 master URL"),
    chat_id: str | None = typer.Option(None, "--chat-id", help="Telegram 目标 chat id，默认 HMN_TELEGRAM_CHAT_ID"),
    token: str | None = typer.Option(None, "--token", help="Telegram Bot token，默认 HMN_TELEGRAM_BOT_TOKEN"),
    interval_seconds: int = typer.Option(10, "--interval", min=1, help="轮询间隔秒数"),
    once: bool = typer.Option(False, "--once", help="只轮询一次后退出"),
) -> None:
    """兼容旧命令：持续轮询 outbox，并向 Telegram 发送审批通知。"""
    resolved_api_url, resolved_chat_id, resolved_token = _telegram_gateway_settings(api_url, chat_id, token)
    api_client = HttpGatewayApiClient(resolved_api_url)
    telegram_client = TelegramBotApiClient(resolved_token)
    while True:
        result = telegram_poll_once(api_client, telegram_client, chat_id=resolved_chat_id)
        typer.echo(f"sent={result.sent} failed={result.failed}")
        if once:
            if result.failed:
                raise typer.Exit(1)
            return
        time.sleep(interval_seconds)


@backup_app.command("plan")
def backup_plan(
    node_id: str = typer.Option(..., "--node", help="目标节点 ID"),
    include: list[str] = typer.Option(..., "--include", help="本地路径，可重复"),
    output_dir: Path = typer.Option(Path("./hmn-backups"), "--output-dir", help="manifest 输出目录"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    component, _node = _load_component_for_node(store, "backup", node_id)
    plan = _backup_plan_payload(node_id, include, output_dir, str(component.drivers.get("default", "local-archive")))
    run = store.record_component_run(component_id="backup", node_id=node_id, action="backup.plan", risk=component.risk, status="planned", plan=plan)
    typer.echo("backup plan: dry-run")
    typer.echo(f"run: {run.run_id}")
    typer.echo(f"node: {node_id}")
    typer.echo("restore: disabled")
    for item in include:
        typer.echo(f"include: {item}")
    typer.echo(f"output_dir: {output_dir}")


@backup_app.command("run")
def backup_run(
    node_id: str = typer.Option(..., "--node", help="目标节点 ID"),
    include: list[str] = typer.Option(..., "--include", help="本地路径，可重复"),
    output_dir: Path = typer.Option(Path("./hmn-backups"), "--output-dir", help="manifest 输出目录"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    component, _node = _load_component_for_node(store, "backup", node_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _backup_manifest_for_paths(include, dry_run=False)
    plan = _backup_plan_payload(node_id, include, output_dir, str(component.drivers.get("default", "local-archive")))
    archive_path, manifest_path = _backup_archive_paths(output_dir, node_id)
    _archive_backup_paths(include, archive_path)
    archive_sha256 = _sha256_file(archive_path)
    manifest.update(
        {
            "node_id": node_id,
            "archive_path": str(archive_path),
            "archive_sha256": archive_sha256,
            "archive_size_bytes": archive_path.stat().st_size,
        }
    )
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    result = {
        "dry_run": False,
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha256,
        "archive_size_bytes": archive_path.stat().st_size,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "entries": len(manifest["entries"]),
        "total_bytes": manifest["total_bytes"],
        "restore_enabled": False,
        "machine_changed": True,
    }
    run = store.record_component_run(component_id="backup", node_id=node_id, action="backup.run", risk=component.risk, status="succeeded", plan=plan, result=result)
    store.record_audit(event_type="component.backup", subject_type="component", subject_id="backup", action="run", outcome="succeeded", details={"node_id": node_id, "run_id": run.run_id, **result})
    typer.echo("backup run: succeeded")
    typer.echo(f"run: {run.run_id}")
    typer.echo(f"archive: {archive_path}")
    typer.echo(f"archive_checksum: {archive_sha256}")
    typer.echo(f"manifest: {manifest_path}")
    typer.echo(f"checksum: {manifest['manifest_sha256']}")
    typer.echo("restore: disabled")


@backup_app.command("status")
def backup_status(
    node_id: str | None = typer.Option(None, "--node", help="目标节点 ID"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    run = _latest_backup_run(store, node_id)
    if run is None:
        typer.echo("backup status: missing")
        raise typer.Exit(1)
    typer.echo(f"backup status: {run.status}")
    typer.echo(f"run: {run.run_id}")
    typer.echo(f"node: {run.node_id}")
    typer.echo(f"action: {run.action}")
    typer.echo(f"dry_run: {bool(run.plan.get('dry_run') or run.result.get('dry_run'))}")
    if run.result.get("archive_path"):
        typer.echo(f"archive: {run.result['archive_path']}")
    if run.result.get("archive_sha256"):
        typer.echo(f"archive_checksum: {run.result['archive_sha256']}")
    if run.result.get("manifest_path"):
        typer.echo(f"manifest: {run.result['manifest_path']}")
    if run.result.get("manifest_sha256"):
        typer.echo(f"checksum: {run.result['manifest_sha256']}")
    typer.echo("restore: disabled")


@backup_app.command("verify")
def backup_verify(
    node_id: str | None = typer.Option(None, "--node", help="目标节点 ID"),
    manifest_path: Path | None = typer.Option(None, "--manifest", help="要验证的 manifest；默认使用最近一次 backup run"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    run = _latest_backup_run(store, node_id) if manifest_path is None else None
    resolved_manifest = manifest_path or (Path(str(run.result.get("manifest_path"))) if run and run.result.get("manifest_path") else None)
    effective_node = node_id or (run.node_id if run else "unknown")
    if resolved_manifest is None:
        result = {"manifest_path": None, "error": "missing_manifest", "restore_enabled": False}
        recorded = _record_backup_verify_result(store, node_id=effective_node, result=result, ok=False)
        typer.echo("backup verify: missing manifest")
        typer.echo(f"run: {recorded.run_id}")
        typer.echo("restore: disabled")
        raise typer.Exit(1)
    try:
        manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        result = {"manifest_path": str(resolved_manifest), "error": "missing_manifest", "restore_enabled": False}
        recorded = _record_backup_verify_result(store, node_id=effective_node, result=result, ok=False)
        typer.echo(f"backup verify: missing manifest {resolved_manifest}")
        typer.echo(f"run: {recorded.run_id}")
        typer.echo("restore: disabled")
        raise typer.Exit(1)
    effective_node = node_id or (run.node_id if run else str(manifest.get("node_id") or "unknown"))
    archive_value = manifest.get("archive_path")
    archive_sha = manifest.get("archive_sha256")
    if not archive_value or not archive_sha:
        result = {"manifest_path": str(resolved_manifest), "error": "missing_archive_metadata", "restore_enabled": False}
        recorded = _record_backup_verify_result(store, node_id=effective_node, result=result, ok=False)
        typer.echo("backup verify: missing archive metadata")
        typer.echo(f"run: {recorded.run_id}")
        typer.echo("restore: disabled")
        raise typer.Exit(1)
    archive_path = Path(str(archive_value))
    if not archive_path.is_file():
        result = {
            "manifest_path": str(resolved_manifest),
            "archive_path": str(archive_path),
            "archive_sha256": archive_sha,
            "archive_exists": False,
            "checksum_match": False,
            "error": "missing_archive",
            "restore_enabled": False,
        }
        recorded = _record_backup_verify_result(store, node_id=effective_node, result=result, ok=False)
        typer.echo(f"backup verify: missing archive {archive_path}")
        typer.echo(f"run: {recorded.run_id}")
        typer.echo("restore: disabled")
        raise typer.Exit(1)
    actual_sha = _sha256_file(archive_path)
    ok = actual_sha == archive_sha
    result = {
        "manifest_path": str(resolved_manifest),
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha,
        "actual_archive_sha256": actual_sha,
        "archive_exists": archive_path.is_file(),
        "checksum_match": ok,
        "restore_enabled": False,
    }
    recorded = _record_backup_verify_result(store, node_id=effective_node, result=result, ok=ok)
    typer.echo("backup verify: ok" if ok else "backup verify: failed")
    typer.echo(f"run: {recorded.run_id}")
    typer.echo(f"archive: {archive_path}")
    typer.echo(f"archive_checksum: {archive_sha}")
    typer.echo(f"actual_checksum: {actual_sha}")
    typer.echo("restore: disabled")
    if not ok:
        raise typer.Exit(1)


@component_app.command("list")
def list_components(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    store = _store(db)
    components = _ensure_builtin_components(store)
    for component in components.values():
        typer.echo(f"{component.id}\t{component.name}\t{component.version}\trisk={component.risk}")


@component_app.command("show")
def show_component(
    component_id: str = typer.Argument(..., help="组件 ID"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    _ensure_builtin_components(store)
    component = store.load_component(component_id)
    if component is None:
        raise typer.BadParameter(f"未知组件: {component_id}")
    typer.echo(f"component: {component.id}")
    typer.echo(f"name: {component.name}")
    typer.echo(f"version: {component.version}")
    typer.echo(f"api_version: {component.api_version}")
    typer.echo(f"risk: {component.risk}")
    typer.echo(f"driver: {component.drivers.get('default', '')}")
    typer.echo("requires:")
    for key, value in component.requires.items():
        typer.echo(f"  {key}: {value}")
    typer.echo("provides:")
    for key, value in component.provides.items():
        typer.echo(f"  {key}: {value}")


@component_app.command("plan")
def plan_component(
    component_id: str = typer.Argument(..., help="组件 ID"),
    node_id: str = typer.Option(..., "--node", help="目标节点 ID"),
    set_values: list[str] = typer.Option([], "--set", help="组件配置 KEY=VALUE，可重复"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    component, _node = _load_component_for_node(store, component_id, node_id)
    config = _parse_key_values(set_values)
    plan = _component_plan(component, node_id=node_id, config=config)
    run = store.record_component_run(
        component_id=component.id,
        node_id=node_id,
        action="plan",
        risk=component.risk,
        status="planned",
        plan=plan,
    )
    typer.echo(f"plan: {component.id}")
    typer.echo(f"run: {run.run_id}")
    typer.echo(f"node: {node_id}")
    typer.echo(f"risk: {component.risk}")
    typer.echo("mutating: no")
    typer.echo(f"driver: {plan['driver']}")
    typer.echo("config:")
    for key, value in config.items():
        typer.echo(f"  {key}: {value}")
    typer.echo("apply command:")
    typer.echo(f"  hmn component apply {component.id} --node {node_id}")


@component_app.command("apply")
def apply_component(
    component_id: str = typer.Argument(..., help="组件 ID"),
    node_id: str = typer.Option(..., "--node", help="目标节点 ID"),
    set_values: list[str] = typer.Option([], "--set", help="组件配置 KEY=VALUE，可重复"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    """记录组件期望状态；MVP 不真实修改机器。"""
    store = _store(db)
    component, _node = _load_component_for_node(store, component_id, node_id)
    config = _parse_key_values(set_values)
    plan = _component_plan(component, node_id=node_id, config=config, action="apply", mutating=True)
    if _component_action_requires_approval(component.risk, mutating=bool(plan["mutating"])):
        run, approval = _request_component_action_approval(
            store,
            component,
            node_id=node_id,
            action="apply",
            plan=plan,
            config=config,
        )
        typer.echo(f"需要审批: {approval.approval_id}")
        typer.echo(f"apply: {component.id}")
        typer.echo(f"run: {run.run_id}")
        typer.echo(f"node: {node_id}")
        typer.echo("state: pending_approval")
        raise typer.Exit(1)
    monitor_snapshot = None
    current_state = "planned"
    extra_result: dict[str, object] = {}
    if component.id == "monitor":
        summary = _monitor_health_summary(store, node_id)
        monitor_snapshot = _record_monitor_snapshot(store, node_id, summary)
        current_state = monitor_snapshot.health
        extra_result = {
            "monitor_snapshot_id": monitor_snapshot.snapshot_id,
            "monitor_health": monitor_snapshot.health,
            "monitor_reason": monitor_snapshot.reason,
            "heartbeat_seen": summary["heartbeat_seen"],
            "worker_compatible": summary["worker_compatible"],
        }
    result = {"machine_changed": False, "state_closed_loop": True, "remote_execution": "not_enabled", **extra_result}
    run = store.record_component_run(
        component_id=component.id,
        node_id=node_id,
        action="apply",
        risk=component.risk,
        status="state_recorded",
        plan=plan,
        result=result,
    )
    store.set_node_component(
        node_id=node_id,
        component_id=component.id,
        desired_state="enabled",
        current_state=current_state,
        config=config,
        installed_version=component.version,
        driver=str(component.drivers.get("default", "")),
        last_run_id=run.run_id,
    )
    typer.echo(f"apply: {component.id}")
    typer.echo(f"run: {run.run_id}")
    typer.echo(f"node: {node_id}")
    typer.echo("machine_changed: no")
    typer.echo(f"state: enabled/{current_state}")
    if monitor_snapshot is not None:
        typer.echo(f"monitor_snapshot: {monitor_snapshot.snapshot_id}")
        typer.echo(f"monitor_health: {monitor_snapshot.health}")
        typer.echo(f"monitor_reason: {monitor_snapshot.reason}")
    typer.echo("说明: MVP 只闭环状态与审计，不真实改机器。")


@component_app.command("verify")
def verify_component(
    component_id: str = typer.Argument(..., help="组件 ID"),
    node_id: str = typer.Option(..., "--node", help="目标节点 ID"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    """独立检查组件状态；不依赖 apply 记录。"""
    store = _store(db)
    component, node = _load_component_for_node(store, component_id, node_id)
    plan = _component_plan(component, node_id=node_id, config={}, action="verify", mutating=False)
    if component.id == "monitor":
        result = _monitor_summary(store, node_id)
        status = "ok" if result["heartbeat_ok"] else "warn"
        run = store.record_component_run(
            component_id=component.id,
            node_id=node_id,
            action="verify",
            risk=component.risk,
            status=status,
            plan=plan,
            result=result,
        )
        store.record_audit(
            event_type="component.monitor",
            subject_type="component",
            subject_id=component.id,
            action="verify",
            outcome=status,
            details={"node_id": node_id, "run_id": run.run_id, **result},
        )
        existing = next((item for item in store.list_node_components(node_id) if item.component_id == component.id), None)
        store.set_node_component(
            node_id=node_id,
            component_id=component.id,
            desired_state=existing.desired_state if existing else "enabled",
            current_state=status,
            config=existing.config if existing else {},
            installed_version=existing.installed_version if existing else component.version,
            driver=existing.driver if existing else str(component.drivers.get("default", "")),
            last_run_id=run.run_id,
            last_verified_at=datetime.now(timezone.utc),
        )
        typer.echo(f"verify: {component.id}")
        typer.echo(f"run: {run.run_id}")
        typer.echo(f"node: {node_id}")
        typer.echo("independent: yes")
        typer.echo("remote_check: heartbeat_audit")
        if result["heartbeat_seen"]:
            typer.echo(f"heartbeat: {'OK' if result['heartbeat_ok'] else 'WARN'}")
        else:
            typer.echo("heartbeat: WARN missing")
        _echo_monitor_summary(result)
        if status != "ok":
            raise typer.Exit(1)
        return
    try:
        target = ssh_target_details_for_node(node)
        result = {
            "independent_from_apply": True,
            "remote_check": "overlay_network" if target.source == "network_ip" else "target_resolved",
            "probe_target": target.host,
            "target_source": target.source,
            "network_provider": node.network_provider,
            "network_online": node.network_online,
            "message": "component verification target resolved through node networking metadata",
        }
    except ValueError:
        target = None
        result = {
            "independent_from_apply": True,
            "remote_check": "not_enabled",
            "message": "remote component verification target is not configured",
        }
    run = store.record_component_run(
        component_id=component.id,
        node_id=node_id,
        action="verify",
        risk=component.risk,
        status="checked",
        plan=plan,
        result=result,
    )
    store.record_audit(
        event_type=component.audit.get("category", "component"),
        subject_type="component",
        subject_id=component.id,
        action="verify",
        outcome="checked",
        details={"node_id": node_id, "run_id": run.run_id, **result},
    )
    typer.echo(f"verify: {component.id}")
    typer.echo(f"run: {run.run_id}")
    typer.echo(f"node: {node_id}")
    typer.echo("independent: yes")
    typer.echo(f"remote_check: {result['remote_check']}")
    if target is not None:
        typer.echo(f"probe_target: {target.host}")
        typer.echo(f"target_source: {target.source}")


@component_app.command("uninstall")
def uninstall_component(
    component_id: str = typer.Argument(..., help="组件 ID"),
    node_id: str = typer.Option(..., "--node", help="目标节点 ID"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    """记录组件卸载期望状态；卸载是一等动作。"""
    store = _store(db)
    component, _node = _load_component_for_node(store, component_id, node_id)
    plan = _component_plan(component, node_id=node_id, config={}, action="uninstall", mutating=True)
    if _component_action_requires_approval(component.risk, mutating=bool(plan["mutating"])):
        run, approval = _request_component_action_approval(
            store,
            component,
            node_id=node_id,
            action="uninstall",
            plan=plan,
        )
        typer.echo(f"需要审批: {approval.approval_id}")
        typer.echo(f"uninstall: {component.id}")
        typer.echo(f"run: {run.run_id}")
        typer.echo(f"node: {node_id}")
        typer.echo("state: pending_approval")
        raise typer.Exit(1)
    result = {"machine_changed": False, "state_closed_loop": True, "remote_execution": "not_enabled"}
    run = store.record_component_run(
        component_id=component.id,
        node_id=node_id,
        action="uninstall",
        risk=component.risk,
        status="state_recorded",
        plan=plan,
        result=result,
    )
    existing = next(
        (item for item in store.list_node_components(node_id) if item.component_id == component.id),
        None,
    )
    store.set_node_component(
        node_id=node_id,
        component_id=component.id,
        desired_state="absent",
        current_state="planned",
        config=existing.config if existing else {},
        installed_version=existing.installed_version if existing else component.version,
        driver=existing.driver if existing else str(component.drivers.get("default", "")),
        last_run_id=run.run_id,
    )
    typer.echo(f"uninstall: {component.id}")
    typer.echo(f"run: {run.run_id}")
    typer.echo(f"node: {node_id}")
    typer.echo("machine_changed: no")
    typer.echo("state: absent/planned")


@component_app.command("status")
def component_status(
    node_id: str | None = typer.Option(None, "--node", help="目标节点 ID；省略时显示所有节点组件"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    _ensure_builtin_components(store)
    items = store.list_node_components(node_id)
    if node_id:
        typer.echo(f"node: {node_id}")
        if store.load_node(node_id) is not None:
            summary = _monitor_summary(store, node_id)
            if summary["heartbeat_seen"]:
                typer.echo("monitor:")
                _echo_monitor_summary(summary)
    if not items:
        typer.echo("暂无组件状态")
        return
    for item in items:
        typer.echo(
            f"{item.node_id}\t{item.component_id}\tdesired={item.desired_state}\tcurrent={item.current_state}\tdriver={item.driver}"
        )


def _audit_ssh_summary(details: dict[str, object]) -> str:
    reason = str(details.get("failure_reason") or "")
    stdout_preview = str(details.get("stdout_preview") or "")
    stderr_preview = str(details.get("stderr_preview") or "")
    duration_ms = details.get("duration_ms")
    ssh_connectivity = details.get("ssh_connectivity") if isinstance(details.get("ssh_connectivity"), dict) else None
    if ssh_connectivity is not None:
        summary = _summarize_ssh_connectivity(ssh_connectivity)
        return summary["reason"]
    if reason:
        return _format_ssh_reason_cn(reason, stderr_preview)
    if duration_ms is not None or stdout_preview or stderr_preview:
        parts: list[str] = []
        if duration_ms is not None:
            parts.append(f"耗时 {duration_ms}ms")
        if stdout_preview:
            parts.append(f"stdout={stdout_preview}")
        if stderr_preview:
            parts.append(f"stderr={stderr_preview}")
        return "；".join(parts) if parts else "-"
    return "-"


@audit_app.command("list")
def list_audit_events(
    limit: int = typer.Option(50, "--limit", min=1, max=500, help="最多显示多少条"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    events = _store(db).list_audit_events()[-limit:]
    if json_output:
        payload = [
            {
                "event_type": event.event_type,
                "subject_type": event.subject_type,
                "subject_id": event.subject_id,
                "action": event.action,
                "outcome": event.outcome,
                "details": event.details,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for event in events:
        typer.echo(
            f"{event.created_at.isoformat()}\t{event.event_type}\t{event.subject_type}:{event.subject_id}\t{event.action}\t{event.outcome}"
        )
        ssh_summary = _audit_ssh_summary(event.details)
        if ssh_summary != "-":
            typer.echo(f"  ssh: {ssh_summary}")


@audit_app.command("show")
def show_audit_event(
    subject_id: str = typer.Argument(..., help="事件 subject_id"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    events = [event for event in _store(db).list_audit_events() if event.subject_id == subject_id]
    if not events:
        typer.echo("审计事件不存在。")
        raise typer.Exit(1)
    event = events[-1]
    typer.echo(f"time: {event.created_at.isoformat()}")
    typer.echo(f"event_type: {event.event_type}")
    typer.echo(f"subject: {event.subject_type}:{event.subject_id}")
    typer.echo(f"action: {event.action}")
    typer.echo(f"outcome: {event.outcome}")
    ssh_summary = _audit_ssh_summary(event.details)
    if ssh_summary != "-":
        typer.echo(f"ssh: {ssh_summary}")
    typer.echo("details:")
    typer.echo(json.dumps(event.details, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    app()
