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


def test_network_node_tags_set_creates_approval_without_provider_write(tmp_path, monkeypatch):
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
            network_provider="headscale",
            network_node_id="123",
            network_ip="100.64.0.10",
            network_tags=["tag:old"],
            network_online=True,
        )
    )

    def fake_request(self, method, path, payload=None):
        raise AssertionError("tag write must not reach provider before approval")

    monkeypatch.setattr("hermes_managed_network.network_base.JsonHttpClient.request_json", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "network",
            "node",
            "tags",
            "set",
            "--db",
            str(db),
            "--node",
            "node_worker",
            "--tag",
            "tag:web",
            "--tag",
            "tag:ssh",
        ],
    )

    assert result.exit_code != 0
    assert "需要审批" in result.stdout
    approvals = SQLiteStore(db).list_approval_requests(status="pending")
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.subject_type == "network_node"
    assert approval.subject_id == "node_worker"
    assert approval.action == "network.tags.set"
    assert approval.risk == "high"
    assert approval.details["node_id"] == "node_worker"
    assert approval.details["provider_node_id"] == "123"
    assert approval.details["old_tags"] == ["tag:old"]
    assert approval.details["requested_tags"] == ["tag:web", "tag:ssh"]
    assert SQLiteStore(db).load_node("node_worker").network_tags == ["tag:old"]


def test_approval_approve_dispatches_network_tag_update(tmp_path, monkeypatch):
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
            network_provider="headscale",
            network_node_id="123",
            network_ip="100.64.0.10",
            network_tags=["tag:old"],
            network_online=True,
        )
    )
    approval = store.create_approval_request(
        subject_type="network_node",
        subject_id="node_worker",
        action="network.tags.set",
        risk="high",
        requested_by="hmn",
        details={
            "node_id": "node_worker",
            "provider": "headscale",
            "provider_node_id": "123",
            "old_tags": ["tag:old"],
            "requested_tags": ["tag:web"],
        },
    )
    captured = {}

    def fake_request(self, method, path, payload=None):
        captured.update({"method": method, "path": path, "payload": payload})
        return {"node": {"id": 123, "givenName": "worker-node", "ipAddresses": ["100.64.0.10"], "forcedTags": ["tag:web"], "online": True}}

    monkeypatch.setattr("hermes_managed_network.network_base.JsonHttpClient.request_json", fake_request)

    result = CliRunner().invoke(app, ["approval", "approve", approval.approval_id, "--db", str(db), "--by", "Misk"])

    assert result.exit_code == 0
    assert "已更新网络 tags" in result.stdout
    assert captured == {"method": "POST", "path": "/api/v1/node/123/tags", "payload": {"tags": ["tag:web"]}}
    node = SQLiteStore(db).load_node("node_worker")
    assert node.network_tags == ["tag:web"]
    events = SQLiteStore(db).list_audit_events()
    tag_event = [event for event in events if event.event_type == "network" and event.action == "tags/update"][-1]
    assert tag_event.outcome == "ok"
    assert tag_event.details["node_id"] == "node_worker"
    assert tag_event.details["provider_node_id"] == "123"
    assert tag_event.details["old_tags"] == ["tag:old"]
    assert tag_event.details["requested_tags"] == ["tag:web"]
    assert tag_event.details["approval_id"] == approval.approval_id


def test_network_acl_plan_creates_approval_with_diff_without_writing(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    current_acl = tmp_path / "acl.hujson"
    proposed_acl = tmp_path / "acl.next.hujson"
    config = tmp_path / "config.yaml"
    _write_config(config)
    monkeypatch.setenv("HMN_CONFIG", str(config))
    current_acl.write_text('{"groups": {"group:admin": ["misk"]}}\n', encoding="utf-8")
    proposed_acl.write_text('{"groups": {"group:admin": ["misk", "bot"]}}\n', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "network",
            "acl",
            "plan",
            "--db",
            str(db),
            "--current",
            str(current_acl),
            "--proposed",
            str(proposed_acl),
        ],
    )

    assert result.exit_code != 0
    assert "需要审批" in result.stdout
    assert current_acl.read_text(encoding="utf-8") == '{"groups": {"group:admin": ["misk"]}}\n'
    approval = SQLiteStore(db).list_approval_requests(status="pending")[0]
    assert approval.subject_type == "network_acl"
    assert approval.subject_id == str(current_acl)
    assert approval.action == "network.acl.apply"
    assert approval.risk == "critical"
    assert approval.details["current_path"] == str(current_acl)
    assert approval.details["proposed_path"] == str(proposed_acl)
    assert approval.details["old_sha256"] != approval.details["new_sha256"]
    assert "-{" in approval.details["diff"]
    assert "+{" in approval.details["diff"]


def test_approval_approve_dispatches_network_acl_apply(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    current_acl = tmp_path / "acl.hujson"
    proposed_acl = tmp_path / "acl.next.hujson"
    config = tmp_path / "config.yaml"
    _write_config(config)
    monkeypatch.setenv("HMN_CONFIG", str(config))
    current_acl.write_text('{"groups": {"group:admin": ["misk"]}}\n', encoding="utf-8")
    proposed_acl.write_text('{"groups": {"group:admin": ["misk", "bot"]}}\n', encoding="utf-8")
    plan_result = CliRunner().invoke(
        app,
        [
            "network",
            "acl",
            "plan",
            "--db",
            str(db),
            "--current",
            str(current_acl),
            "--proposed",
            str(proposed_acl),
            "--reload-command",
            "printf reloaded > reload.txt",
            "--verify-command",
            "test -f acl.hujson && grep -q bot acl.hujson",
        ],
    )
    assert plan_result.exit_code != 0
    approval = SQLiteStore(db).list_approval_requests(status="pending")[0]

    result = CliRunner().invoke(app, ["approval", "approve", approval.approval_id, "--db", str(db), "--by", "Misk"])

    assert result.exit_code == 0
    assert "已应用 Headscale ACL" in result.stdout
    assert current_acl.read_text(encoding="utf-8") == proposed_acl.read_text(encoding="utf-8")
    assert (tmp_path / "reload.txt").read_text(encoding="utf-8") == "reloaded"
    events = SQLiteStore(db).list_audit_events()
    acl_event = [event for event in events if event.event_type == "network" and event.action == "acl/apply"][-1]
    assert acl_event.outcome == "ok"
    assert acl_event.details["approval_id"] == approval.approval_id
    assert acl_event.details["current_path"] == str(current_acl)
    assert acl_event.details["verify_exit_code"] == 0
    assert acl_event.details["reload_exit_code"] == 0
