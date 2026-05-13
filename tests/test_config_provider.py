from __future__ import annotations

import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore, ServiceRecord
from hermes_managed_network.config_provider import plan_config_inventory_export


def test_plan_config_inventory_export_groups_nodes_and_services_and_is_json_serializable():
    node = Node(
        node_id="node-brarm",
        fingerprint="fp-1",
        hostname="brarm",
        addresses=["10.0.0.10", "100.64.0.10"],
        trust_level="managed",
        labels=["region:hk", "role:web"],
        status="managed",
        ssh_host="brarm.example.invalid",
        ssh_user="misk",
        ssh_port=46222,
    )
    services = [
        ServiceRecord(
            service_id="svc_web",
            name="Web",
            node_id="node-brarm",
            kind="docker",
            runtime="compose",
            domains=["web.example.invalid"],
            ports=[80, 443],
            source="manual",
            status="active",
            metadata={"image": "nginx:latest", "env": {"APP_ENV": "prod", "TOKEN": "hidden"}},
        ),
        ServiceRecord(
            service_id="svc_worker",
            name="Worker",
            node_id="node-brarm",
            kind="systemd",
            runtime="systemd",
            source="manual",
            status="active",
        ),
    ]

    plan = plan_config_inventory_export(nodes=[node], services=services)
    payload = json.loads(json.dumps(plan))

    assert payload["provider_id"] == "config-provider"
    assert payload["operation"] == "inventory_export_plan"
    assert payload["dry_run"] is True
    assert payload["approval_required"] is False
    assert payload["capabilities"]["apply"]["status"] == "not_enabled"
    assert payload["inventory"]["by_node"]["node-brarm"]["host"] == "brarm.example.invalid"
    assert payload["inventory"]["by_node"]["node-brarm"]["ssh_user"] == "misk"
    assert payload["inventory"]["by_node"]["node-brarm"]["ssh_port"] == 46222
    assert payload["inventory"]["by_node"]["node-brarm"]["labels"] == ["region:hk", "role:web"]
    assert payload["inventory"]["by_node"]["node-brarm"]["services"] == ["svc_web", "svc_worker"]
    assert payload["inventory"]["by_service"]["svc_web"]["nodes"] == ["node-brarm"]
    assert payload["inventory"]["by_service"]["svc_web"]["service"]["ports"] == [80, 443]
    assert payload["inventory"]["by_service"]["svc_web"]["service"]["domains"] == ["web.example.invalid"]


def test_plan_config_inventory_export_redacts_or_omits_secret_like_fields():
    node = Node(
        node_id="node-secret",
        fingerprint="fp-secret",
        hostname="secret-host",
        addresses=["10.0.0.20"],
        trust_level="managed",
        labels=["role:db"],
        status="managed",
    )
    services = [
        ServiceRecord(
            service_id="svc_db",
            name="DB",
            node_id="node-secret",
            kind="docker",
            runtime="compose",
            env_paths=["/srv/db/.env"],
            config_paths=["/srv/db/compose.yml"],
            data_paths=["/srv/db/data"],
            source="manual",
            status="active",
            metadata={
                "api_key": "very-secret",
                "env": {"PASSWORD": "super-secret", "SAFE_NAME": "visible"},
                "notes": "token=abc123 safe=ok",
            },
        )
    ]

    plan = plan_config_inventory_export(nodes=[node], services=services)
    service_payload = plan["inventory"]["by_service"]["svc_db"]["service"]

    assert service_payload["metadata"]["api_key"] == "[REDACTED]"
    assert service_payload["metadata"]["env"]["PASSWORD"] == "[REDACTED]"
    assert service_payload["metadata"]["env"]["SAFE_NAME"] == "visible"
    assert service_payload["metadata"]["notes"] == "[REDACTED] safe=ok"
    assert "env_paths" not in service_payload
    assert "config_paths" not in service_payload
    assert "data_paths" not in service_payload


