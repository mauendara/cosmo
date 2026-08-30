"""`cosmo notify watch`/`cosmo notify config` (v5 improvements plan part 3):
the CLI's own guard rails -- `watch` refuses to start rather than run
uselessly, `config` is the interactive setup wizard. The watch loop itself
is `test_notify_watch.py`'s job; the Telegram API calls the wizard makes are
always mocked here (`test_notify_setup.py` covers those for real, against a
fake `urlopen`)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cosmo.cli.main import app
from cosmo.notify.setup import TelegramApiError

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_refuses_to_start_when_notify_is_disabled() -> None:
    result = runner.invoke(app, ["notify", "watch"])
    assert result.exit_code == 1
    assert "notify.enabled is false" in result.output


def test_refuses_to_start_without_telegram_credentials(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[notify]\nenabled = true\n")

    result = runner.invoke(app, ["notify", "watch", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "telegram_bot_token" in result.output


def test_config_wizard_writes_the_file_and_confirms_with_a_real_test_message(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    # Bot token, then (severity, stale-after) accept their shown defaults --
    # no confirm prompt: a fresh config has no existing chat id to reuse.
    stdin = "123456:fake-token\n\n\n"

    with (
        patch("cosmo.cli.main.discover_chat_id", return_value="chat-1") as fake_discover,
        patch("cosmo.cli.main.send_test_message") as fake_send,
    ):
        result = runner.invoke(app, ["notify", "config", "--config", str(config_path)], input=stdin)

    assert result.exit_code == 0, result.output
    assert "test message sent" in result.output
    fake_discover.assert_called_once_with("123456:fake-token")
    fake_send.assert_called_once_with("123456:fake-token", "chat-1")

    written = config_path.read_text()
    assert 'telegram_bot_token = "123456:fake-token"' in written
    assert 'telegram_chat_id = "chat-1"' in written
    assert "enabled = true" in written


def test_config_wizard_reuses_an_existing_chat_id_when_confirmed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[notify]\nenabled = true\ntelegram_bot_token = "old-token"\n'
        'telegram_chat_id = "existing-chat"\n'
    )
    # token, confirm-reuse=yes, severity default, stale default.
    stdin = "new-token\ny\n\n\n"

    with (
        patch("cosmo.cli.main.discover_chat_id") as fake_discover,
        patch("cosmo.cli.main.send_test_message"),
    ):
        result = runner.invoke(app, ["notify", "config", "--config", str(config_path)], input=stdin)

    assert result.exit_code == 0, result.output
    fake_discover.assert_not_called()
    assert 'telegram_chat_id = "existing-chat"' in config_path.read_text()


def test_config_wizard_walks_through_discovery_when_no_messages_found_yet(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    # token, [press enter after messaging the bot], severity default, stale default.
    stdin = "token\n\n\n\n"

    with (
        patch("cosmo.cli.main.discover_chat_id", side_effect=[None, "chat-found"]) as fake_discover,
        patch("cosmo.cli.main.send_test_message"),
    ):
        result = runner.invoke(app, ["notify", "config", "--config", str(config_path)], input=stdin)

    assert result.exit_code == 0, result.output
    assert fake_discover.call_count == 2
    assert "No messages found yet" in result.output
    assert 'telegram_chat_id = "chat-found"' in config_path.read_text()


def test_config_wizard_surfaces_a_rejected_token(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    stdin = "bad-token\n"

    with patch(
        "cosmo.cli.main.discover_chat_id",
        side_effect=TelegramApiError("Telegram API rejected the request: Unauthorized"),
    ):
        result = runner.invoke(app, ["notify", "config", "--config", str(config_path)], input=stdin)

    assert result.exit_code == 1
    assert "Unauthorized" in result.output
    assert not config_path.exists()


def test_config_wizard_reports_when_config_wrote_but_the_test_message_failed(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    stdin = "token\n\n\n"

    with (
        patch("cosmo.cli.main.discover_chat_id", return_value="chat-1"),
        patch(
            "cosmo.cli.main.send_test_message",
            side_effect=TelegramApiError("Telegram API rejected the message: chat not found"),
        ),
    ):
        result = runner.invoke(app, ["notify", "config", "--config", str(config_path)], input=stdin)

    assert result.exit_code == 1
    assert "wrote config, but the test message failed" in result.output
    # The config was still written -- the failure is in verification, not setup.
    assert config_path.exists()
