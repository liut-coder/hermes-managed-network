from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_worker_scripts_are_identical_between_repo_and_package_asset():
    repo_script = Path("scripts/worker.sh").read_text()
    asset_script = Path("src/hermes_managed_network/assets/worker.sh").read_text()

    assert repo_script == asset_script


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
    assert '"exec_enabled": os.environ.get("HMN_ENABLE_EXEC") == "1"' in script
    assert "bash -lc" not in script.split("if [ \"$HMN_ENABLE_EXEC\" != \"1\" ]; then", maxsplit=1)[0]


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
