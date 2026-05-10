from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    node_id: str
    fingerprint: str
    hostname: str
    addresses: list[str]
    trust_level: str
    labels: list[str]
    status: str = "pending"
    permission_bundles: list[str] = field(default_factory=list)
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_port: int = 22


class NodeRegistry:
    """In-memory node registry for the MVP control plane."""

    def __init__(self, nodes: dict[str, Node] | None = None) -> None:
        self._nodes: dict[str, Node] = dict(nodes or {})

    def register_pending(
        self,
        *,
        node_id: str,
        fingerprint: str,
        hostname: str,
        addresses: list[str],
        trust_level: str,
        labels: list[str],
    ) -> Node:
        node = Node(
            node_id=node_id,
            fingerprint=fingerprint,
            hostname=hostname,
            addresses=list(addresses),
            trust_level=trust_level,
            labels=list(labels),
        )
        self._nodes[node_id] = node
        return node

    def get(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def confirm(self, node_id: str, *, permission_bundles: list[str]) -> Node | None:
        node = self.get(node_id)
        if node is None or node.status == "revoked":
            return None
        node.status = "managed"
        node.permission_bundles = list(permission_bundles)
        return node

    def revoke(self, node_id: str) -> Node | None:
        node = self.get(node_id)
        if node is None:
            return None
        node.status = "revoked"
        return node
