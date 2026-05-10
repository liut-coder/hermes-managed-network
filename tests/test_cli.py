import json

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
    event = json.loads(result.stdout.strip())
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
    assert "hmn version" in result.stdout
    assert "示例" in result.stdout


def test_command_help_includes_examples():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "示例" in result.stdout
    assert "hmn node confirm" in result.stdout


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
    )
    SQLiteStore(db).save_node(node)

    result = runner.invoke(app, ["node", "status", "--db", str(db)])

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_status (status-node)" in result.stdout
    assert "node: node_status" in result.stdout
    assert "status: managed" in result.stdout
    assert "host: status-node" in result.stdout
    assert "labels: backup, worker" in result.stdout
    assert "addresses: 10.0.0.3" in result.stdout
    assert "bundles: observe" in result.stdout


def test_node_status_without_id_prompts_when_multiple_managed_nodes(tmp_path):
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
                status="managed",
            )
        )

    result = runner.invoke(app, ["node", "status", "--db", str(db)], input="2\n")

    assert result.exit_code == 0
    assert "有多个 managed 节点，请选择：" in result.stdout
    assert "node: node_b" in result.stdout
    assert "host: b" in result.stdout


def test_root_menu_can_show_node_status(tmp_path, monkeypatch):
    from hermes_managed_network.inventory import Node

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))
    SQLiteStore(db).save_node(
        Node(
            node_id="node_menu_status",
            fingerprint="fp",
            hostname="menu-status-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
        )
    )

    result = runner.invoke(app, [], input="4\n")

    assert result.exit_code == 0
    assert "node: node_menu_status" in result.stdout
    assert "host: menu-status-node" in result.stdout



def test_node_doctor_auto_selects_and_records_audit(tmp_path):
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
        )
    )

    result = runner.invoke(app, ["node", "doctor", "--db", str(db)])

    assert result.exit_code == 0
    assert "自动选择 managed 节点: node_doctor (doctor-node)" in result.stdout
    assert "doctor: node_doctor" in result.stdout
    assert "登记状态: OK" in result.stdout
    assert "权限包: OK" in result.stdout
    events = SQLiteStore(db).list_audit_events()
    assert events[-1].action == "doctor"
    assert events[-1].subject_id == "node_doctor"
    assert events[-1].outcome == "ok"


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
        )
    )

    result = runner.invoke(app, [], input="5\n")

    assert result.exit_code == 0
    assert "doctor: node_menu_doctor" in result.stdout


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
    assert "sudo bash -lc" in result.stdout
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
    assert "runtime: full-worker" in result.stdout
    assert "service_manager: systemd" in result.stdout
    assert "执行: SAFE HMN_ENABLE_EXEC=0" in result.stdout
    event = store.list_audit_events()[-2]
    assert event.action == "worker-status"
    assert event.details["runtime_profile"] == "full-worker"
    assert event.details["service_manager"] == "systemd"


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
