import json
from types import SimpleNamespace

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.storage import SQLiteStore


def _managed_node(node_id="node_worker", hostname="worker-node", fingerprint="sha256:worker"):
    from hermes_managed_network.inventory import Node

    return Node(
        node_id=node_id,
        fingerprint=fingerprint,
        hostname=hostname,
        addresses=[],
        trust_level="B",
        labels=[],
        status="managed",
        permission_bundles=["observe"],
    )



def test_backup_plan_run_status_dry_run_manifest_checksum(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("hello backup\n")
    out = tmp_path / "out"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_backup", hostname="backup-node"))

    plan = runner.invoke(app, ["backup", "plan", "--db", str(db), "--node", "node_backup", "--include", str(source), "--output-dir", str(out)])

    assert plan.exit_code == 0
    assert "backup plan: dry-run" in plan.stdout
    assert "restore: disabled" in plan.stdout

    run = runner.invoke(app, ["backup", "run", "--db", str(db), "--node", "node_backup", "--include", str(source), "--output-dir", str(out)])

    assert run.exit_code == 0
    assert "backup run: succeeded" in run.stdout
    assert "archive_checksum:" in run.stdout
    assert "checksum:" in run.stdout
    manifest_files = list(out.glob("*.manifest.json"))
    archive_files = list(out.glob("*.tar.gz"))
    assert len(manifest_files) == 1
    assert len(archive_files) == 1
    manifest = json.loads(manifest_files[0].read_text())
    assert manifest["dry_run"] is False
    assert manifest["archive_path"] == str(archive_files[0])
    assert manifest["archive_sha256"]
    assert manifest["entries"][0]["type"] == "directory"
    assert manifest["entries"][0]["file_count"] == 1
    assert manifest["manifest_sha256"]

    verify = runner.invoke(app, ["backup", "verify", "--db", str(db), "--node", "node_backup"])

    assert verify.exit_code == 0
    assert "backup verify: ok" in verify.stdout
    assert f"archive: {archive_files[0]}" in verify.stdout

    archive_files[0].unlink()
    failed_verify = runner.invoke(app, ["backup", "verify", "--db", str(db), "--node", "node_backup"])
    assert failed_verify.exit_code == 1
    assert "backup verify: missing archive" in failed_verify.stdout
    assert "run:" in failed_verify.stdout

    status = runner.invoke(app, ["backup", "status", "--db", str(db), "--node", "node_backup"])

    assert status.exit_code == 0
    assert "backup status: succeeded" in status.stdout
    assert f"archive: {archive_files[0]}" in status.stdout
    assert f"manifest: {manifest_files[0]}" in status.stdout
    assert "restore: disabled" in status.stdout
    runs = SQLiteStore(db).list_component_runs()
    assert any(item.component_id == "backup" and item.action == "backup.run" for item in runs)
    assert any(item.component_id == "backup" and item.action == "backup.verify" for item in runs)


def test_top_help_and_menu_show_monitor_and_backup_commands():
    runner = CliRunner()

    help_result = runner.invoke(app, ["--help"])
    menu_result = runner.invoke(app, ["menu", "--plain"])

    assert help_result.exit_code == 0
    assert "hmn monitor status" in help_result.stdout
    assert "hmn backup plan" in help_result.stdout
    assert "hmn backup run" in help_result.stdout
    assert "hmn backup verify" in help_result.stdout
    assert "hmn backup status" in help_result.stdout
    assert "hmn task recover-stuck" in help_result.stdout
    assert menu_result.exit_code == 0
    assert "hmn task recover-stuck" in menu_result.stdout
    assert "hmn monitor status" in menu_result.stdout
    assert "hmn backup plan" in menu_result.stdout
    assert "hmn backup run" in menu_result.stdout
    assert "hmn backup verify" in menu_result.stdout
    assert "hmn backup status" in menu_result.stdout

def test_task_recover_stuck_cli_expires_old_running_tasks(tmp_path):
    from datetime import datetime, timezone

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_recover"))
    task = store.create_task(node_id="node_recover", command="slow")
    store.claim_next_task("node_recover", lease_seconds=900)
    with store.connect() as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
            (datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat(), task.task_id),
        )

    result = runner.invoke(app, ["task", "recover-stuck", "--older-than", "1", "--db", str(db)])

    assert result.exit_code == 0
    assert "expired count: 1" in result.stdout
    assert task.task_id in result.stdout
    assert SQLiteStore(db).load_task(task.task_id).status == "failed"



