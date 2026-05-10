from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def test_storage_roundtrips_node_network_fields(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_net",
            fingerprint="sha256:net",
            hostname="net-node",
            addresses=["192.0.2.10"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.10",
            ssh_user="ops",
            ssh_port=2222,
            network_provider="headscale",
            network_node_id="123",
            network_ip="100.64.0.10",
            network_tags=["tag:worker", "tag:ssh"],
            network_online=True,
        )
    )

    loaded = SQLiteStore(db).load_node("node_net")

    assert loaded == Node(
        node_id="node_net",
        fingerprint="sha256:net",
        hostname="net-node",
        addresses=["192.0.2.10"],
        trust_level="B",
        labels=["worker"],
        status="managed",
        permission_bundles=["observe"],
        ssh_host="100.64.0.10",
        ssh_user="ops",
        ssh_port=2222,
        network_provider="headscale",
        network_node_id="123",
        network_ip="100.64.0.10",
        network_tags=["tag:worker", "tag:ssh"],
        network_online=True,
    )
