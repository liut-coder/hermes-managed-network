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
    assert "HERMES_JOIN_TOKEN='" in result.stdout
    assert token_value in result.stdout
    assert "HERMES_MASTER_URL='https://example.com'" in result.stdout
    assert "bash -s < <(curl -fsSL https://example.com/scripts/join.sh)" in result.stdout


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