def test_config_provider_inventory_plan_cli_reads_db_and_prints_json(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node-1",
            fingerprint="fp-1",
            hostname="node1",
            addresses=["10.0.0.1"],
            trust_level="managed",
            labels=["env:test"],
            status="managed",
            ssh_host="node1.example.invalid",
            ssh_user="root",
            ssh_port=2201,
        )
    )
    store.save_service_record(
        ServiceRecord(
            service_id="svc_api",
            name="API",
            node_id="node-1",
            kind="docker",
            runtime="compose",
            ports=[8080],
            source="manual",
            status="active",
            metadata={"secret_token": "nope", "safe": "ok"},
        )
    )

    result = CliRunner().invoke(app, ["config-provider", "inventory", "plan", "--db", str(db)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["inventory"]["by_node"]["node-1"]["host"] == "node1.example.invalid"
    assert payload["inventory"]["by_service"]["svc_api"]["service"]["metadata"]["secret_token"] == "[REDACTED]"
    assert payload["inventory"]["by_service"]["svc_api"]["service"]["metadata"]["safe"] == "ok"



def test_config_provider_playbook_apply_requires_approval_and_writes_audit_only(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node-apply",
            fingerprint="fp-apply",
            hostname="node-apply",
            addresses=["10.0.0.2"],
            trust_level="managed",
            labels=["env:prod", "role:web"],
            status="managed",
            ssh_host="node-apply.example.invalid",
            ssh_user="ops",
            ssh_port=2222,
        )
    )
    store.save_service_record(
        ServiceRecord(
            service_id="svc_web_apply",
            name="Web Apply",
            node_id="node-apply",
            kind="docker",
            runtime="compose",
            domains=["apply.example.invalid"],
            ports=[80],
            source="manual",
            status="active",
            metadata={"safe": "ok", "token": "blocked"},
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "config-provider",
            "playbook",
            "apply",
            "deploy-web.yml",
            "--db",
            str(db),
            "--limit-node",
            "node-apply",
            "--tag",
            "deploy",
            "--extra-var",
            "release=2026.05.13",
            "--request-by",
            "cron",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    approvals = SQLiteStore(db).list_approval_requests()
    audit_events = SQLiteStore(db).list_audit_events()

    assert payload["operation"] == "playbook_apply_request"
    assert payload["provider_id"] == "config-provider"
    assert payload["dry_run"] is True
    assert payload["approval_required"] is True
    assert payload["execution"]["not_executed"] is True
    assert payload["execution"]["external_writes_blocked"] is True
    assert payload["playbook"]["name"] == "deploy-web.yml"
    assert payload["inventory"]["node_count"] == 1
    assert payload["inventory"]["service_count"] == 1
    assert payload["approval_id"].startswith("appr_")
    assert len(approvals) == 1
    assert approvals[0].action == "config_provider.playbook_apply"
    assert approvals[0].risk == "high"
    assert approvals[0].details["playbook"]["name"] == "deploy-web.yml"
    assert approvals[0].details["execution"]["external_writes_blocked"] is True
    assert any(event.action == "approval/request" for event in audit_events)



def test_config_provider_playbook_apply_inventory_export_contains_redacted_hosts_and_targets(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node-a",
            fingerprint="fp-a",
            hostname="host-a",
            addresses=["10.0.0.11"],
            trust_level="managed",
            labels=["role:web"],
            status="managed",
            ssh_host="host-a.example.invalid",
            ssh_user="root",
            ssh_port=22,
        )
    )
    store.save_node(
        Node(
            node_id="node-b",
            fingerprint="fp-b",
            hostname="host-b",
            addresses=["10.0.0.12"],
            trust_level="managed",
            labels=["role:worker"],
            status="managed",
            ssh_host="host-b.example.invalid",
            ssh_user="ops",
            ssh_port=2202,
        )
    )
    store.save_service_record(
        ServiceRecord(
            service_id="svc_worker_b",
            name="Worker B",
            node_id="node-b",
            kind="systemd",
            runtime="systemd",
            source="manual",
            status="active",
            metadata={"api_key": "very-secret", "safe": "ok"},
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "config-provider",
            "playbook",
            "apply",
            "site.yml",
            "--db",
            str(db),
            "--limit-node",
            "node-b",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)

    assert payload["inventory"]["node_count"] == 1
    assert list(payload["inventory"]["inventory"]["by_node"].keys()) == ["node-b"]
    assert list(payload["inventory"]["inventory"]["by_service"].keys()) == ["svc_worker_b"]
    assert payload["inventory"]["inventory"]["by_service"]["svc_worker_b"]["service"]["metadata"]["api_key"] == "[REDACTED]"
    assert payload["inventory"]["inventory"]["by_service"]["svc_worker_b"]["service"]["metadata"]["safe"] == "ok"
