from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .inventory import Node
from .storage import SQLiteStore


DEFAULT_DOCS_ROOT = Path("/srv/files/docs")


@dataclass(frozen=True)
class DocsGenerateResult:
    server_count: int
    paths: list[Path]


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


def generate_docs(store: SQLiteStore, output_root: Path = DEFAULT_DOCS_ROOT) -> DocsGenerateResult:
    paths = [write_server_doc(store, node.node_id, output_root) for node in store.list_nodes()]
    paths.append(write_server_index(store, output_root))
    return DocsGenerateResult(server_count=len(paths) - 1, paths=paths)
