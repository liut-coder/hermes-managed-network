from typer.testing import CliRunner

from hermes_managed_network.cli import app


def test_telegram_gateway_help_is_available():
    runner = CliRunner()

    result = runner.invoke(app, ["telegram-gateway", "--help"])

    assert result.exit_code == 0
    assert "poll-once" in result.stdout
    assert "run" in result.stdout


def test_telegram_gateway_poll_once_requires_token_without_env():
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "telegram-gateway",
            "poll-once",
            "--api-url",
            "http://127.0.0.1:8765",
            "--chat-id",
            "7500615916",
        ],
        env={"HMN_TELEGRAM_BOT_TOKEN": ""},
    )

    assert result.exit_code != 0
    assert "HMN_TELEGRAM_BOT_TOKEN" in result.stdout


def test_menu_plain_mentions_telegram_gateway():
    runner = CliRunner()

    result = runner.invoke(app, ["menu", "--plain"])

    assert result.exit_code == 0
    assert "hmn telegram-gateway poll-once" in result.stdout