def test_cli_can_create_and_revoke_token(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"

    created = runner.invoke(app, ["token", "create", "--db", str(db), "--trust", "B", "--label", "managed"])

    assert created.exit_code == 0
    token_value = created.stdout.strip()
    assert token_value.startswith("j_")
    assert SQLiteStore(db).load_token(token_value).labels == ["managed"]

    revoked = runner.invoke(app, ["token", "revoke", token_value, "--db", str(db)])

    assert revoked.exit_code == 0
    assert SQLiteStore(db).load_token(token_value).status == "revoked"


def test_service_discover_dry_run_uses_fixture_text_and_does_not_write_db(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    systemd_fixture = tmp_path / "systemd.txt"
    systemd_fixture.write_text("nginx.service loaded active running nginx web server\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "service",
            "discover",
            "--db",
            str(db),
            "--node-id",
            "node-cli",
            "--systemd-output",
            str(systemd_fixture),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "discovered services: 1" in result.stdout
    assert "create: svc_node-cli_nginx" in result.stdout
    assert "kind: None -> systemd" in result.stdout
    assert "svc_node-cli_nginx" in result.stdout
    assert SQLiteStore(db).list_service_records() == []
    assert SQLiteStore(db).list_audit_events() == []


def test_service_discover_apply_uses_fixture_text_and_writes_db_and_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    docker_fixture = tmp_path / "docker.jsonl"
    docker_fixture.write_text('{"Names":"web-app","Image":"nginx:alpine","Ports":"0.0.0.0:8080->80/tcp"}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "service",
            "discover",
            "--db",
            str(db),
            "--node-id",
            "node-cli",
            "--docker-output",
            str(docker_fixture),
            "--apply",
        ],
    )

    assert result.exit_code == 0
    assert "applied services: 1" in result.stdout
    stored = SQLiteStore(db).list_service_records(node_id="node-cli")
    assert [record.service_id for record in stored] == ["svc_node-cli_web-app"]
    assert stored[0].kind == "docker"
    assert stored[0].ports == [8080]
    events = SQLiteStore(db).list_audit_events()
    assert events[-1].action == "service_discovery"
    assert events[-1].details["service_ids"] == ["svc_node-cli_web-app"]


def test_service_discover_rejects_no_dry_run_without_applying(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    systemd_fixture = tmp_path / "systemd.txt"
    systemd_fixture.write_text("nginx.service loaded active running nginx web server\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "service",
            "discover",
            "--db",
            str(db),
            "--node-id",
            "node-cli",
            "--systemd-output",
            str(systemd_fixture),
            "--no-dry-run",
        ],
    )

    assert result.exit_code != 0
    assert SQLiteStore(db).list_service_records() == []
    assert SQLiteStore(db).list_audit_events() == []


def test_cli_can_expire_pending_tokens(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    token_value = runner.invoke(app, ["token", "create", "--db", str(db), "--ttl-minutes", "-1"]).stdout.strip()

    result = runner.invoke(app, ["token", "expire", "--db", str(db)])

    assert result.exit_code == 0
    assert "expired 1 token" in result.stdout
    assert token_value in result.stdout
    assert SQLiteStore(db).load_token(token_value).status == "expired"
    events = SQLiteStore(db).list_audit_events()
    assert events[-1].action == "expire"
    assert events[-1].subject_id == token_value


def test_cli_token_list_refreshes_expired_status(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    token_value = runner.invoke(app, ["token", "create", "--db", str(db), "--ttl-minutes", "-1"]).stdout.strip()

    result = runner.invoke(app, ["token", "list", "--db", str(db)])

    assert result.exit_code == 0
    assert f"{token_value}\texpired" in result.stdout
    assert SQLiteStore(db).load_token(token_value).status == "expired"


def test_cli_can_render_join_command(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    token_value = runner.invoke(app, ["token", "create", "--db", str(db)]).stdout.strip()

    result = runner.invoke(
        app,
        ["token", "join-command", token_value, "--master-url", "https://example.com", "--user", "hermes"],
    )

    assert result.exit_code == 0
    assert "HERMES_JOIN_TOKEN=" in result.stdout
    assert token_value in result.stdout
    assert "HERMES_MASTER_URL=https://example.com" in result.stdout
    assert "bash -s < <(curl -fsSL https://example.com/scripts/join.sh)" in result.stdout


def test_cli_can_render_safe_join_command(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    token_value = runner.invoke(app, ["token", "create", "--db", str(db)]).stdout.strip()

    result = runner.invoke(
        app,
        ["token", "join-command", token_value, "--master-url", "https://example.com", "--safe"],
    )

    assert result.exit_code == 0
    assert "mktemp" in result.stdout
    assert "sha256sum" in result.stdout
    assert token_value in result.stdout



def test_join_command_accepts_ipv6_literal_master_url(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    token_value = runner.invoke(app, ["token", "create", "--db", str(db)]).stdout.strip()

    result = runner.invoke(
        app,
        ["token", "join-command", token_value, "--master-url", "http://[2001:db8::10]:8765", "--safe"],
    )

    assert result.exit_code == 0
    assert "HERMES_MASTER_URL='http://[2001:db8::10]:8765'" in result.stdout
    assert "'http://[2001:db8::10]:8765'/scripts/join.sh" in result.stdout


def test_node_install_heartbeat_can_render_nas_lite_worker_with_endpoint_fallbacks(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_nas",
            fingerprint="sha256:nas",
            hostname="nas-node",
            addresses=["2001:db8::20"],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        [
            "node",
            "install-heartbeat",
            "--db",
            str(db),
            "--master-url",
            "https://master.example",
            "--endpoint",
            "https://[2001:db8::10]:8765",
            "--endpoint",
            "http://headscale.internal:8765",
            "--endpoint",
            "https://relay.example",
            "--runtime",
            "lite-worker",
            "--service-manager",
            "cron",
        ],
    )

    assert result.exit_code == 0
    assert "runtime=lite-worker" in result.stdout
    assert "service_manager=cron" in result.stdout
    assert "HERMES_MASTER_URL=https://master.example" in result.stdout
    assert "HMN_MASTER_URLS=https://[2001:db8::10]:8765,http://headscale.internal:8765,https://relay.example" in result.stdout
    assert "scripts/worker-lite.sh" in result.stdout
    assert "crontab" in result.stdout
    event = SQLiteStore(db).list_audit_events()[-1]
    assert event.details["runtime"] == "lite-worker"
    assert event.details["endpoints"] == [
        "https://[2001:db8::10]:8765",
        "http://headscale.internal:8765",
        "https://relay.example",
    ]

def test_cli_can_list_audit_events(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    token_value = runner.invoke(app, ["token", "create", "--db", str(db), "--trust", "B"]).stdout.strip()

    result = runner.invoke(app, ["audit", "list", "--db", str(db)])

    assert result.exit_code == 0
    assert token_value in result.stdout
    assert "join_token" in result.stdout
    assert "create" in result.stdout


def test_cli_can_list_audit_events_as_json_lines(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    token_value = runner.invoke(app, ["token", "create", "--db", str(db), "--trust", "C"]).stdout.strip()

    result = runner.invoke(app, ["audit", "list", "--db", str(db), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, list)
    event = payload[-1]
    assert event["subject_id"] == token_value
    assert event["subject_type"] == "join_token"
    assert event["action"] == "create"
    assert event["details"]["trust_level"] == "C"


def test_menu_shows_quick_actions():
    runner = CliRunner()

    result = runner.invoke(app, ["menu"], input="q\n")

    assert result.exit_code == 0
    assert "HMN 控制台" in result.stdout
    assert "hmn node confirm" in result.stdout
    assert "hmn node status" in result.stdout
    assert "hmn node doctor" in result.stdout
    assert "hmn node heartbeat-command" in result.stdout
    assert "hmn node install-heartbeat" in result.stdout
    assert "hmn node worker-status" in result.stdout
    assert "hmn task ssh-run-next" in result.stdout
    assert "hmn service discover" in result.stdout
    assert "hmn audit list" in result.stdout
    assert "查看审计" in result.stdout


def test_root_command_shows_menu_instead_of_missing_command():
    runner = CliRunner()

    result = runner.invoke(app, [], input="q\n")

    assert result.exit_code == 0
    assert "HMN 控制台" in result.stdout
    assert "hmn wake" in result.stdout
    assert "接入新机器" in result.stdout
    assert "hmn node confirm" in result.stdout
    assert "hmn node status" in result.stdout
    assert "hmn node doctor" in result.stdout
    assert "hmn task ssh-run-next" in result.stdout
    assert "hmn service discover" in result.stdout
    assert "hmn version" in result.stdout
    assert "hmn update" in result.stdout
    assert "hmn uninstall" in result.stdout


def test_menu_plain_prints_quick_actions():
    runner = CliRunner()

    result = runner.invoke(app, ["menu", "--plain"])

    assert result.exit_code == 0
    assert "HMN 快速菜单" in result.stdout
    assert "hmn wake" in result.stdout
    assert "hmn node confirm" in result.stdout
    assert "hmn node status" in result.stdout
    assert "hmn node doctor" in result.stdout
    assert "hmn node heartbeat-command" in result.stdout
    assert "hmn node install-heartbeat" in result.stdout
    assert "hmn node worker-status" in result.stdout
    assert "hmn task ssh-run-next" in result.stdout
    assert "hmn service discover" in result.stdout
    assert "hmn version" in result.stdout
    assert "示例" in result.stdout


def test_command_help_includes_examples():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "示例" in result.stdout
    assert "hmn node confirm" in result.stdout
    assert "hmn task ssh-run-next" in result.stdout
    assert "hmn service discover" in result.stdout


def test_root_menu_can_start_wake_flow(tmp_path, monkeypatch):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))

    result = runner.invoke(
        app,
        [],
        input="1\n\n\nhttp://master.internal:8765\n\n\n\n\n",
    )

    assert result.exit_code == 0
    assert "唤醒脚本已生成" in result.stdout
    assert "机器: node-server1" in result.stdout
    assert "HERMES_JOIN_TOKEN=" in result.stdout


def test_root_menu_can_confirm_pending_node(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    SQLiteStore(db).save_node(
        Node(
            node_id="node_menu",
            fingerprint="fp",
            hostname="menu-node",
            addresses=[],
            trust_level="B",
            labels=[],
        )
    )

    result = runner.invoke(app, [], input="3\n")

    assert result.exit_code == 0
    assert "自动选择 pending 节点: node_menu (menu-node)" in result.stdout
    assert SQLiteStore(db).load_node("node_menu").status == "managed"


def test_root_menu_can_show_audit_without_optioninfo(tmp_path, monkeypatch):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    token_value = runner.invoke(app, ["token", "create", "--db", str(db), "--trust", "B"]).stdout.strip()

    result = runner.invoke(app, [], input="16\n")

    assert result.exit_code == 0
    assert token_value in result.stdout
    assert "join_token" in result.stdout


def test_root_menu_can_create_token_without_optioninfo(tmp_path, monkeypatch):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))

    result = runner.invoke(app, [], input="17\n")

    assert result.exit_code == 0
    token_value = result.stdout.strip().splitlines()[-1]
    assert token_value.startswith("j_")
    assert SQLiteStore(db).load_token(token_value).trust_level == "B"


def test_default_db_reads_installer_master_env(tmp_path, monkeypatch):
    import hermes_managed_network.cli as cli

    db = tmp_path / "installed.db"
    env_file = tmp_path / "master.env"
    env_file.write_text(f"HMN_DB={db}\n")
    monkeypatch.delenv("HMN_DB", raising=False)
    monkeypatch.setattr(cli, "_read_master_env", lambda: {"HMN_DB": str(db)})

    runner = CliRunner()
    runner.invoke(app, ["token", "create", "--trust", "B"])

    result = runner.invoke(app, ["node", "list"])

    assert result.exit_code == 0
    assert db.exists()


def test_update_command_prints_raw_github_update_command():
    runner = CliRunner()

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "更新命令" in result.stdout
    assert "raw.githubusercontent.com/liut-coder/hermes-managed-network" in result.stdout
    assert "install.sh | sudo bash" in result.stdout


def test_doctor_command_reports_full_production_readiness(tmp_path):
    runner = CliRunner()
    etc_dir = tmp_path / "etc" / "hermes-managed-network"
    service_dir = tmp_path / "systemd"
    backup_dir = tmp_path / "backups"
    db = tmp_path / "state" / "control-plane.db"
    log_file = tmp_path / "hmn.log"
    etc_dir.mkdir(parents=True)
    service_dir.mkdir()
    backup_dir.mkdir()
    db.parent.mkdir()
    db.write_text("")
    log_file.write_text("started\nready\n")
    (etc_dir / "master.env").write_text(
        f"HMN_DB={db}\nHMN_HOST=127.0.0.1\nHMN_PORT=8765\nHMN_PUBLIC_URL=https://hmn.example\n"
    )
    (etc_dir / "approval-gateway.env").write_text("HMN_APPROVAL_GATEWAY_CLIENT=telegram\n")
    (etc_dir / "headscale.env").write_text("HMN_HEADSCALE_MODE=bundled\n")
    (etc_dir / "config.yaml").write_text("network:\n  provider: headscale\n")
    (service_dir / "hermes-managed-network.service").write_text("[Service]\nExecStart=/opt/hmn/.venv/bin/python\n")
    (service_dir / "hermes-managed-network-approval-gateway.service").write_text(
        "[Service]\nExecStart=/usr/local/bin/hmn approval-gateway run\n"
    )
    stamp = "20260101-010203"
    for name in [
        "control-plane.20260101-010203.db",
        "master.20260101-010203.env",
        "config.20260101-010203.yaml",
        "metadata.20260101-010203.env",
    ]:
        (backup_dir / name).write_text("backup\n")
    (etc_dir / "upgrade-manifest.env").write_text(
        f"PREVIOUS_VERSION=0.9.0\n"
        f"TARGET_VERSION=1.0.0\n"
        f"VERSION_POLICY=upgrade\n"
        f"HMN_BACKUP_DIR={backup_dir}\n"
        f"HMN_LAST_BACKUP_STAMP={stamp}\n"
        f"BACKUP_DB={backup_dir / 'control-plane.20260101-010203.db'}\n"
        f"BACKUP_ENV={backup_dir / 'master.20260101-010203.env'}\n"
        f"BACKUP_CONFIG={backup_dir / 'config.20260101-010203.yaml'}\n"
        f"BACKUP_METADATA={backup_dir / 'metadata.20260101-010203.env'}\n"
        f"ROLLBACK_COMMAND=hmn rollback --stamp {stamp}\n"
    )

    result = runner.invoke(
        app,
        [
            "doctor",
            "--etc-dir",
            str(etc_dir),
            "--service-dir",
            str(service_dir),
            "--backup-dir",
            str(backup_dir),
            "--skip-systemd",
            "--health-url",
            "skip",
            "--version-url",
            "skip",
            "--log-file",
            str(log_file),
        ],
    )

    assert result.exit_code == 0
    assert "生产巡检" in result.stdout
    assert "安装状态" in result.stdout
    assert "master.env: OK" in result.stdout
    assert "database path: OK" in result.stdout
    assert "database file: OK" in result.stdout
    assert "服务状态" in result.stdout
    assert "control plane service: OK" in result.stdout
    assert "approval gateway service: OK" in result.stdout
    assert "headscale config: OK" in result.stdout
    assert "接口状态" in result.stdout
    assert "healthz: SKIP" in result.stdout
    assert "api version: SKIP" in result.stdout
    assert "upgrade/rollback readiness" in result.stdout
    assert "upgrade backup: OK" in result.stdout
    assert "rollback command: hmn rollback --stamp 20260101-010203" in result.stdout
    assert "最近日志提示" in result.stdout
    assert "ready" in result.stdout
    assert "hmn update" in result.stdout


def test_rollback_command_dry_run_prints_restore_plan(tmp_path):
    runner = CliRunner()
    etc_dir = tmp_path / "etc" / "hermes-managed-network"
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    etc_dir.mkdir(parents=True)
    backup_dir.mkdir()
    state_dir.mkdir()
    stamp = "20260101-010203"
    db_backup = backup_dir / f"control-plane.{stamp}.db"
    env_backup = backup_dir / f"master.{stamp}.env"
    config_backup = backup_dir / f"config.{stamp}.yaml"
    metadata_backup = backup_dir / f"metadata.{stamp}.env"
    db_backup.write_text("old-db\n")
    env_backup.write_text(f"HMN_DB={state_dir / 'control-plane.db'}\n")
    config_backup.write_text("network:\n  provider: headscale\n")
    metadata_backup.write_text("PREVIOUS_VERSION=0.9.0\n")
    (etc_dir / "upgrade-manifest.env").write_text(
        f"HMN_BACKUP_DIR={backup_dir}\n"
        f"HMN_LAST_BACKUP_STAMP={stamp}\n"
        f"BACKUP_DB={db_backup}\n"
        f"BACKUP_ENV={env_backup}\n"
        f"BACKUP_CONFIG={config_backup}\n"
        f"BACKUP_METADATA={metadata_backup}\n"
    )

    result = runner.invoke(
        app,
        ["rollback", "--etc-dir", str(etc_dir), "--backup-dir", str(backup_dir), "--skip-systemd"],
    )

    assert result.exit_code == 0
    assert "回滚计划" in result.stdout
    assert "DRY RUN" in result.stdout
    assert str(db_backup) in result.stdout
    assert str(state_dir / "control-plane.db") in result.stdout
    assert f"--etc-dir {etc_dir}" in result.stdout
    assert f"--backup-dir {backup_dir}" in result.stdout
    assert "--skip-systemd --yes" in result.stdout
    assert not (state_dir / "control-plane.db").exists()


def test_rollback_command_yes_restores_backup_files(tmp_path):
    runner = CliRunner()
    etc_dir = tmp_path / "etc" / "hermes-managed-network"
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    etc_dir.mkdir(parents=True)
    backup_dir.mkdir()
    state_dir.mkdir()
    stamp = "20260101-010203"
    db_backup = backup_dir / f"control-plane.{stamp}.db"
    env_backup = backup_dir / f"master.{stamp}.env"
    config_backup = backup_dir / f"config.{stamp}.yaml"
    metadata_backup = backup_dir / f"metadata.{stamp}.env"
    db_backup.write_text("old-db\n")
    env_backup.write_text(f"HMN_DB={state_dir / 'control-plane.db'}\nHMN_PORT=8765\n")
    config_backup.write_text("network:\n  provider: headscale\n")
    metadata_backup.write_text("PREVIOUS_VERSION=0.9.0\n")
    (etc_dir / "master.env").write_text(f"HMN_DB={state_dir / 'control-plane.db'}\nHMN_PORT=9999\n")
    (etc_dir / "config.yaml").write_text("network:\n  provider: disabled\n")
    (etc_dir / "upgrade-manifest.env").write_text(
        f"HMN_BACKUP_DIR={backup_dir}\n"
        f"HMN_LAST_BACKUP_STAMP={stamp}\n"
        f"BACKUP_DB={db_backup}\n"
        f"BACKUP_ENV={env_backup}\n"
        f"BACKUP_CONFIG={config_backup}\n"
        f"BACKUP_METADATA={metadata_backup}\n"
    )

    result = runner.invoke(
        app,
        ["rollback", "--etc-dir", str(etc_dir), "--backup-dir", str(backup_dir), "--skip-systemd", "--yes"],
    )

    assert result.exit_code == 0
    assert "已恢复备份" in result.stdout
    assert (state_dir / "control-plane.db").read_text() == "old-db\n"
    assert (etc_dir / "master.env").read_text() == f"HMN_DB={state_dir / 'control-plane.db'}\nHMN_PORT=8765\n"
    assert (etc_dir / "config.yaml").read_text() == "network:\n  provider: headscale\n"
    assert (etc_dir / f"metadata.{stamp}.env").read_text() == "PREVIOUS_VERSION=0.9.0\n"


def test_rollback_command_rejects_symlink_and_unsafe_target_before_writes(tmp_path):
    runner = CliRunner()
    etc_dir = tmp_path / "etc" / "hermes-managed-network"
    backup_dir = tmp_path / "backups"
    safe_state = tmp_path / "state"
    etc_dir.mkdir(parents=True)
    backup_dir.mkdir()
    safe_state.mkdir()
    stamp = "20260101-010203"
    db_backup = backup_dir / f"control-plane.{stamp}.db"
    env_backup = backup_dir / f"master.{stamp}.env"
    config_backup = backup_dir / f"config.{stamp}.yaml"
    metadata_backup = backup_dir / f"metadata.{stamp}.env"
    db_backup.write_text("old-db\n")
    env_backup.write_text(f"HMN_DB={safe_state / 'control-plane.db'}\n")
    config_backup.write_text("network:\n  provider: headscale\n")
    metadata_backup.write_text("PREVIOUS_VERSION=0.9.0\n")
    poisoned_target = tmp_path.parent / f"outside-{tmp_path.name}.db"
    (etc_dir / "master.env").write_text(f"HMN_DB={poisoned_target}\n")
    (etc_dir / "config.yaml").symlink_to(config_backup)
    (etc_dir / "upgrade-manifest.env").write_text(
        f"HMN_BACKUP_DIR={backup_dir}\n"
        f"HMN_LAST_BACKUP_STAMP={stamp}\n"
        f"BACKUP_DB={db_backup}\n"
        f"BACKUP_ENV={env_backup}\n"
        f"BACKUP_CONFIG={config_backup}\n"
        f"BACKUP_METADATA={metadata_backup}\n"
    )

    result = runner.invoke(app, ["rollback", "--etc-dir", str(etc_dir), "--backup-dir", str(backup_dir), "--skip-systemd", "--yes"])

    assert result.exit_code == 1
    assert "回滚安全校验失败" in result.stdout
    assert "target is symlink" in result.stdout
    assert "target outside allowed data dirs" in result.stdout
    assert not poisoned_target.exists()
    assert (etc_dir / "master.env").read_text() == f"HMN_DB={poisoned_target}\n"


def test_rollback_command_explicit_stamp_ignores_stale_manifest_paths(tmp_path):
    runner = CliRunner()
    etc_dir = tmp_path / "etc" / "hermes-managed-network"
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    stale_dir = tmp_path / "stale"
    etc_dir.mkdir(parents=True)
    backup_dir.mkdir()
    state_dir.mkdir()
    stale_dir.mkdir()
    requested_stamp = "20260101-010203"
    stale_stamp = "20250101-010203"
    for name, content in [
        (f"control-plane.{requested_stamp}.db", "requested-db\n"),
        (f"master.{requested_stamp}.env", f"HMN_DB={state_dir / 'control-plane.db'}\n"),
        (f"config.{requested_stamp}.yaml", "requested-config\n"),
        (f"metadata.{requested_stamp}.env", "requested-metadata\n"),
    ]:
        (backup_dir / name).write_text(content)
    for name in [
        f"control-plane.{stale_stamp}.db",
        f"master.{stale_stamp}.env",
        f"config.{stale_stamp}.yaml",
        f"metadata.{stale_stamp}.env",
    ]:
        (stale_dir / name).write_text("stale\n")
    (etc_dir / "upgrade-manifest.env").write_text(
        f"HMN_BACKUP_DIR={stale_dir}\n"
        f"HMN_LAST_BACKUP_STAMP={stale_stamp}\n"
        f"BACKUP_DB={stale_dir / f'control-plane.{stale_stamp}.db'}\n"
        f"BACKUP_ENV={stale_dir / f'master.{stale_stamp}.env'}\n"
        f"BACKUP_CONFIG={stale_dir / f'config.{stale_stamp}.yaml'}\n"
        f"BACKUP_METADATA={stale_dir / f'metadata.{stale_stamp}.env'}\n"
    )

    result = runner.invoke(app, ["rollback", "--stamp", requested_stamp, "--etc-dir", str(etc_dir), "--backup-dir", str(backup_dir), "--skip-systemd", "--yes"])

    assert result.exit_code == 0
    assert (state_dir / "control-plane.db").read_text() == "requested-db\n"
    assert (etc_dir / "config.yaml").read_text() == "requested-config\n"
    assert (etc_dir / f"metadata.{requested_stamp}.env").read_text() == "requested-metadata\n"


def test_rollback_command_systemd_failures_abort(monkeypatch, tmp_path):
    runner = CliRunner()
    from hermes_managed_network import cli

    etc_dir = tmp_path / "etc" / "hermes-managed-network"
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    etc_dir.mkdir(parents=True)
    backup_dir.mkdir()
    state_dir.mkdir()
    stamp = "20260101-010203"
    (backup_dir / f"control-plane.{stamp}.db").write_text("old-db\n")
    (backup_dir / f"master.{stamp}.env").write_text(f"HMN_DB={state_dir / 'control-plane.db'}\n")
    (backup_dir / f"config.{stamp}.yaml").write_text("config\n")
    (backup_dir / f"metadata.{stamp}.env").write_text("metadata\n")
    (etc_dir / "upgrade-manifest.env").write_text(f"HMN_BACKUP_DIR={backup_dir}\nHMN_LAST_BACKUP_STAMP={stamp}\n")
    calls = []

    def fake_run(args, check=False, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=9 if args[:2] == ["systemctl", "stop"] else 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["rollback", "--etc-dir", str(etc_dir), "--backup-dir", str(backup_dir), "--yes"])

    assert result.exit_code == 9
    assert "停止服务失败" in result.stdout
    assert not (state_dir / "control-plane.db").exists()
    assert calls == [["systemctl", "stop", "hermes-managed-network.service"]]


def test_doctor_command_reports_installer_readiness(tmp_path):
    runner = CliRunner()
    etc_dir = tmp_path / "etc" / "hermes-managed-network"
    service_dir = tmp_path / "systemd"
    db = tmp_path / "state" / "control-plane.db"
    etc_dir.mkdir(parents=True)
    service_dir.mkdir()
    db.parent.mkdir()
    db.write_text("")
    (etc_dir / "master.env").write_text(
        f"HMN_DB={db}\nHMN_HOST=127.0.0.1\nHMN_PORT=8765\nHMN_PUBLIC_URL=https://hmn.example\n"
    )
    (etc_dir / "approval-gateway.env").write_text("HMN_APPROVAL_GATEWAY_CLIENT=telegram\n")
    (etc_dir / "headscale.env").write_text("HMN_HEADSCALE_MODE=bundled\n")
    (service_dir / "hermes-managed-network.service").write_text("[Service]\nExecStart=/opt/hmn/.venv/bin/python\n")
    (service_dir / "hermes-managed-network-approval-gateway.service").write_text(
        "[Service]\nExecStart=/usr/local/bin/hmn approval-gateway run\n"
    )

    result = runner.invoke(
        app,
        ["doctor", "--etc-dir", str(etc_dir), "--service-dir", str(service_dir), "--skip-systemd"],
    )

    assert result.exit_code == 0
    assert "生产巡检" in result.stdout
    assert "安装状态" in result.stdout
    assert "master.env: OK" in result.stdout
    assert "database path: OK" in result.stdout
    assert "服务状态" in result.stdout
    assert "control plane service: OK" in result.stdout
    assert "approval gateway service: OK" in result.stdout
    assert "headscale config: OK" in result.stdout
    assert "接口状态" in result.stdout
    assert "upgrade/rollback readiness" in result.stdout
    assert "upgrade backup: WARN" in result.stdout
    assert "/var/backups/hermes-managed-network" in result.stdout
    assert "hmn update" in result.stdout


def test_uninstall_without_yes_prints_safe_command_only():
    runner = CliRunner()

    result = runner.invoke(app, ["uninstall"])

    assert result.exit_code == 0
    assert "卸载命令" in result.stdout
    assert "hmn uninstall --yes" in result.stdout
    assert "systemctl disable --now hermes-managed-network.service" in result.stdout


def test_node_confirm_without_id_auto_selects_single_pending_node(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_auto",
            fingerprint="fp",
            hostname="lazy-node",
            addresses=["10.0.0.2"],
            trust_level="B",
            labels=[],
        )
    )

    result = runner.invoke(app, ["node", "confirm", "--db", str(db)])

    assert result.exit_code == 0
    assert "自动选择 pending 节点: node_auto (lazy-node)" in result.stdout
    assert "confirmed node_auto" in result.stdout
    assert SQLiteStore(db).load_node("node_auto").status == "managed"


def test_node_confirm_without_id_prompts_when_multiple_pending_nodes(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    for node_id, hostname in [("node_a", "a"), ("node_b", "b")]:
        SQLiteStore(db).save_node(
            Node(
                node_id=node_id,
                fingerprint=f"fp-{node_id}",
                hostname=hostname,
                addresses=[],
                trust_level="B",
                labels=[],
            )
        )

    result = runner.invoke(app, ["node", "confirm", "--db", str(db)], input="2\n")

    assert result.exit_code == 0
    assert "有多个 pending 节点" in result.stdout
    assert "confirmed node_b" in result.stdout
    assert SQLiteStore(db).load_node("node_a").status == "pending"
    assert SQLiteStore(db).load_node("node_b").status == "managed"


def test_node_status_without_id_auto_selects_single_managed_node(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    node = Node(
        node_id="node_status",
        fingerprint="fp-status",
        hostname="status-node",
        addresses=["10.0.0.3"],
        trust_level="B",
        labels=["backup", "worker"],
        status="managed",
        permission_bundles=["observe"],
        ssh_host="100.64.0.3",
        ssh_user="ops",
        ssh_port=2222,
    )
    store = SQLiteStore(db)
    store.save_node(node)
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_status",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )

    result = runner.invoke(app, ["node", "status", "--db", str(db)])

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_status (status-node)" in result.stdout
    assert "node: node_status" in result.stdout
    assert "status: managed" in result.stdout
    assert "host: status-node" in result.stdout
    assert "labels: backup, worker" in result.stdout
    assert "addresses: 10.0.0.3" in result.stdout
    assert "bundles: observe" in result.stdout
    assert "ssh_host: 100.64.0.3" in result.stdout
    assert "ssh_user: ops" in result.stdout
    assert "ssh_port: 2222" in result.stdout
    assert "network_provider: -" in result.stdout
    assert "network_ip: -" in result.stdout
    assert "network_online: -" in result.stdout
    assert "last_ssh_check: -" in result.stdout


def test_node_status_shows_last_ssh_check_from_doctor_audit(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_status_ssh",
            fingerprint="fp-status-ssh",
            hostname="status-ssh-node",
            addresses=["10.0.0.4"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.4",
            ssh_user="ops",
            ssh_port=2227,
        )
    )
    monkeypatch.setattr(
        "hermes_managed_network.cli.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="pong\n", stderr=""),
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_status_ssh",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )
    runner.invoke(app, ["node", "doctor", "--db", str(db)])

    result = runner.invoke(app, ["node", "status", "--db", str(db)])

    assert result.exit_code == 0
    assert "last_ssh_check: ok ops@100.64.0.4:2227" in result.stdout
    assert "ssh_reason: SSH 连通正常" in result.stdout


def test_node_status_shows_headscale_network_fields(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_status_network",
            fingerprint="fp-status-network",
            hostname="status-network-node",
            addresses=["10.0.0.6"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
            network_provider="headscale",
            network_node_id="321",
            network_ip="100.64.0.6",
            network_tags=["tag:worker", "tag:ssh"],
            network_online=True,
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_status_network",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )

    result = runner.invoke(app, ["node", "status", "--db", str(db)])

    assert result.exit_code == 0
    assert "network_provider: headscale" in result.stdout
    assert "network_node_id: 321" in result.stdout
    assert "network_ip: 100.64.0.6" in result.stdout
    assert "network_tags: tag:worker, tag:ssh" in result.stdout
    assert "network_online: yes" in result.stdout


def test_node_doctor_uses_headscale_network_ip_when_no_ssh_host(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_doctor_network",
            fingerprint="fp-doctor-network",
            hostname="doctor-network-node",
            addresses=["192.0.2.7"],
            trust_level="B",
            labels=["ssh-user=ops", "ssh-port=2207"],
            status="managed",
            permission_bundles=["observe"],
            network_provider="headscale",
            network_node_id="777",
            network_ip="100.64.0.7",
            network_online=True,
        )
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("hermes_managed_network.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["node", "doctor", "--db", str(db)])

    assert result.exit_code == 0
    assert calls == [["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-p", "2207", "ops@100.64.0.7", "true"]]
    assert "ssh_host: -" in result.stdout
    assert "network_ip: 100.64.0.7" in result.stdout
    assert "SSH 连通: OK ops@100.64.0.7:2207" in result.stdout
    event = SQLiteStore(db).list_audit_events()[-1]
    assert event.details["ssh_connectivity"]["host"] == "100.64.0.7"
    assert event.details["ssh_connectivity"]["target_source"] == "network_ip"


def test_worker_status_shows_friendly_ssh_reason(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_worker_ssh_warn",
            fingerprint="fp-worker-ssh",
            hostname="worker-ssh-node",
            addresses=["10.0.0.5"],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.5",
            ssh_user="ops",
            ssh_port=2228,
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_worker_ssh_warn",
        action="doctor",
        outcome="warn",
        details={
            "checks": {"SSH 连通": False},
            "ssh_connectivity": {
                "configured": True,
                "reachable": False,
                "host": "100.64.0.5",
                "user": "ops",
                "port": 2228,
                "exit_code": 255,
                "stderr": "Connection refused",
                "skipped": False,
            },
        },
    )

    result = runner.invoke(app, ["node", "worker-status", "--db", str(db)])

    assert result.exit_code == 1
    assert "ssh: warn ops@100.64.0.5:2228 Connection refused" in result.stdout
    assert "ssh_reason: SSH 网络不通：Connection refused" in result.stdout



def test_node_status_without_id_prompts_when_multiple_managed_nodes(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    for node_id, hostname in [("node_a", "a"), ("node_b", "b")]:
        store.save_node(
            Node(
                node_id=node_id,
                fingerprint=f"fp-{node_id}",
                hostname=hostname,
                addresses=[],
                trust_level="B",
                labels=[],
                status="managed",
            )
        )
        store.record_audit(
            event_type="node",
            subject_type="node",
            subject_id=node_id,
            action="heartbeat",
            outcome="ok",
            details={"status": "ok", "facts": {}},
        )

    result = runner.invoke(app, ["node", "status", "--db", str(db)], input="2\n")

    assert result.exit_code == 0
    assert "有多个 managed 节点，请选择：" in result.stdout
    assert "node: node_b" in result.stdout
    assert "host: b" in result.stdout


def test_node_status_auto_select_keeps_node_after_later_warn_heartbeat(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_once_ok_cli",
            fingerprint="fp-once-ok-cli",
            hostname="once-ok-cli-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_once_ok_cli",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {}},
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_once_ok_cli",
        action="heartbeat",
        outcome="warn",
        details={"status": "warn", "facts": {}},
    )

    result = runner.invoke(app, ["node", "status", "--db", str(db)])

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_once_ok_cli" in result.stdout
    assert "node: node_once_ok_cli" in result.stdout


def test_node_status_auto_select_ignores_managed_without_first_heartbeat(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_real_managed",
            fingerprint="fp-real",
            hostname="real-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_real_managed",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {}},
    )
    store.save_node(
        Node(
            node_id="node_no_heartbeat",
            fingerprint="fp-waiting",
            hostname="waiting-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
        )
    )

    result = runner.invoke(app, ["node", "status", "--db", str(db)])

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_real_managed" in result.stdout
    assert "node: node_real_managed" in result.stdout
    assert "waiting-node" not in result.stdout


def test_root_menu_can_show_node_status(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_menu_status",
            fingerprint="fp",
            hostname="menu-status-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            ssh_host="100.64.0.20",
            ssh_user="ops",
            ssh_port=2205,
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_menu_status",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )

    result = runner.invoke(app, [], input="4\n")

    assert result.exit_code == 0
    assert "node: node_menu_status" in result.stdout
    assert "host: menu-status-node" in result.stdout
    assert "ssh_host: 100.64.0.20" in result.stdout
    assert "ssh_user: ops" in result.stdout
    assert "ssh_port: 2205" in result.stdout



def test_node_doctor_auto_selects_and_records_audit(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_doctor",
            fingerprint="fp",
            hostname="doctor-node",
            addresses=["10.0.0.9"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.9",
            ssh_user="deploy",
            ssh_port=2202,
        )
    )

    monkeypatch.setattr(
        "hermes_managed_network.cli.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="pong\n", stderr=""),
    )

    result = runner.invoke(app, ["node", "doctor", "--db", str(db)])

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_doctor (doctor-node)" in result.stdout
    assert "doctor: node_doctor" in result.stdout
    assert "ssh_host: 100.64.0.9" in result.stdout
    assert "ssh_user: deploy" in result.stdout
    assert "ssh_port: 2202" in result.stdout
    assert "登记状态: OK" in result.stdout
    assert "权限包: OK" in result.stdout
    events = SQLiteStore(db).list_audit_events()
    assert events[-1].action == "doctor"
    assert events[-1].subject_id == "node_doctor"
    assert events[-1].outcome == "ok"


def test_node_doctor_can_skip_ssh_check(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_doctor_skip_ssh",
            fingerprint="fp",
            hostname="doctor-skip-ssh",
            addresses=["10.0.0.12"],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.12",
            ssh_user="root",
            ssh_port=2224,
        )
    )

    def fail_if_called(command, **kwargs):
        raise AssertionError("ssh check should be skipped")

    monkeypatch.setattr("hermes_managed_network.cli.subprocess.run", fail_if_called)

    result = runner.invoke(app, ["node", "doctor", "--no-ssh-check", "--db", str(db)])

    assert result.exit_code == 0
    assert "SSH 连通: SKIPPED" in result.stdout
    event = SQLiteStore(db).list_audit_events()[-1]
    assert event.details["ssh_connectivity"]["skipped"] is True
    assert event.details["checks"]["SSH 连通"] is True



def test_node_doctor_reports_ssh_connectivity_ok(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_doctor_ssh_ok",
            fingerprint="fp",
            hostname="doctor-ssh-ok",
            addresses=["10.0.0.10"],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.10",
            ssh_user="root",
            ssh_port=2222,
        )
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="pong\n", stderr="")

    monkeypatch.setattr("hermes_managed_network.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["node", "doctor", "--db", str(db)])

    assert result.exit_code == 0
    assert "SSH 连通: OK" in result.stdout
    events = SQLiteStore(db).list_audit_events()
    assert events[-1].action == "doctor"
    assert events[-1].details["ssh_connectivity"]["reachable"] is True
    assert events[-1].details["ssh_connectivity"]["host"] == "100.64.0.10"



def test_node_doctor_reports_ssh_connectivity_warn(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_doctor_ssh_warn",
            fingerprint="fp",
            hostname="doctor-ssh-warn",
            addresses=["10.0.0.11"],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.11",
            ssh_user="root",
            ssh_port=2223,
        )
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=255, stdout="", stderr="Connection timed out")

    monkeypatch.setattr("hermes_managed_network.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["node", "doctor", "--db", str(db)])

    assert result.exit_code == 1
    assert "SSH 连通: WARN" in result.stdout
    events = SQLiteStore(db).list_audit_events()
    assert events[-1].action == "doctor"
    assert events[-1].outcome == "warn"
    assert events[-1].details["ssh_connectivity"]["reachable"] is False
    assert events[-1].details["ssh_connectivity"]["exit_code"] == 255



def test_node_doctor_reports_missing_permission_bundle(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_weak",
            fingerprint="fp",
            hostname="weak-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
        )
    )

    result = runner.invoke(app, ["node", "doctor", "--db", str(db)])

    assert result.exit_code == 1
    assert "权限包: WARN" in result.stdout
    assert SQLiteStore(db).list_audit_events()[-1].outcome == "warn"


def test_root_menu_can_run_node_doctor(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    SQLiteStore(db).save_node(
        Node(
            node_id="node_menu_doctor",
            fingerprint="fp",
            hostname="menu-doctor-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="100.64.0.21",
            ssh_user="deploy",
            ssh_port=2206,
        )
    )

    monkeypatch.setattr(
        "hermes_managed_network.cli.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="pong\n", stderr=""),
    )

    result = runner.invoke(app, [], input="5\n")

    assert result.exit_code == 0
    assert "doctor: node_menu_doctor" in result.stdout
    assert "ssh_host: 100.64.0.21" in result.stdout
    assert "ssh_user: deploy" in result.stdout
    assert "ssh_port: 2206" in result.stdout


def test_version_command_prints_package_version():
    runner = CliRunner()

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "hmn" in result.stdout
    assert "version" in result.stdout



def test_node_heartbeat_command_prints_copy_paste_command(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_hb_cmd",
            fingerprint="sha256:hb",
            hostname="hb-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        ["node", "heartbeat-command", "--db", str(db), "--master-url", "https://master.example"],
    )

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_hb_cmd (hb-node)" in result.stdout
    assert "curl -fsS -X POST" in result.stdout
    assert "https://master.example/api/v1/nodes/node_hb_cmd/heartbeat" in result.stdout
    assert "sha256:hb" in result.stdout


def test_root_menu_can_show_heartbeat_command(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    monkeypatch.setenv("HMN_PUBLIC_URL", "https://master.example")
    SQLiteStore(db).save_node(
        Node(
            node_id="node_menu_hb",
            fingerprint="sha256:hb",
            hostname="menu-hb-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(app, [], input="6\n")

    assert result.exit_code == 0
    assert "node_menu_hb/heartbeat" in result.stdout



def test_node_install_heartbeat_prints_systemd_timer(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_timer",
            fingerprint="sha256:timer",
            hostname="timer-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        ["node", "install-heartbeat", "--db", str(db), "--master-url", "https://master.example"],
    )

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_timer (timer-node)" in result.stdout
    assert "sudo bash -c" in result.stdout
    assert "install -d -m 0700 /etc/hermes-managed-network" in result.stdout
    assert "HERMES_MASTER_URL=https://master.example" in result.stdout
    assert "HERMES_NODE_ID=node_timer" in result.stdout
    assert "HERMES_NODE_FINGERPRINT=sha256:timer" in result.stdout
    assert "HMN_ENABLE_EXEC=0" in result.stdout
    assert "chmod 0600 /etc/hermes-managed-network/node.env" in result.stdout
    assert "https://master.example/scripts/worker.sh" in result.stdout
    assert "chmod 0755 /usr/local/bin/hmn-worker" in result.stdout
    assert "hermes-managed-network-heartbeat.service" in result.stdout
    assert "hermes-managed-network-heartbeat.timer" in result.stdout
    assert "systemctl enable --now hermes-managed-network-heartbeat.timer" in result.stdout


def test_node_install_heartbeat_can_render_windows_task_scheduler_adapter(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_windows",
            fingerprint="sha256:windows",
            hostname="windows-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        [
            "node",
            "install-heartbeat",
            "--db",
            str(db),
            "--master-url",
            "https://master.example",
            "--service-manager",
            "windows-task",
        ],
    )

    assert result.exit_code == 0
    assert "service_manager=windows-task" in result.stdout
    assert "$baseDir = \"C:\\ProgramData\\HermesManagedNetwork\"" in result.stdout
    assert "worker-windows.ps1" in result.stdout
    assert "node.env.ps1" in result.stdout
    assert "schtasks /Create /SC MINUTE /MO 1" in result.stdout
    assert "HMN_WORKER_MODE=worker" in result.stdout
    assert "HMN_BEACON_ONLY=0" in result.stdout
    assert "默认安全模式：HMN_ENABLE_EXEC=0，不会执行下发 shell 命令。" in result.stdout


def test_node_uninstall_heartbeat_can_render_windows_task_scheduler_uninstaller(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_windows_uninstall",
            fingerprint="sha256:windows-uninstall",
            hostname="windows-uninstall-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        ["node", "uninstall-heartbeat", "--db", str(db), "--service-manager", "windows-task"],
    )

    assert result.exit_code == 0
    assert "service_manager=windows-task" in result.stdout
    assert "schtasks /Delete /TN $taskName /F" in result.stdout
    assert "Remove-Item -LiteralPath $baseDir -Recurse -Force" in result.stdout


def test_root_menu_can_show_install_heartbeat_command(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    monkeypatch.setenv("HMN_PUBLIC_URL", "https://master.example")
    SQLiteStore(db).save_node(
        Node(
            node_id="node_menu_timer",
            fingerprint="sha256:timer",
            hostname="menu-timer-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(app, [], input="7\n")

    assert result.exit_code == 0
    assert "node_menu_timer" in result.stdout
    assert "hermes-managed-network-heartbeat.timer" in result.stdout
    assert "HMN_ENABLE_EXEC=0" in result.stdout


def test_node_install_heartbeat_can_render_cron_adapter(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_cron",
            fingerprint="sha256:cron",
            hostname="cron-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        [
            "node",
            "install-heartbeat",
            "--db",
            str(db),
            "--master-url",
            "https://master.example",
            "--service-manager",
            "cron",
        ],
    )

    assert result.exit_code == 0
    assert "service_manager=cron" in result.stdout
    assert "crontab" in result.stdout
    assert "* * * * * /usr/local/bin/hmn-worker" in result.stdout
    assert "systemctl enable" not in result.stdout


def test_node_install_heartbeat_can_render_beacon_only_mode(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_beacon",
            fingerprint="sha256:beacon",
            hostname="beacon-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        ["node", "install-heartbeat", "--db", str(db), "--master-url", "https://master.example", "--beacon-only"],
    )

    assert result.exit_code == 0
    assert "beacon-only" in result.stdout
    assert "HMN_WORKER_MODE=beacon" in result.stdout
    assert "HMN_BEACON_ONLY=1" in result.stdout
    assert "不会 poll tasks" in result.stdout


def test_node_install_heartbeat_records_audit_event(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_timer_audit",
            fingerprint="sha256:timer-audit",
            hostname="timer-audit-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        ["node", "install-heartbeat", "--db", str(db), "--master-url", "https://master.example"],
    )

    assert result.exit_code == 0
    event = store.list_audit_events()[-1]
    assert event.event_type == "node"
    assert event.subject_id == "node_timer_audit"
    assert event.action == "install-heartbeat"
    assert event.outcome == "rendered"
    assert event.details == {
        "master_url": "https://master.example",
        "service_manager": "systemd",
        "enable_exec": False,
        "runtime": "full-worker",
        "endpoints": ["https://master.example"],
    }


def test_node_list_shows_offline_when_no_heartbeat_and_records_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_no_hb", hostname="no-hb-node"))

    result = runner.invoke(app, ["node", "list", "--db", str(db)])

    assert result.exit_code == 0
    assert "node_no_hb\tmanaged\tno-hb-node\ttrust=B\tliveness=offline" in result.stdout
    event = store.list_audit_events()[-1]
    assert event.action == "liveness"
    assert event.subject_id == "node_no_hb"
    assert event.outcome == "offline"
    assert event.details["reason"] == "missing_heartbeat"


def test_node_status_shows_stale_for_old_heartbeat_and_records_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_stale", hostname="stale-node"))
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_stale",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )
    with store.connect() as conn:
        conn.execute("UPDATE audit_events SET created_at = ? WHERE action = 'heartbeat'", ("2026-01-01T00:00:00+00:00",))

    result = runner.invoke(app, ["node", "status", "--db", str(db), "--now", "2026-01-01T00:06:00+00:00"])

    assert result.exit_code == 0
    assert "liveness: stale" in result.stdout
    assert "last_heartbeat: 2026-01-01T00:00:00+00:00" in result.stdout
    assert "runtime: proxy-managed" in result.stdout
    assert "service_manager: none" in result.stdout
    event = store.list_audit_events()[-1]
    assert event.action == "liveness"
    assert event.outcome == "stale"
    assert event.details["age_seconds"] == 360


def test_node_worker_status_reports_missing_heartbeat(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(_managed_node(node_id="node_no_hb", hostname="no-hb-node"))

    result = runner.invoke(app, ["node", "worker-status", "--db", str(db)])

    assert result.exit_code == 1
    assert "自动选择 managed 节点: node_no_hb (no-hb-node)" in result.stdout
    assert "worker: node_no_hb" in result.stdout
    assert "liveness: offline" in result.stdout
    assert "心跳: WARN 未收到" in result.stdout
    assert "worker: WARN 未安装或未上报" in result.stdout
    assert "执行: SAFE HMN_ENABLE_EXEC=0" in result.stdout


def test_node_worker_status_reports_ok_from_heartbeat_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_hb_ok", hostname="hb-ok-node"))
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_hb_ok",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_protocol_version": "0.1",
                "worker_version": "0.1.0",
                "exec_enabled": False,
                "capabilities": {
                    "os_family": "linux",
                    "has_sh": True,
                    "has_curl": True,
                    "has_python3": True,
                    "has_systemctl": True,
                    "writable_etc": True,
                },
            },
            "worker_compatible": True,
        },
    )

    result = runner.invoke(app, ["node", "worker-status", "--db", str(db)])

    assert result.exit_code == 0
    assert "worker: node_hb_ok" in result.stdout
    assert "心跳: OK" in result.stdout
    assert "worker: OK installed/reported" in result.stdout
    assert "协议: OK 0.1" in result.stdout
    assert "版本: 0.1.0" in result.stdout
    assert "系统: unknown" in result.stdout
    assert "runtime: full-worker" in result.stdout
    assert "service_manager: systemd" in result.stdout
    assert "worker_mode: unknown" in result.stdout
    assert "task_policy: poll-and-exec" in result.stdout
    assert "执行: SAFE HMN_ENABLE_EXEC=0" in result.stdout
    event = store.list_audit_events()[-2]
    assert event.action == "worker-status"
    assert event.details["runtime_profile"] == "full-worker"
    assert event.details["service_manager"] == "systemd"


def test_node_worker_status_reports_windows_beacon_details(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_windows_status", hostname="windows-status-node"))
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_windows_status",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_protocol_version": "0.1",
                "worker_version": "windows-beacon",
                "worker_mode": "beacon",
                "task_policy": "heartbeat-only",
                "exec_enabled": False,
                "os_release": "Microsoft Windows Server 2022",
                "capabilities": {"os_family": "windows", "has_powershell": True},
            },
            "worker_compatible": True,
        },
    )

    result = runner.invoke(app, ["node", "worker-status", "--db", str(db)])

    assert result.exit_code == 0
    assert "系统: Microsoft Windows Server 2022" in result.stdout
    assert "runtime: beacon-only" in result.stdout
    assert "service_manager: windows-task" in result.stdout
    assert "worker_mode: beacon" in result.stdout
    assert "task_policy: heartbeat-only" in result.stdout
    assert "Windows 说明: 当前为 Task Scheduler 心跳模式，不会执行远程下发命令。" in result.stdout


def test_node_worker_status_reports_windows_full_worker_mode(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_windows_full", hostname="windows-full-node"))
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_windows_full",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_protocol_version": "0.1",
                "worker_version": "windows-worker",
                "worker_mode": "worker",
                "task_policy": "poll-tasks",
                "can_poll_tasks": True,
                "exec_enabled": False,
                "os_release": "Microsoft Windows Server 2022",
                "capabilities": {"os_family": "windows", "has_powershell": True, "has_curl": True},
            },
            "worker_compatible": True,
        },
    )

    result = runner.invoke(app, ["node", "worker-status", "--db", str(db)])

    assert result.exit_code == 0
    assert "系统: Microsoft Windows Server 2022" in result.stdout
    assert "runtime: full-worker" in result.stdout
    assert "service_manager: windows-task" in result.stdout
    assert "worker_mode: worker" in result.stdout
    assert "task_policy: poll-tasks" in result.stdout
    assert "Windows 说明: 当前为 Task Scheduler worker 模式，可轮询签名任务；默认仍保持 HMN_ENABLE_EXEC=0。" in result.stdout


def test_node_worker_status_warns_for_incompatible_worker(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_hb_bad", hostname="hb-bad-node"))
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_hb_bad",
        action="heartbeat",
        outcome="warn",
        details={
            "status": "ok",
            "facts": {"worker_protocol_version": "9.9", "exec_enabled": True},
            "worker_compatible": False,
        },
    )

    result = runner.invoke(app, ["node", "worker-status", "--db", str(db)])

    assert result.exit_code == 1
    assert "心跳: WARN" in result.stdout
    assert "协议: WARN 9.9 incompatible" in result.stdout
    assert "执行: ENABLED HMN_ENABLE_EXEC=1" in result.stdout


def test_root_menu_can_show_worker_status(tmp_path, monkeypatch):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    store = SQLiteStore(db)
    store.save_node(_managed_node(node_id="node_menu_worker", hostname="menu-worker-node"))
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_menu_worker",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )

    result = runner.invoke(app, [], input="8\n")

    assert result.exit_code == 0
    assert "worker: node_menu_worker" in result.stdout
    assert "心跳: OK" in result.stdout


def test_task_run_creates_task_for_auto_selected_node(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_task_cli",
            fingerprint="sha256:task",
            hostname="task-cli-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(app, ["task", "run", "uptime", "--db", str(db)])

    assert result.exit_code == 0
    assert "已创建任务" in result.stdout
    tasks = SQLiteStore(db).list_tasks()
    assert len(tasks) == 1
    assert tasks[0].node_id == "node_task_cli"
    assert tasks[0].command == "uptime"
    assert tasks[0].status == "pending"


def test_task_list_shows_created_tasks(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_task_list",
            fingerprint="sha256:task",
            hostname="task-list-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    task = store.create_task(node_id="node_task_list", command="date", risk="low", created_by="test")

    result = runner.invoke(app, ["task", "list", "--db", str(db)])

    assert result.exit_code == 0
    assert task.task_id in result.stdout
    assert "node_task_list" in result.stdout
    assert "pending" in result.stdout


def test_wake_interactively_creates_token_and_safe_join_command(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"

    result = runner.invoke(
        app,
        ["wake", "--db", str(db), "--master-url", "http://master.internal:8765"],
        input="backup-node.example\n10.0.0.10\n\nB\nbackup,worker\nhermes\n30\n",
    )

    assert result.exit_code == 0
    assert "唤醒脚本已生成" in result.stdout
    assert "机器: backup-node.example" in result.stdout
    assert "地址: 10.0.0.10" in result.stdout
    assert "HERMES_JOIN_TOKEN=" in result.stdout
    assert "HERMES_MASTER_URL=http://master.internal:8765" in result.stdout
    assert "mktemp" in result.stdout
    assert "sha256sum" in result.stdout
    tokens = SQLiteStore(db).list_tokens()
    assert len(tokens) == 1
    assert tokens[0].trust_level == "B"
    assert tokens[0].labels == ["backup", "worker"]


def test_wake_defaults_to_generic_next_node_name(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"

    result = runner.invoke(
        app,
        ["wake", "--db", str(db), "--master-url", "http://master.internal:8765"],
        input="\n\n\n\n\n\n\n",
    )

    assert result.exit_code == 0
    assert "机器: node-server1" in result.stdout
    assert "s22900" not in result.stdout
    assert "23.165.105.105" not in result.stdout


def test_wake_uses_next_node_number_from_inventory(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(
        Node(
            node_id="node_existing",
            fingerprint="fp",
            hostname="already-there",
            addresses=["10.0.0.2"],
            trust_level="B",
            labels=[],
        )
    )

    result = runner.invoke(
        app,
        ["wake", "--db", str(db), "--master-url", "http://master.internal:8765"],
        input="\n\n\n\n\n\n\n",
    )

    assert result.exit_code == 0
    assert "机器: node-server2" in result.stdout


def test_node_rotate_fingerprint_command_updates_node_and_prints_env_update(tmp_path):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_cli_rotate",
            fingerprint="sha256:old-cli",
            hostname="cli-rotate-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(
        app,
        [
            "node",
            "rotate-fingerprint",
            "node_cli_rotate",
            "--db",
            str(db),
            "--new-fingerprint",
            "sha256:new-cli",
        ],
    )

    assert result.exit_code == 0
    assert store.load_node("node_cli_rotate").fingerprint == "sha256:new-cli"
    assert "fingerprint rotated: node_cli_rotate" in result.stdout
    assert "HERMES_NODE_FINGERPRINT=sha256:new-cli" in result.stdout
    event = store.list_audit_events()[-1]
    assert event.action == "rotate_fingerprint"
    assert event.outcome == "ok"
