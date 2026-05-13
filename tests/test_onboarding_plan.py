import json
from pathlib import Path

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.service_registry import ServiceRecord, ServiceRegistry

runner = CliRunner()


def _write_nodes(tmp_path: Path) -> Path:
    nodes = [
        {
            "node": "node-full-01",
            "hostname": "full-01",
            "os_release": "Ubuntu 24.04",
            "arch": "x86_64",
            "cpu": {"cores": 8},
            "memory": {"total_mb": 16384},
            "disk": {"free_gb": 320},
            "network": {"egress": "public", "latency_tier": "good"},
            "ssh": {"port": 2222, "user": "root", "password": "super-secret"},
            "service_manager": "systemd",
            "docker": {"available": True},
            "systemd": {"available": True},
        },
        {
            "node": "node-lite-01",
            "hostname": "lite-01",
            "os_release": "OpenWrt 23.05",
            "arch": "mips",
            "cpu": {"cores": 2},
            "memory": {"total_mb": 512},
            "disk": {"free_gb": 6},
            "network": {"egress": "nat", "latency_tier": "fair"},
            "ssh": {"port": 22},
            "service_manager": "cron",
            "docker": {"available": False},
            "systemd": {"available": False},
        },
        {
            "node": "node-beacon-01",
            "hostname": "beacon-01",
            "os_release": "BusyBox",
            "arch": "armv7",
            "cpu": {"cores": 1},
            "memory": {"total_mb": 128},
            "disk": {"free_gb": 1},
            "network": {"egress": "unknown"},
            "ssh": {"port": 22},
            "service_manager": "none",
            "docker": {"available": False},
            "systemd": {"available": False},
        },
        {
            "node": "node-proxy-01",
            "hostname": "proxy-01",
            "os_release": "Unknown appliance",
            "arch": "unknown",
            "cpu": {"cores": 1},
            "memory": {"total_mb": 64},
            "disk": {"free_gb": 0.5},
            "network": {"egress": "unknown"},
            "ssh": {"port": 22, "token": "abc123"},
            "service_manager": "none",
            "docker": {"available": False},
            "systemd": {"available": False},
        },
    ]
    path = tmp_path / "nodes.json"
    path.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_service_registry(tmp_path: Path) -> Path:
    registry = ServiceRegistry(
        [
            ServiceRecord(
                service_id="svc-heavy",
                name="heavy",
                node="old-node",
                kind="docker",
                domains=["heavy.example.com"],
                ports=[443],
                runtime="ghcr.io/example/heavy:latest",
                source="discovery token=very-secret",
                docs_path="service/heavy.md",
                monitor={},
                warnings=[],
            ),
            ServiceRecord(
                service_id="svc-lite",
                name="lite",
                node="old-node",
                kind="systemd",
                domains=["lite.example.com"],
                ports=[8080],
                runtime="lite.service",
                source="discovery password=hunter2",
                docs_path="service/lite.md",
                monitor={},
                warnings=[],
            ),
        ]
    )
    path = tmp_path / "service-registry.json"
    registry.save(path)
    return path


def test_nodes_json_builds_onboarding_plan(tmp_path):
    from hermes_managed_network.onboarding import build_onboarding_plan_from_path

    nodes_path = _write_nodes(tmp_path)

    payload = build_onboarding_plan_from_path(nodes_path)

    assert payload["mode"] == "plan"
    assert payload["dry_run"] is True
    assert payload["nodes_count"] == 4
    assert payload["target_role"] is None
    assert payload["source"] == str(nodes_path)
    assert len(payload["nodes"]) == 4

    node = next(item for item in payload["nodes"] if item["node_id"] == "node-full-01")
    assert node["capability_profile"]["os"] == "Ubuntu 24.04"
    assert node["capability_profile"]["arch"] == "x86_64"
    assert node["capability_profile"]["cpu"]["cores"] == 8
    assert node["capability_profile"]["memory"]["total_mb"] == 16384
    assert node["capability_profile"]["disk"]["free_gb"] == 320
    assert node["capability_profile"]["network"]["egress"] == "public"
    assert node["capability_profile"]["service_manager"] == "systemd"
    assert node["capability_profile"]["docker_available"] is True
    assert node["capability_profile"]["systemd_available"] is True



