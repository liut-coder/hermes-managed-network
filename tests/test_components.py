from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import replace

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.components import ComponentRegistry, load_builtin_components, load_component_manifest
from hermes_managed_network.storage import SQLiteStore
from hermes_managed_network.inventory import Node


def _save_managed_node(store: SQLiteStore, node_id: str = "node_component") -> None:
    store.save_node(
        Node(
            node_id=node_id,
            fingerprint="sha256:" + node_id,
            hostname=f"{node_id}-host",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )


def test_load_builtin_reverse_proxy_manifest():
    components = load_builtin_components()

    reverse_proxy = components["reverse-proxy"]

    assert reverse_proxy.id == "reverse-proxy"
    assert reverse_proxy.version == "0.1.0"
    assert reverse_proxy.api_version == 1
    assert reverse_proxy.risk == "medium"
    assert "network.bind" in reverse_proxy.requires["capabilities"]
    assert "reverse-proxy" in reverse_proxy.provides["services"]
    assert reverse_proxy.drivers["default"] == "caddy"
    assert reverse_proxy.audit["category"] == "component.reverse-proxy"


def test_load_builtin_forwarder_manifest():
    components = load_builtin_components()

    forwarder = components["forwarder"]

    assert forwarder.id == "forwarder"
    assert forwarder.version == "0.1.0"
    assert forwarder.api_version == 1
    assert forwarder.risk == "medium"
    assert "network.listen" in forwarder.requires["capabilities"]
    assert "network.connect" in forwarder.requires["capabilities"]
    assert "forwarder" in forwarder.provides["services"]
    assert forwarder.drivers["default"] == "gost"
    assert set(forwarder.drivers["options"]) >= {"gost", "frp", "socat", "nftables"}
    assert forwarder.config_schema["required"] == ["listen", "target"]
    assert forwarder.audit["category"] == "component.forwarder"


def test_load_builtin_monitor_manifest():
    components = load_builtin_components()

    monitor = components["monitor"]

    assert monitor.id == "monitor"
    assert monitor.version == "0.1.0"
    assert monitor.api_version == 1
    assert monitor.risk == "low"
    assert "metrics.read" in monitor.requires["capabilities"]
    assert "monitor" in monitor.provides["services"]
    assert monitor.drivers["default"] == "heartbeat"
    assert set(monitor.drivers["options"]) >= {"heartbeat", "node-exporter", "agent-metrics"}
    assert monitor.config_schema["required"] == []
    assert monitor.health["checks"][0]["type"] == "heartbeat"
    assert monitor.audit["category"] == "component.monitor"


def test_load_component_manifest_validates_required_fields(tmp_path):
    manifest = tmp_path / "component.yaml"
    manifest.write_text("id: broken\nname: Broken\n", encoding="utf-8")

    try:
        load_component_manifest(manifest)
    except ValueError as exc:
        assert "version" in str(exc)
        assert "api_version" in str(exc)
        assert "risk" in str(exc)
    else:
        raise AssertionError("invalid manifest should fail")


def _valid_manifest_text(**overrides):
    data = {
        "id": "example",
        "name": "Example",
        "version": "0.1.0",
        "api_version": 1,
        "summary": "Example component",
        "risk": "low",
        "requires": {"capabilities": ["network.bind"]},
        "provides": {"services": ["example"]},
        "config_schema": {"type": "object", "required": ["domain"], "properties": {"domain": {"type": "string"}}},
        "drivers": {"default": "caddy", "options": ["caddy", "nginx"]},
        "playbooks": {
            "install": "playbooks/install.yaml",
            "configure": "playbooks/configure.yaml",
            "verify": "playbooks/verify.yaml",
            "uninstall": "playbooks/uninstall.yaml",
        },
        "audit": {"category": "component.example"},
    }
    data.update(overrides)
    import yaml

    return yaml.safe_dump(data)


def test_component_manifest_rejects_invalid_risk_default_driver_and_missing_playbook(tmp_path):
    cases = [
        ({"risk": "extreme"}, "risk"),
        ({"drivers": {"default": "apache", "options": ["caddy", "nginx"]}}, "drivers.default"),
        ({"playbooks": {"install": "playbooks/install.yaml", "configure": "playbooks/configure.yaml", "verify": "playbooks/verify.yaml"}}, "playbooks.uninstall"),
    ]

    for idx, (overrides, expected) in enumerate(cases):
        manifest = tmp_path / f"component-{idx}.yaml"
        manifest.write_text(_valid_manifest_text(**overrides), encoding="utf-8")

        try:
            load_component_manifest(manifest)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"manifest with invalid {expected} should fail")


