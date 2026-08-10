from pathlib import Path

import pytest

from policy_data.config import Settings


def test_production_secrets_can_be_loaded_from_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cursor = tmp_path / "cursor"
    pepper = tmp_path / "pepper"
    resend = tmp_path / "resend"
    cursor.write_text("c" * 32)
    pepper.write_text("p" * 32)
    resend.write_text("re_live_test")
    monkeypatch.setenv("POLICY_DATA_ENV", "production")
    monkeypatch.setenv("CURSOR_SECRET_FILE", str(cursor))
    monkeypatch.setenv("AUTH_PEPPER_FILE", str(pepper))
    monkeypatch.setenv("RESEND_API_KEY_FILE", str(resend))

    settings = Settings.from_environment()

    assert settings.cursor_secret == b"c" * 32
    assert settings.auth_pepper == b"p" * 32
    assert settings.resend_api_key == "re_live_test"


def test_production_rejects_missing_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICY_DATA_ENV", "production")
    for name in (
        "CURSOR_SECRET",
        "CURSOR_SECRET_FILE",
        "AUTH_PEPPER",
        "AUTH_PEPPER_FILE",
        "RESEND_API_KEY",
        "RESEND_API_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="CURSOR_SECRET"):
        Settings.from_environment()