def test_role_recommendation_covers_all_profiles(tmp_path):
    from hermes_managed_network.onboarding import build_onboarding_plan_from_path

    payload = build_onboarding_plan_from_path(_write_nodes(tmp_path))
    by_id = {item["node_id"]: item for item in payload["nodes"]}

    assert by_id["node-full-01"]["recommended_role"] == "full-worker"
    assert by_id["node-lite-01"]["recommended_role"] == "lite-worker"
    assert by_id["node-beacon-01"]["recommended_role"] == "beacon-only"
    assert by_id["node-proxy-01"]["recommended_role"] == "proxy-managed"



def test_plan_includes_expected_warnings(tmp_path):
    from hermes_managed_network.onboarding import build_onboarding_plan_from_path

    payload = build_onboarding_plan_from_path(_write_nodes(tmp_path))
    by_id = {item["node_id"]: item for item in payload["nodes"]}

    lite_warnings = "\n".join(by_id["node-lite-01"]["warnings"])
    beacon_warnings = "\n".join(by_id["node-beacon-01"]["warnings"])

    assert "low disk" in lite_warnings
    assert "no docker" in lite_warnings
    assert "no systemd" in lite_warnings
    assert "ssh port 22" in lite_warnings
    assert "low disk" in beacon_warnings
    assert "unknown network" in beacon_warnings



def test_migration_target_recommendation_scores_candidates(tmp_path):
    from hermes_managed_network.onboarding import build_onboarding_plan_from_path

    payload = build_onboarding_plan_from_path(_write_nodes(tmp_path))
    by_id = {item["node_id"]: item for item in payload["nodes"]}

    full_node = by_id["node-full-01"]
    candidates = full_node["migration_target_recommendation"]["candidates"]

    assert candidates[0]["node_id"] == "node-full-01"
    assert candidates[0]["score"] > candidates[-1]["score"]
    assert candidates[0]["recommended"] is True
    assert any("docker available" in reason for reason in candidates[0]["reasons"])



def test_service_registry_option_participates_in_recommendation(tmp_path):
    from hermes_managed_network.onboarding import build_onboarding_plan_from_path

    nodes_path = _write_nodes(tmp_path)
    registry_path = _write_service_registry(tmp_path)

    payload = build_onboarding_plan_from_path(nodes_path, service_registry_path=registry_path)

    full_node = next(item for item in payload["nodes"] if item["node_id"] == "node-full-01")
    candidates = full_node["migration_target_recommendation"]["candidates"]

    assert payload["service_count"] == 2
    assert candidates[0]["service_registry_considered"] is True
    assert any("service registry fit" in reason for reason in candidates[0]["reasons"])



def test_apply_skeleton_requires_approval_and_does_not_execute(tmp_path):
    from hermes_managed_network.onboarding import build_onboarding_plan_from_path

    payload = build_onboarding_plan_from_path(_write_nodes(tmp_path), target_role="full-worker")

    for node in payload["nodes"]:
        assert node["apply"] == {
            "approval_required": True,
            "not_executed": True,
            "machine_changed": False,
        }
        assert node["onboarding_steps"][-1] == "optional worker shell disabled by default"



def test_cli_json_is_parseable(tmp_path):
    nodes_path = _write_nodes(tmp_path)
    registry_path = _write_service_registry(tmp_path)

    result = runner.invoke(
        app,
        [
            "onboarding",
            "plan",
            "--nodes",
            str(nodes_path),
            "--service-registry",
            str(registry_path),
            "--target-role",
            "lite-worker",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["nodes_count"] == 4
    assert payload["target_role"] == "lite-worker"
    assert payload["nodes"][0]["apply"]["not_executed"] is True



def test_sensitive_fields_are_redacted(tmp_path):
    from hermes_managed_network.onboarding import render_onboarding_plan_json

    rendered = render_onboarding_plan_json(
        _write_nodes(tmp_path),
        service_registry_path=_write_service_registry(tmp_path),
    )

    payload = json.loads(rendered)
    full_node = next(item for item in payload["nodes"] if item["node_id"] == "node-full-01")

    assert full_node["input_summary"]["ssh"]["password"] == "[REDACTED]"
    assert "super-secret" not in rendered
    assert "hunter2" not in rendered
    assert "very-secret" not in rendered
    assert "abc123" not in rendered
    assert rendered.count("[REDACTED]") >= 3
