from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_worker_scripts_are_identical_between_repo_and_package_asset():
    repo_script = Path("scripts/worker.sh").read_text()
    asset_script = Path("src/hermes_managed_network/assets/worker.sh").read_text()

    assert repo_script == asset_script


def test_worker_lite_scripts_are_identical_between_repo_and_package_asset():
    repo_script = Path("scripts/worker-lite.sh").read_text()
    asset_script = Path("src/hermes_managed_network/assets/worker-lite.sh").read_text()

    assert repo_script == asset_script


def test_worker_lite_is_posix_sh_and_avoids_heavy_dependencies():
    script = Path("src/hermes_managed_network/assets/worker-lite.sh").read_text()

    assert script.startswith("#!/bin/sh")
    assert "need_command bash" not in script
    assert "need_command python" not in script
    assert "need_command jq" not in script
    assert "/api/v1/nodes/${HERMES_NODE_ID}/heartbeat" in script
    assert "task_policy\":\"heartbeat-only" in script
    assert "/tasks/next" not in script
    assert "eval " not in script
    assert " sh -c" not in script


def test_worker_lite_default_protocol_matches_current_worker_protocol(tmp_path):
    from hermes_managed_network.version import current_version_info, is_worker_compatible

    env_file = tmp_path / "node.env"
    curl_log = tmp_path / "curl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "log=${HMN_CURL_LOG:?}\n"
        "url=\"\"\n"
        "data=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --data) shift; data=\"$1\" ;;\n"
        "    http://*) url=\"$1\" ;;\n"
        "  esac\n"
        "  shift || true\n"
        "done\n"
        "printf '%s\\t%s\\n' \"$url\" \"$data\" >>\"$log\"\n"
        "printf '{\"status\":\"ok\"}'\n"
    )
    curl.chmod(0o755)
    env_file.write_text(
        'HERMES_MASTER_URL="http://master.example"\n'
        'HERMES_NODE_ID="node-lite"\n'
        'HERMES_NODE_FINGERPRINT="sha256:lite"\n'
    )

    subprocess.run(
        ["sh", "scripts/worker-lite.sh"],
        check=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HMN_ENV_FILE": str(env_file),
            "HMN_CURL_LOG": str(curl_log),
        },
    )

    [_url, payload] = curl_log.read_text().rstrip("\n").split("\t", 1)
    facts = json.loads(payload)["facts"]
    expected_protocol = current_version_info().worker_protocol_version
    assert facts["worker_protocol_version"] == expected_protocol
    assert is_worker_compatible(expected_protocol, facts["worker_protocol_version"])
    assert facts["worker_variant"] == "posix-lite"


def test_worker_script_asset_exists_and_has_task_loop():
    script = Path("src/hermes_managed_network/assets/worker.sh").read_text()
    assert "/api/v1/nodes/${HERMES_NODE_ID}/tasks/next" in script
    assert "/api/v1/tasks/${task_id}/result" in script
    assert "HMN_ENABLE_EXEC" in script
    assert "execution disabled; set HMN_ENABLE_EXEC=1" in script
    assert "HMN_WORKER_PROTOCOL_VERSION" in script
    assert "worker_protocol_version" in script


def test_worker_heartbeat_collects_safe_facts_without_enabling_exec():
    script = Path("src/hermes_managed_network/assets/worker.sh").read_text()

    assert "collect_worker_facts()" in script
    assert '"load_average"' in script
    assert '"disk"' in script
    assert '"memory"' in script
    assert '"uptime"' in script
    assert '"capabilities"' in script
    assert '"has_systemctl"' in script
    assert '"writable_etc"' in script
    assert '"exec_enabled": os.environ.get("HMN_ENABLE_EXEC_VALUE") == "1"' in script
    assert "bash -lc" not in script.split("if [ \"$HMN_ENABLE_EXEC\" != \"1\" ]; then", maxsplit=1)[0]


def test_worker_beacon_mode_heartbeats_without_polling_or_execution(tmp_path):
    env_file = tmp_path / "node.env"
    curl_log = tmp_path / "curl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "log=${HMN_CURL_LOG:?}\n"
        "url=\"\"\n"
        "data=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --data) shift; data=\"$1\" ;;\n"
        "    http://*) url=\"$1\" ;;\n"
        "  esac\n"
        "  shift || true\n"
        "done\n"
        "printf '%s\\t%s\\n' \"$url\" \"$data\" >>\"$log\"\n"
        "case \"$url\" in\n"
        "  */tasks/next) printf '%s' '{\"task_id\":\"must-not-poll\",\"command\":\"touch SHOULD_NOT_RUN\"}' ;;\n"
        "  *) printf '{\"status\":\"ok\"}' ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    env_file.write_text(
        'HERMES_MASTER_URL="http://master.example"\n'
        'HERMES_NODE_ID="node-beacon"\n'
        'HERMES_NODE_FINGERPRINT="sha256:beacon"\n'
        'HMN_WORKER_MODE="beacon"\n'
    )

    subprocess.run(
        ["bash", str(Path.cwd() / "scripts/worker.sh")],
        check=True,
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HMN_ENV_FILE": str(env_file),
            "HMN_CURL_LOG": str(curl_log),
            "HMN_ENABLE_EXEC": "1",
        },
    )

    calls = [line.rstrip("\n").split("\t", 1) for line in curl_log.read_text().splitlines()]
    assert len(calls) == 1
    url, payload = calls[0]
    assert url.endswith("/api/v1/nodes/node-beacon/heartbeat")
    facts = json.loads(payload)["facts"]
    assert facts["worker_mode"] == "beacon"
    assert facts["task_policy"] == "heartbeat-only"
    assert facts["can_poll_tasks"] is False
    assert facts["exec_enabled"] is False
    assert not (tmp_path / "SHOULD_NOT_RUN").exists()