def test_component_manifest_requires_declared_config_properties_and_audit_category(tmp_path):
    cases = [
        ({"config_schema": {"type": "object", "required": ["domain"], "properties": {}}}, "config_schema.required"),
        ({"audit": {}}, "audit.category"),
    ]

    for idx, (overrides, expected) in enumerate(cases):
        manifest = tmp_path / f"component-schema-{idx}.yaml"
        manifest.write_text(_valid_manifest_text(**overrides), encoding="utf-8")

        try:
            load_component_manifest(manifest)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"manifest missing {expected} should fail")


def test_component_registry_lists_gets_and_validates_builtin_components():
    registry = ComponentRegistry.from_builtin()

    listed = registry.list()
    reverse_proxy = registry.get("reverse-proxy")

    assert [component.id for component in listed] == ["forwarder", "monitor", "reverse-proxy"]
    assert reverse_proxy.name == "Reverse Proxy"
    forwarder = registry.get("forwarder")
    assert forwarder.name == "Forwarder"
    monitor = registry.get("monitor")
    assert monitor.name == "Monitor"
    assert registry.validate("reverse-proxy") is reverse_proxy
    assert registry.validate("forwarder") is forwarder
    assert registry.validate("monitor") is monitor
    try:
        registry.get("missing")
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("missing component should fail")


def test_builtin_component_manifests_are_package_data():
    from importlib import resources

    reverse_proxy_manifest = resources.files("hermes_managed_network").joinpath("components", "reverse-proxy", "component.yaml")
    forwarder_manifest = resources.files("hermes_managed_network").joinpath("components", "forwarder", "component.yaml")
    monitor_manifest = resources.files("hermes_managed_network").joinpath("components", "monitor", "component.yaml")

    assert reverse_proxy_manifest.is_file()
    assert forwarder_manifest.is_file()
    assert monitor_manifest.is_file()


