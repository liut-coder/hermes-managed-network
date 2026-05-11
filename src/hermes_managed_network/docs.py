from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .inventory import Node
from .storage import SQLiteStore


DEFAULT_DOCS_ROOT = Path("/srv/files/docs")
DEFAULT_SERVICE_ROOT = Path("/srv/files/service")


@dataclass(frozen=True)
class DocsGenerateResult:
    server_count: int
    paths: list[Path]
    service_index: Path | None = None
    domain_index: Path | None = None
    runbook_index: Path | None = None


@dataclass(frozen=True)
class ServiceDoc:
    service_id: str
    title: str
    node: str = ""
    url: str = ""
    port: str = ""
    summary: str = ""


def _safe_segment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_", ".") else "-" for char in value.strip())
    return cleaned.strip(".-") or "unknown"


def _inline_list(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "-"


def _node_doc_dir(output_root: Path, node: Node) -> Path:
    return output_root / "server" / _safe_segment(node.hostname or node.node_id)


def _render_server_doc(node: Node) -> str:
    ssh_host = node.ssh_host or node.network_ip or (node.addresses[0] if node.addresses else "")
    ssh_user = node.ssh_user or "root"
    ssh_line = f"`{ssh_user}@{ssh_host} -p {node.ssh_port}`" if ssh_host else "-"
    network_state = "online" if node.network_online else "offline"
    headscale_line = "-"
    if node.network_node_id or node.network_ip or node.network_provider:
        provider_id = node.network_node_id or "-"
        network_ip = node.network_ip or "-"
        headscale_line = f"`{provider_id}` / `{network_ip}` / {network_state}"
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return "\n".join(
        [
            f"# {node.hostname}",
            "",
            "## 基本信息",
            f"- 节点 ID: `{node.node_id}`",
            f"- 状态: `{node.status}`",
            f"- 信任等级: `{node.trust_level}`",
            f"- 标签: {_inline_list(node.labels)}",
            f"- 权限包: {_inline_list(node.permission_bundles)}",
            "",
            "## 连接信息",
            f"- SSH: {ssh_line}",
            f"- 地址: {_inline_list(node.addresses)}",
            f"- Headscale: {headscale_line}",
            f"- 网络标签: {_inline_list(node.network_tags)}",
            "",
            "## 运维记录",
            "- 待补充：部署服务、端口、域名、备份、迁移记录。",
            "",
            f"生成时间: `{generated_at}`",
            "",
        ]
    )


def write_server_doc(store: SQLiteStore, node_id: str, output_root: Path = DEFAULT_DOCS_ROOT) -> Path:
    node = store.load_node(node_id)
    if node is None:
        raise ValueError(f"node not found: {node_id}")
    doc_dir = _node_doc_dir(output_root, node)
    doc_dir.mkdir(parents=True, exist_ok=True)
    doc_path = doc_dir / "README.md"
    doc_path.write_text(_render_server_doc(node), encoding="utf-8")
    return doc_path


def write_server_index(store: SQLiteStore, output_root: Path = DEFAULT_DOCS_ROOT) -> Path:
    nodes = store.list_nodes()
    index_dir = output_root / "server"
    index_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# HMN 机器索引", ""]
    if not nodes:
        lines.append("暂无节点。")
    for node in nodes:
        segment = _safe_segment(node.hostname or node.node_id)
        ip = node.network_ip or (node.addresses[0] if node.addresses else "-")
        lines.append(f"- [{node.hostname}]({segment}/README.md) — `{node.status}` — `{ip}`")
    lines.append("")
    index_path = index_dir / "README.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path




def _service_doc_dir(service_root: Path, service_id: str) -> Path:
    return service_root / _safe_segment(service_id)


def _render_service_doc(service: ServiceDoc) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    title = service.title or service.service_id
    return "\n".join(
        [
            f"# {title}",
            "",
            "## 基本信息",
            f"- 服务 ID: `{service.service_id}`",
            f"- 节点: `{service.node}`" if service.node else "- 节点: -",
            f"- URL: `{service.url}`" if service.url else "- URL: -",
            f"- 端口: `{service.port}`" if service.port else "- 端口: -",
            "",
            "## 简介",
            service.summary or "待补充。",
            "",
            "## 运维记录",
            "- 待补充：部署路径、systemd 服务名、环境变量、备份和恢复说明。",
            "",
            f"生成时间: `{generated_at}`",
            "",
        ]
    )


def write_service_doc(service: ServiceDoc, service_root: Path = DEFAULT_SERVICE_ROOT) -> Path:
    doc_dir = _service_doc_dir(service_root, service.service_id)
    doc_dir.mkdir(parents=True, exist_ok=True)
    doc_path = doc_dir / "README.md"
    doc_path.write_text(_render_service_doc(service), encoding="utf-8")
    return doc_path


def _service_title_from_doc(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        return path.parent.name
    return path.parent.name


def write_service_index(service_root: Path = DEFAULT_SERVICE_ROOT) -> Path:
    service_root.mkdir(parents=True, exist_ok=True)
    entries = []
    for readme in sorted(service_root.glob("*/README.md")):
        service_id = readme.parent.name
        title = _service_title_from_doc(readme)
        entries.append(f"- [{title}]({service_id}/README.md) — `{service_id}`")
    lines = ["# HMN 服务索引", ""]
    lines.extend(entries or ["暂无服务。"])
    lines.append("")
    index_path = service_root / "README.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def _service_url_from_doc(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- URL: `") and line.endswith("`"):
                return line.removeprefix("- URL: `").removesuffix("`")
    except OSError:
        return ""
    return ""


def write_domain_index(service_root: Path = DEFAULT_SERVICE_ROOT) -> Path:
    service_root.mkdir(parents=True, exist_ok=True)
    entries = []
    for readme in sorted(service_root.glob("*/README.md")):
        url = _service_url_from_doc(readme)
        host = urlparse(url).hostname if url and url != "-" else None
        if not host:
            continue
        service_id = readme.parent.name
        title = _service_title_from_doc(readme)
        entries.append(f"- `{host}` → [{title}]({service_id}/README.md)")
    lines = ["# HMN 域名索引", ""]
    lines.extend(entries or ["暂无域名。"])
    lines.append("")
    index_path = service_root / "domains.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def _markdown_title(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        return path.stem
    return path.stem.replace("-", " ").title()


def write_runbook_index(service_root: Path = DEFAULT_SERVICE_ROOT, runbook_root: Path | None = None) -> Path:
    service_root.mkdir(parents=True, exist_ok=True)
    root = runbook_root or (service_root / "runbooks")
    entries = []
    if root.exists():
        for runbook in sorted(root.glob("*.md")):
            title = _markdown_title(runbook)
            entries.append(f"- [{title}]({runbook.as_posix()})")
    lines = ["# HMN Runbook 索引", ""]
    lines.extend(entries or ["暂无 Runbook。"])
    lines.append("")
    index_path = service_root / "runbooks.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def generate_docs(
    store: SQLiteStore,
    output_root: Path = DEFAULT_DOCS_ROOT,
    service_root: Path = DEFAULT_SERVICE_ROOT,
    runbook_root: Path | None = None,
) -> DocsGenerateResult:
    paths = [write_server_doc(store, node.node_id, output_root) for node in store.list_nodes()]
    paths.append(write_server_index(store, output_root))
    service_index = write_service_index(service_root)
    domain_index = write_domain_index(service_root)
    runbook_index = write_runbook_index(service_root, runbook_root)
    paths.extend([service_index, domain_index, runbook_index])
    return DocsGenerateResult(
        server_count=len(paths) - 4,
        paths=paths,
        service_index=service_index,
        domain_index=domain_index,
        runbook_index=runbook_index,
    )
