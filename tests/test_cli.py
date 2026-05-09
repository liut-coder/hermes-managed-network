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
