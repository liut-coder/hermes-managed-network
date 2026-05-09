from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, version as package_version
import shutil
import shlex
import socket
import subprocess
from datetime import timedelta
from pathlib import Path

import typer

from .executor import PlaybookExecutor
from .inventory import NodeRegistry
from .playbook import Playbook
from .storage import SQLiteStore
from .tokens import JoinTokenStore
from .version import current_version_info

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()
DEFAULT_PLAYBOOK_DIR = Path("playbooks")
INSTALL_URL = "https://raw.githubusercontent.com/liut-coder/hermes-managed-network/feat/control-plane-mvp/install.sh"
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
        "  hmn node install-heartbeat 安装节点心跳/worker\n"
        "  hmn node worker-status     查看节点 worker 安装状态\n"
        "  hmn task run              下发低风险任务\n"
        "  hmn task list             查看任务队列\n"
        "  hmn audit list            查看审计\n"
        "  hmn token create          创建 token\n"
        "  hmn version               查看版本\n"
        "  hmn update                输出更新命令\n"
        "  hmn uninstall             查看卸载命令"
    ),
    invoke_without_command=True,
)
token_app = typer.Typer(help="管理一次性节点接入令牌")
node_app = typer.Typer(help="管理已登记节点")
playbook_app = typer.Typer(help="运行本地 playbook")
audit_app = typer.Typer(help="查看审计事件")
task_app = typer.Typer(help="下发和查看节点任务")
app.add_typer(token_app, name="token")
app.add_typer(node_app, name="node")
app.add_typer(playbook_app, name="playbook")
app.add_typer(audit_app, name="audit")
app.add_typer(task_app, name="task")


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
    typer.echo("9. hmn task run                     下发低风险任务")
    typer.echo("10. hmn task list                   查看任务")
    typer.echo("11. hmn audit list                  查看审计")
    typer.echo("12. hmn token create                创建 token")
    typer.echo("13. hmn version                     查看版本")
    typer.echo("14. hmn update                      更新主控")
    typer.echo("15. hmn uninstall                   卸载主控")
    typer.echo("")
    typer.echo("示例：")
    typer.echo("  hmn wake")
    typer.echo("  hmn node confirm")
    typer.echo("  hmn node status")
    typer.echo("  hmn node doctor")
    typer.echo("  hmn node heartbeat-command")
    typer.echo("  hmn node install-heartbeat")
    typer.echo("  hmn node worker-status")
    typer.echo("  hmn task run 'uptime'")
    typer.echo("  hmn task list")
    typer.echo("  hmn audit list")
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
        typer.echo("9) hmn task run      下发任务")
        typer.echo("10) hmn task list    查看任务")
        typer.echo("11) hmn audit list   查看审计")
        typer.echo("12) hmn token create 创建 token")
        typer.echo("13) hmn version      查看版本")
        typer.echo("14) hmn update       更新主控")
        typer.echo("15) hmn uninstall    卸载主控")
        typer.echo("q) quit              退出")
        choice = typer.prompt("选择编号或命令", default="1")
        normalized = choice.strip().lower()
        if normalized in {"1", "wake", "hmn wake"}:
            wake(db=db, master_url=None)
            return
        if normalized in {"2", "node", "nodes", "node list", "hmn node list"}:
            list_nodes(db=db)
            return
        if normalized in {"3", "confirm", "node confirm", "hmn node confirm"}:
            confirm_node(node_id=None, bundle=["observe"], db=db)
            return
        if normalized in {"4", "status", "node status", "hmn node status"}:
            status_node(node_id=None, db=db)
            return
        if normalized in {"5", "doctor", "node doctor", "hmn node doctor"}:
            doctor_node(node_id=None, db=db)
            return
        if normalized in {"6", "heartbeat", "heartbeat-command", "node heartbeat-command", "hmn node heartbeat-command"}:
            heartbeat_command(node_id=None, master_url=None, db=db)
            return
        if normalized in {"7", "install-heartbeat", "node install-heartbeat", "hmn node install-heartbeat"}:
            install_heartbeat(node_id=None, master_url=None, db=db)
            return
        if normalized in {"8", "worker", "worker status", "node worker-status", "hmn node worker-status"}:
            worker_status(node_id=None, db=db)
            return
        if normalized in {"9", "task run", "hmn task run"}:
            command = typer.prompt("任务命令", default="uptime")
            create_task_command(command=command, node_id=None, risk="low", db=db)
            return
        if normalized in {"10", "task", "task list", "hmn task list"}:
            list_task_commands(db=db)
            return
        if normalized in {"11", "audit", "audit list", "hmn audit list"}:
            list_audit_events(limit=50, json_output=False, db=db)
            return
        if normalized in {"12", "token", "token create", "hmn token create"}:
            create_token(trust_level="B", label=[], ttl_minutes=30, db=db)
            return
        if normalized in {"13", "version", "hmn version"}:
            version()
            return
        if normalized in {"14", "update", "hmn update"}:
            update()
            return
        if normalized in {"15", "uninstall", "hmn uninstall"}:
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
    typer.echo(_render_join_command(token.value, selected_master_url, user, safe=True))


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


