import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def _write_config(path):
    path.write_text(
        """network:\n  provider: headscale\n  headscale:\n    url: https://headscale.example\n    api_key_env: HEADSCALE_API_KEY\n    user: misk\n""",
        encoding="utf-8",
    )


def test_network_status_reports_headscale_nodes(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    _write_config(config)
    monkeypatch.setenv("HMN_CONFIG", str(config))
    monkeypatch.setenv("HEADSCALE_API_KEY", "test-token")

    calls = []

    def fake_request(self, method, path, payload=None):
        calls.append((method, path, payload))
        return {
            "nodes": [
                {
                    "id": 123,
                    "givenName": "worker-node",
                    "ipAddresses": ["100.64.0.10"],
                    "forcedTags": ["tag:worker"],
                    "online": True,
                }
            ]
        }

    monkeypatch.setattr("hermes_managed_network.network_base.JsonHttpClient.request_json", fake_request)

    result = CliRunner().invoke(app, ["network", "status"])

    assert result.exit_code == 0
    assert calls == [("GET", "/api/v1/node?user=misk", None)]
    assert "provider: headscale" in result.stdout
    assert "nodes: 1" in result.stdout
    assert "online: 1" in result.stdout
    assert "worker-node" in result.stdout
    assert "100.64.0.10" in result.stdout


def test_network_preauth_key_create_uses_headscale_api(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    _write_config(config)
    monkeypatch.setenv("HMN_CONFIG", str(config))
    monkeypatch.setenv("HEADSCALE_API_KEY", "test-token")
    captured = {}

    def fake_request(self, method, path, payload=None):
        captured.update({"method": method, "path": path, "payload": payload})
        return {"preAuthKey": {"key": "hskey_abc", "aclTags": ["tag:worker"], "reusable": False, "ephemeral": True}}

    monkeypatch.setattr("hermes_managed_network.network_base.JsonHttpClient.request_json", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "network",
            "preauth-key",
            "create",
            "--node",
            "node1",
            "--tag",
            "tag:worker",
            "--ephemeral",
        ],
    )

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/preauthkey"
    assert captured["payload"]["user"] == "misk"
    assert captured["payload"]["aclTags"] == ["tag:worker"]
    assert captured["payload"]["ephemeral"] is True
    assert "hskey_abc" in result.stdout


def test_network_sync_updates_hmn_nodes_and_records_audit(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    config = tmp_path / "config.yaml"
    _write_config(config)
    monkeypatch.setenv("HMN_CONFIG", str(config))
    monkeypatch.setenv("HEADSCALE_API_KEY", "test-token")
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_worker",
            fingerprint="fp",
            hostname="worker-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
        )
    )

    def fake_request(self, method, path, payload=None):
        return {
            "nodes": [
                {
                    "id": 123,
                    "givenName": "worker-node",
                    "ipAddresses": ["100.64.0.10"],
                    "forcedTags": ["tag:worker"],
                    "online": True,
                },
                {"id": 456, "givenName": "stray", "ipAddresses": ["100.64.0.11"], "online": False},
            ]
        }

    monkeypatch.setattr("hermes_managed_network.network_base.JsonHttpClient.request_json", fake_request)

    result = CliRunner().invoke(app, ["network", "sync", "--db", str(db)])

    assert result.exit_code == 0
    assert "linked: 1" in result.stdout
    assert "updated: 1" in result.stdout
    assert "unmatched: stray" in result.stdout
    node = SQLiteStore(db).load_node("node_worker")
    assert node.network_provider == "headscale"
    assert node.network_node_id == "123"
    assert node.network_ip == "100.64.0.10"
    assert node.network_tags == ["tag:worker"]
    assert node.network_online is True
    event = SQLiteStore(db).list_audit_events()[-1]
    assert event.event_type == "network"
    assert event.action == "sync"
    assert event.details["linked"] == 1
    assert event.details["updated"] == 1
    assert event.details["unmatched"] == ["stray"]


def test_wake_network_headscale_prints_preauth_key_and_join_command(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    config = tmp_path / "config.yaml"
    _write_config(config)
    monkeypatch.setenv("HMN_CONFIG", str(config))
    monkeypatch.setenv("HEADSCALE_API_KEY", "test-token")

    def fake_request(self, method, path, payload=None):
        assert method == "POST"
        assert path == "/api/v1/preauthkey"
        assert payload["aclTags"] == ["tag:worker"]
        return {"preAuthKey": {"key": "hskey_join", "aclTags": ["tag:worker"]}}

    monkeypatch.setattr("hermes_managed_network.network_base.JsonHttpClient.request_json", fake_request)

    result = CliRunner().invoke(
        app,
        ["wake", "--db", str(db), "--network", "headscale"],
        input="worker-node\n\nhttp://master.internal:8765\n\nworker\nhermes\n30\ntag:worker\n",
    )

    assert result.exit_code == 0
    assert "Headscale 接入 key 已生成" in result.stdout
    assert "hskey_join" in result.stdout
    assert "tailscale up --login-server=https://headscale.example --authkey=hskey_join" in result.stdout
    assert "HERMES_JOIN_TOKEN=" in result.stdout