def test_store_can_register_components_and_node_status(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    component = load_builtin_components()["reverse-proxy"]
    _save_managed_node(store)

    store.save_component(component)
    store.set_node_component(
        node_id="node_component",
        component_id="reverse-proxy",
        desired_state="enabled",
        current_state="planned",
        config={"domain": "example.com", "upstream": "http://127.0.0.1:3000"},
        driver="caddy",
    )

    assert store.load_component("reverse-proxy").name == "Reverse Proxy"
    status = store.list_node_components("node_component")
    assert len(status) == 1
    assert status[0].component_id == "reverse-proxy"
    assert status[0].desired_state == "enabled"
    assert status[0].current_state == "planned"
    assert status[0].config["domain"] == "example.com"


def test_component_cli_lists_and_shows_builtin_components(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"

    listed = runner.invoke(app, ["component", "list", "--db", str(db)])
    shown = runner.invoke(app, ["component", "show", "reverse-proxy", "--db", str(db)])

    assert listed.exit_code == 0
    assert "reverse-proxy" in listed.stdout
    assert "Reverse Proxy" in listed.stdout
    assert "medium" in listed.stdout
    assert shown.exit_code == 0
    assert "component: reverse-proxy" in shown.stdout
    assert "driver: caddy" in shown.stdout
    assert "network.bind" in shown.stdout


def test_component_plan_is_non_mutating_and_records_run(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_plan")

    result = runner.invoke(
        app,
        [
            "component",
            "plan",
            "reverse-proxy",
            "--node",
            "node_plan",
            "--set",
            "domain=example.com",
            "--set",
            "upstream=http://127.0.0.1:3000",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code == 0
    assert "plan: reverse-proxy" in result.stdout
    assert "node: node_plan" in result.stdout
    assert "risk: medium" in result.stdout
    assert "mutating: no" in result.stdout
    assert "apply command:" in result.stdout
    assert store.list_node_components("node_plan") == []
    runs = store.list_component_runs()
    assert len(runs) == 1
    assert runs[0].action == "plan"
    assert runs[0].status == "planned"
    events = store.list_audit_events()
    assert events[-1].action == "plan"
    assert events[-1].outcome == "planned"


def test_component_apply_updates_state_without_touching_machine_and_records_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_apply")

    result = runner.invoke(
        app,
        [
            "component",
            "apply",
            "reverse-proxy",
            "--node",
            "node_apply",
            "--set",
            "domain=example.com",
            "--set",
            "upstream=http://127.0.0.1:3000",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code == 0
    assert "apply: reverse-proxy" in result.stdout
    assert "machine_changed: no" in result.stdout
    assert "state: enabled/planned" in result.stdout
    item = store.list_node_components("node_apply")[0]
    assert item.desired_state == "enabled"
    assert item.current_state == "planned"
    assert item.config["domain"] == "example.com"
    runs = store.list_component_runs()
    assert runs[0].action == "apply"
    assert runs[0].status == "state_recorded"
    assert runs[0].result["machine_changed"] is False
    events = store.list_audit_events()
    assert events[-1].action == "apply"
    assert events[-1].outcome == "state_recorded"


def test_high_risk_component_apply_requires_approval_and_dispatches_after_approve(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_high_apply")
    high_component = replace(load_builtin_components()["reverse-proxy"], id="danger-proxy", risk="high")
    store.save_component(high_component)

    result = runner.invoke(
        app,
        [
            "component",
            "apply",
            "danger-proxy",
            "--node",
            "node_high_apply",
            "--set",
            "domain=example.com",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code == 1
    assert "需要审批:" in result.stdout
    assert store.list_node_components("node_high_apply") == []
    runs = store.list_component_runs()
    assert len(runs) == 1
    assert runs[0].action == "apply"
    assert runs[0].status == "pending_approval"
    approvals = store.list_approval_requests(status="pending")
    assert len(approvals) == 1
    assert approvals[0].subject_type == "component_run"
    assert approvals[0].subject_id == runs[0].run_id
    assert approvals[0].action == "component.apply"

    approved = runner.invoke(app, ["approval", "approve", approvals[0].approval_id, "--db", str(db)])

    assert approved.exit_code == 0
    item = store.list_node_components("node_high_apply")[0]
    assert item.component_id == "danger-proxy"
    assert item.desired_state == "enabled"
    assert item.current_state == "planned"
    assert item.last_run_id == runs[0].run_id
    dispatched_run = store.list_component_runs()[0]
    assert dispatched_run.run_id == runs[0].run_id
    assert dispatched_run.status == "state_recorded"
    assert dispatched_run.result["approval_id"] == approvals[0].approval_id


def test_dispatch_approved_component_apply_is_idempotent_under_repeated_calls(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    run = store.record_component_run(
        component_id="danger-proxy",
        node_id="node_repeat_component",
        action="apply",
        risk="high",
        status="pending_approval",
        plan={"config": {"domain": "example.com"}},
    )
    approval = store.create_approval_request(
        subject_type="component_run",
        subject_id=run.run_id,
        action="component.apply",
        risk="high",
        requested_by="hmn",
        details={
            "component_id": "danger-proxy",
            "node_id": "node_repeat_component",
            "config": {"domain": "example.com"},
            "version": "1.2.3",
            "driver": "caddy",
        },
    )
    store.resolve_approval_request(approval.approval_id, status="approved", decided_by="Misk")

    def dispatch_again():
        return SQLiteStore(db).dispatch_approved_component_apply(approval.approval_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        dispatched = list(executor.map(lambda _: dispatch_again(), range(16)))

    runs = SQLiteStore(db).list_component_runs()
    assert len(runs) == 1
    assert runs[0].status == "state_recorded"
    assert {run.run_id for run in dispatched if run is not None} == {runs[0].run_id}
    items = SQLiteStore(db).list_node_components("node_repeat_component")
    assert len(items) == 1
    assert items[0].component_id == "danger-proxy"
    assert items[0].last_run_id == run.run_id
    events = SQLiteStore(db).list_audit_events()
    assert [event.action for event in events].count("apply") == 2  # original pending run + one approved dispatch
    assert [event.outcome for event in events].count("state_recorded") == 1


def test_dispatch_approved_component_apply_returns_existing_state_recorded_run_without_side_effects(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    run = store.record_component_run(
        component_id="danger-proxy",
        node_id="node_recorded_component",
        action="apply",
        risk="high",
        status="state_recorded",
        result={"already": True},
    )
    approval = store.create_approval_request(
        subject_type="component_run",
        subject_id=run.run_id,
        action="component.apply",
        risk="high",
        requested_by="hmn",
        details={"component_id": "danger-proxy", "node_id": "node_recorded_component"},
    )
    store.resolve_approval_request(approval.approval_id, status="approved", decided_by="Misk")
    before_events = SQLiteStore(db).list_audit_events()

    dispatched = SQLiteStore(db).dispatch_approved_component_apply(approval.approval_id)

    assert dispatched is not None
    assert dispatched.run_id == run.run_id
    assert dispatched.status == "state_recorded"
    assert dispatched.result == {"already": True}
    assert SQLiteStore(db).list_node_components("node_recorded_component") == []
    assert SQLiteStore(db).list_audit_events() == before_events


def test_component_verify_is_independent_from_apply_and_records_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_verify")

    result = runner.invoke(app, ["component", "verify", "reverse-proxy", "--node", "node_verify", "--db", str(db)])

    assert result.exit_code == 0
    assert "verify: reverse-proxy" in result.stdout
    assert "independent: yes" in result.stdout
    assert "remote_check: not_enabled" in result.stdout
    assert store.list_node_components("node_verify") == []
    runs = store.list_component_runs()
    assert runs[0].action == "verify"
    assert runs[0].status == "checked"
    assert runs[0].result["independent_from_apply"] is True
    events = store.list_audit_events()
    assert events[-1].action == "verify"
    assert events[-1].outcome == "checked"


def test_component_verify_uses_network_ip_as_overlay_probe_target(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_verify_overlay",
            fingerprint="sha256:node_verify_overlay",
            hostname="node-verify-overlay-host",
            addresses=["192.0.2.50"],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
            network_provider="headscale",
            network_node_id="50",
            network_ip="100.64.0.50",
            network_online=True,
        )
    )

    result = runner.invoke(app, ["component", "verify", "reverse-proxy", "--node", "node_verify_overlay", "--db", str(db)])

    assert result.exit_code == 0
    assert "remote_check: overlay_network" in result.stdout
    assert "probe_target: 100.64.0.50" in result.stdout
    assert "target_source: network_ip" in result.stdout
    runs = SQLiteStore(db).list_component_runs()
    assert runs[0].result["probe_target"] == "100.64.0.50"
    assert runs[0].result["target_source"] == "network_ip"
    assert runs[0].result["network_provider"] == "headscale"
    events = SQLiteStore(db).list_audit_events()
    assert events[-1].details["probe_target"] == "100.64.0.50"
    assert events[-1].details["target_source"] == "network_ip"


def test_component_uninstall_is_first_class_and_records_state_and_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_uninstall")
    store.set_node_component(
        node_id="node_uninstall",
        component_id="reverse-proxy",
        desired_state="enabled",
        current_state="planned",
        config={"domain": "example.com"},
        driver="caddy",
    )

    result = runner.invoke(app, ["component", "uninstall", "reverse-proxy", "--node", "node_uninstall", "--db", str(db)])

    assert result.exit_code == 0
    assert "uninstall: reverse-proxy" in result.stdout
    assert "machine_changed: no" in result.stdout
    assert "state: absent/planned" in result.stdout
    item = store.list_node_components("node_uninstall")[0]
    assert item.desired_state == "absent"
    assert item.current_state == "planned"
    runs = store.list_component_runs()
    assert runs[0].action == "uninstall"
    assert runs[0].status == "state_recorded"
    events = store.list_audit_events()
    assert events[-1].action == "uninstall"
    assert events[-1].outcome == "state_recorded"


def test_component_status_reports_node_components(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    component = load_builtin_components()["reverse-proxy"]
    store.save_component(component)
    _save_managed_node(store, "node_status")
    store.set_node_component(
        node_id="node_status",
        component_id="reverse-proxy",
        desired_state="enabled",
        current_state="planned",
        config={"domain": "example.com"},
        driver="caddy",
    )

    result = runner.invoke(app, ["component", "status", "--node", "node_status", "--db", str(db)])

    assert result.exit_code == 0
    assert "node: node_status" in result.stdout
    assert "reverse-proxy" in result.stdout
    assert "desired=enabled" in result.stdout
    assert "current=planned" in result.stdout


def test_monitor_component_status_summarizes_latest_heartbeat(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_monitor_status")
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_monitor_status",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_protocol_version": "0.1",
                "worker_version": "0.1.0",
                "exec_enabled": False,
                "uptime": {"seconds": 259200},
                "load_average": {"1m": "0.01", "5m": "0.05", "15m": "0.10"},
                "memory": {"total_kb": 1024, "available_kb": 512, "free_kb": 256},
                "disk": {"path": "/", "total_bytes": 1000, "used_bytes": 400, "free_bytes": 600},
            },
            "worker_compatible": True,
        },
    )

    result = runner.invoke(app, ["component", "status", "--node", "node_monitor_status", "--db", str(db)])

    assert result.exit_code == 0
    assert "monitor:" in result.stdout
    assert "heartbeat=OK" in result.stdout
    assert "worker_protocol=0.1" in result.stdout
    assert "worker_version=0.1.0" in result.stdout
    assert "exec=SAFE" in result.stdout
    assert "uptime_seconds=259200" in result.stdout
    assert "load_average=0.01/0.05/0.10" in result.stdout
    assert "memory_kb total=1024 available=512 free=256" in result.stdout
    assert "disk / total=1000 used=400 free=600" in result.stdout


def test_monitor_component_verify_uses_heartbeat_audit_and_records_result(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_monitor_verify")
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_monitor_verify",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )

    result = runner.invoke(app, ["component", "verify", "monitor", "--node", "node_monitor_verify", "--db", str(db)])

    assert result.exit_code == 0
    assert "verify: monitor" in result.stdout
    assert "heartbeat: OK" in result.stdout
    assert "remote_check: heartbeat_audit" in result.stdout
    runs = store.list_component_runs()
    assert runs[-1].component_id == "monitor"
    assert runs[-1].status == "ok"
    assert runs[-1].result["heartbeat_seen"] is True
    assert runs[-1].result["worker_compatible"] is True
    events = store.list_audit_events()
    assert events[-1].event_type == "component.monitor"
    assert events[-1].action == "verify"
    assert events[-1].outcome == "ok"


def test_monitor_component_verify_warns_without_heartbeat(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    _save_managed_node(SQLiteStore(db), "node_monitor_missing")

    result = runner.invoke(app, ["component", "verify", "monitor", "--node", "node_monitor_missing", "--db", str(db)])

    assert result.exit_code == 1
    assert "heartbeat: WARN missing" in result.stdout