def test_worker_json_payloads_escape_special_characters(tmp_path):
    env_file = tmp_path / "node.env"
    curl_log = tmp_path / "curl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "log=${HMN_CURL_LOG:?}\n"
        "url=\"\"\n"
        "data=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --data) shift; data=\"$1\" ;;\n"
        "    http://*) url=\"$1\" ;;\n"
        "  esac\n"
        "  shift || true\n"
        "done\n"
        "printf '%s\\t%s\\n' \"$url\" \"$data\" >>\"$log\"\n"
        "case \"$url\" in\n"
        "  */tasks/next) printf '%s' '{\"task_id\":\"task-special\",\"command\":\"printf %s safe\"}' ;;\n"
        "  *) printf '{\"status\":\"ok\"}' ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    env_file.write_text(
        'HERMES_MASTER_URL="http://master.example"\n'
        'HERMES_NODE_ID="node-special"\n'
        'HERMES_NODE_FINGERPRINT="sha256:quote\\\" slash \\\\ line\\n tab\\t snowman ☃"\n'
    )

    subprocess.run(
        ["bash", "scripts/worker.sh"],
        check=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HMN_ENV_FILE": str(env_file),
            "HMN_CURL_LOG": str(curl_log),
            "HMN_ENABLE_EXEC": "0",
        },
    )

    calls = [line.rstrip("\n").split("\t", 1) for line in curl_log.read_text().splitlines()]
    assert len(calls) == 3
    payloads = {url.rsplit("/", 1)[-1]: json.loads(payload) for url, payload in calls}

    assert payloads["heartbeat"]["fingerprint"] == 'sha256:quote" slash \\ line\\n tab\\t snowman ☃'
    assert payloads["next"]["fingerprint"] == 'sha256:quote" slash \\ line\\n tab\\t snowman ☃'
    assert payloads["result"]["fingerprint"] == 'sha256:quote" slash \\ line\\n tab\\t snowman ☃'
    assert payloads["result"]["stderr"] == "execution disabled; set HMN_ENABLE_EXEC=1"


def test_worker_verifies_task_signature_before_execution():
    script = Path("src/hermes_managed_network/assets/worker.sh").read_text()

    assert "verify_task_signature" in script
    assert "task signature mismatch" in script
    assert "hmac.compare_digest" in script



def test_worker_applies_signed_fingerprint_rotation_task(tmp_path):
    from hermes_managed_network.signing import sign_task_payload

    env_file = tmp_path / "node.env"
    curl_log = tmp_path / "curl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    old_fp = "sha256:old-worker"
    new_fp = "sha256:new-worker"
    command = f"hmn:rotate-fingerprint {new_fp}"
    signature = sign_task_payload(
        node_fingerprint=old_fp,
        task_id="task-rotate",
        command=command,
        risk="low",
    )
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "log=${HMN_CURL_LOG:?}\n"
        "url=\"\"\n"
        "data=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --data) shift; data=\"$1\" ;;\n"
        "    http://*) url=\"$1\" ;;\n"
        "  esac\n"
        "  shift || true\n"
        "done\n"
        "printf '%s\\t%s\\n' \"$url\" \"$data\" >>\"$log\"\n"
        "case \"$url\" in\n"
        f"  */tasks/next) printf '%s' '{{\"task_id\":\"task-rotate\",\"command\":\"{command}\",\"risk\":\"low\",\"signature\":\"{signature}\"}}' ;;\n"
        "  */rotate-fingerprint) printf '{\"node_id\":\"node-rotate\",\"status\":\"rotated\"}' ;;\n"
        "  *) printf '{\"status\":\"ok\"}' ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    env_file.write_text(
        'HERMES_MASTER_URL="http://master.example"\n'
        'HERMES_NODE_ID="node-rotate"\n'
        f'HERMES_NODE_FINGERPRINT="{old_fp}"\n'
    )

    subprocess.run(
        ["bash", "scripts/worker.sh"],
        check=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HMN_ENV_FILE": str(env_file),
            "HMN_CURL_LOG": str(curl_log),
            "HMN_ENABLE_EXEC": "0",
        },
    )

    assert f'HERMES_NODE_FINGERPRINT="{new_fp}"' in env_file.read_text()
    calls = [line.rstrip("\n").split("\t", 1) for line in curl_log.read_text().splitlines()]
    payloads = [(url, json.loads(payload)) for url, payload in calls]
    rotate_payload = [payload for url, payload in payloads if url.endswith("/rotate-fingerprint")][0]
    result_payload = [payload for url, payload in payloads if "/tasks/task-rotate/result" in url][0]
    assert rotate_payload == {"fingerprint": old_fp, "new_fingerprint": new_fp}
    assert result_payload["fingerprint"] == new_fp
    assert result_payload["exit_code"] == 0
    assert result_payload["stdout"] == "fingerprint rotated"
