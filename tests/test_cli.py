import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.storage import SQLiteStore


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
    assert "hmn update" in result.stdout
    assert "hmn uninstall" in result.stdout


def test_menu_plain_prints_quick_actions():
    runner = CliRunner()

    result = runner.invoke(app, ["menu", "--plain"])

    assert result.exit_code == 0
    assert "HMN 快速菜单" in result.stdout
    assert "hmn wake" in result.stdout
    assert "hmn node confirm" in result.stdout
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

    result = runner.invoke(app, [], input="4\n")

    assert result.exit_code == 0
    assert token_value in result.stdout
    assert "join_token" in result.stdout


def test_root_menu_can_create_token_without_optioninfo(tmp_path, monkeypatch):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    monkeypatch.setenv("HMN_DB", str(db))

    result = runner.invoke(app, [], input="5\n")

    assert result.exit_code == 0
    token_value = result.stdout.strip().splitlines()[-1]
    assert token_value.startswith("j_")
    assert SQLiteStore(db).load_token(token_value).trust_level == "B"


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

def test_wake_interactively_creates_token_and_safe_join_command(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"

    result = runner.invoke(
        app,
        ["wake", "--db", str(db), "--master-url", "http://master.internal:8765"],
        input="s22900.dartnode.com\n23.165.105.105\n\nB\nd2,worker,s22900\nhermes\n30\n",
    )

    assert result.exit_code == 0
    assert "唤醒脚本已生成" in result.stdout
    assert "机器: s22900.dartnode.com" in result.stdout
    assert "地址: 23.165.105.105" in result.stdout
    assert "HERMES_JOIN_TOKEN=" in result.stdout
    assert "HERMES_MASTER_URL=http://master.internal:8765" in result.stdout
    assert "mktemp" in result.stdout
    assert "sha256sum" in result.stdout
    tokens = SQLiteStore(db).list_tokens()
    assert len(tokens) == 1
    assert tokens[0].trust_level == "B"
    assert tokens[0].labels == ["d2", "worker", "s22900"]


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
