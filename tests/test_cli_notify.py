"""`cosmo notify watch` (v5 improvements plan part 3): the CLI's own guard
rails -- refuses to start rather than run uselessly. The watch loop itself
is `test_notify_watch.py`'s job."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo.cli.main import app

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
