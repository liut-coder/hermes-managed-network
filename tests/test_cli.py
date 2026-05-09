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

    result = runner.invoke(app, ["menu"])

    assert result.exit_code == 0
    assert "HMN 快速菜单" in result.stdout
    assert "audit list" in result.stdout


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
