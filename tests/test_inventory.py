from hermes_managed_network.inventory import NodeRegistry


def test_register_pending_node_from_consumed_join_token():
    registry = NodeRegistry()

    node = registry.register_pending(
        node_id="node-1",
        fingerprint="sha256:abc",
        hostname="demo-server",
        addresses=["100.64.0.10"],
        trust_level="B",
        labels=["managed"],
    )

    assert node.node_id == "node-1"
    assert node.status == "pending"
    assert node.hostname == "demo-server"
    assert node.trust_level == "B"
    assert registry.get("node-1") == node


def test_confirm_node_marks_it_managed_and_assigns_permissions():
    registry = NodeRegistry()
    registry.register_pending(
        node_id="node-1",
        fingerprint="sha256:abc",
        hostname="demo-server",
        addresses=[],
        trust_level="A",
        labels=[],
    )

    node = registry.confirm("node-1", permission_bundles=["observe"])

    assert node is not None
    assert node.status == "managed"
    assert node.permission_bundles == ["observe"]


def test_revoked_node_cannot_be_confirmed():
    registry = NodeRegistry()
    registry.register_pending(
        node_id="node-1",
        fingerprint="sha256:abc",
        hostname="demo-server",
        addresses=[],
        trust_level="A",
        labels=[],
    )

    registry.revoke("node-1")

    assert registry.confirm("node-1", permission_bundles=["observe"]) is None
    assert registry.get("node-1").status == "revoked"