@node_app.command("list")
def list_nodes(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    for node in _store(db).list_nodes():
        typer.echo(f"{node.node_id}\t{node.status}\t{node.hostname}\ttrust={node.trust_level}")


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


def _render_node_status(node) -> None:
    typer.echo(f"node: {node.node_id}")
    typer.echo(f"status: {node.status}")
    typer.echo(f"host: {node.hostname}")
    typer.echo(f"trust: {node.trust_level}")
    typer.echo(f"labels: {', '.join(node.labels) if node.labels else '-'}")
    typer.echo(f"addresses: {', '.join(node.addresses) if node.addresses else '-'}")
    typer.echo(f"bundles: {', '.join(node.permission_bundles) if node.permission_bundles else '-'}")


@node_app.command("status")
def status_node(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    _render_node_status(node)


@node_app.command("doctor")
def doctor_node(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    checks = {
        "登记状态": node.status == "managed",
        "指纹": bool(node.fingerprint),
        "信任级别": node.trust_level in {"A", "B", "C"},
        "权限包": bool(node.permission_bundles),
    }
    outcome = "ok" if all(checks.values()) else "warn"
    typer.echo(f"doctor: {node.node_id}")
    typer.echo(f"host: {node.hostname}")
    for name, ok in checks.items():
        typer.echo(f"{name}: {'OK' if ok else 'WARN'}")
    typer.echo("远程执行: 未启用（下一步接 SSH/worker 通道）")
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=node.node_id,
        action="doctor",
        outcome=outcome,
        details={"checks": checks, "remote_execution": "not_configured"},
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


@node_app.command("worker-status")
def worker_status(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    event = _latest_heartbeat_event(store, node.node_id)
    facts = event.details.get("facts", {}) if event else {}
    compatible = bool(event.details.get("worker_compatible", True)) if event else False
    protocol = facts.get("worker_protocol_version") or "unknown"
    version_value = facts.get("worker_version") or "unknown"
    exec_enabled = bool(facts.get("exec_enabled", False))

    typer.echo(f"worker: {node.node_id}")
    typer.echo(f"host: {node.hostname}")
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
        },
    )
    if event is None or event.outcome != "ok" or not compatible:
        raise typer.Exit(1)


def _render_worker_installer(node, master_url: str) -> str:
    url = master_url.rstrip("/")
    script = f"""set -euo pipefail
install -d -m 0700 /etc/hermes-managed-network
cat >/etc/hermes-managed-network/node.env <<'EOF'
HERMES_MASTER_URL={url}
HERMES_NODE_ID={node.node_id}
HERMES_NODE_FINGERPRINT={node.fingerprint}
HMN_ENABLE_EXEC=0
EOF
chmod 0600 /etc/hermes-managed-network/node.env
curl -fsSL {url}/scripts/worker.sh -o /usr/local/bin/hmn-worker
chmod 0755 /usr/local/bin/hmn-worker
cat >/etc/systemd/system/hermes-managed-network-heartbeat.service <<'EOF'
[Unit]
Description=Hermes Managed Network heartbeat and worker
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/hermes-managed-network/node.env
Environment=HMN_ENABLE_EXEC=0
ExecStart=/usr/local/bin/hmn-worker
EOF
cat >/etc/systemd/system/hermes-managed-network-heartbeat.timer <<'EOF'
[Unit]
Description=Run Hermes Managed Network heartbeat and worker every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
Unit=hermes-managed-network-heartbeat.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now hermes-managed-network-heartbeat.timer
"""
    return "sudo bash -lc " + _shell_quote(script)


@node_app.command("install-heartbeat")
def install_heartbeat(
    node_id: str | None = typer.Argument(None, help="节点 ID；省略时自动选择唯一的 managed 节点", show_default=False),
    master_url: str | None = typer.Option(None, "--master-url", help="主控 URL；默认自动读取 HMN_PUBLIC_URL 或安装配置"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    store = _store(db)
    node = _select_managed_node(store, node_id)
    url = (master_url or _default_master_url()).rstrip("/")
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=node.node_id,
        action="install-heartbeat",
        outcome="rendered",
        details={"master_url": url, "service_manager": "systemd", "enable_exec": False},
    )
    typer.echo("请复制下面命令到目标节点执行，它会安装心跳/worker 定时器：")
    typer.echo("默认安全模式：HMN_ENABLE_EXEC=0，不会执行下发 shell 命令。")
    typer.echo(_render_worker_installer(node, url))


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
    command: str = typer.Argument(..., help="要在节点上执行的低风险 shell 命令"),
    node_id: str | None = typer.Option(None, "--node", help="节点 ID；省略时自动选择唯一 managed 节点"),
    risk: str = typer.Option("low", "--risk", help="风险级别；当前只允许 low/medium"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    if risk not in {"low", "medium"}:
        typer.echo("当前只允许下发 low/medium 风险任务。")
        raise typer.Exit(1)
    store = _store(db)
    node = _select_managed_node(store, node_id)
    task = store.create_task(node_id=node.node_id, command=command, risk=risk, created_by="hmn")
    typer.echo(f"已创建任务: {task.task_id}")
    typer.echo(f"节点: {node.node_id}")
    typer.echo(f"命令: {command}")


@task_app.command("list")
def list_task_commands(db: Path = typer.Option(None, "--db", help="SQLite 数据库路径")) -> None:
    for task in _store(db).list_tasks():
        typer.echo(f"{task.task_id}	{task.node_id}	{task.status}	risk={task.risk}	{task.command}")


@audit_app.command("list")
def list_audit_events(
    limit: int = typer.Option(50, "--limit", "-n", help="最多显示多少条事件"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON Lines"),
    db: Path = typer.Option(None, "--db", help="SQLite 数据库路径"),
) -> None:
    events = _store(db).list_audit_events()[-limit:]
    for event in events:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "created_at": event.created_at.isoformat(),
                        "event_type": event.event_type,
                        "subject_type": event.subject_type,
                        "subject_id": event.subject_id,
                        "action": event.action,
                        "outcome": event.outcome,
                        "details": event.details,
                    },
                    sort_keys=True,
                )
            )
        else:
            details = json.dumps(event.details, ensure_ascii=False, sort_keys=True)
            typer.echo(
                f"{event.created_at.isoformat()}\t{event.event_type}\t{event.subject_type}\t"
                f"{event.subject_id}\t{event.action}\t{event.outcome}\t{details}"
            )


if __name__ == "__main__":
    app()
