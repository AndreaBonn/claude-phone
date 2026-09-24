from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import PROJECT_ROOT, Settings, format_config_error


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:abc",
        "allowed_users": "42",
        "approved_directory": str(tmp_path),
    }
    values.update(overrides)
    # model_validate skips env and .env sources, so tests never read real secrets.
    return Settings.model_validate(values)


def test_settings_parses_comma_separated_lists(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, allowed_users="42, 7", claude_allowed_tools="Read, Bash ,Edit"
    )
    assert settings.allowed_users == frozenset({42, 7})
    assert settings.claude_allowed_tools == ("Read", "Bash", "Edit")


def test_settings_parses_and_resolves_several_roots(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    other = tmp_path / "Other"
    other.mkdir()
    settings = make_settings(tmp_path, approved_directory=f"{tmp_path / 'link'}, {other}")
    assert settings.approved_directory == (real.resolve(), other.resolve())


def test_settings_rejects_missing_approved_directory(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="APPROVED_DIRECTORY"):
        make_settings(tmp_path, approved_directory=str(tmp_path / "missing"))


def test_settings_rejects_roots_with_the_same_name(tmp_path: Path) -> None:
    (tmp_path / "a" / "Progetti").mkdir(parents=True)
    (tmp_path / "b" / "Progetti").mkdir(parents=True)
    roots = f"{tmp_path / 'a' / 'Progetti'},{tmp_path / 'b' / 'Progetti'}"
    with pytest.raises(ValidationError, match="same name"):
        make_settings(tmp_path, approved_directory=roots)


def test_settings_rejects_nested_roots(tmp_path: Path) -> None:
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    roots = f"{tmp_path / 'outer'},{tmp_path / 'outer' / 'inner'}"
    with pytest.raises(ValidationError, match="nested"):
        make_settings(tmp_path, approved_directory=roots)


def test_settings_accepts_a_root_that_contains_the_bridge(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, approved_directory=str(PROJECT_ROOT.parent))
    assert settings.approved_directory == (PROJECT_ROOT.parent,)


def test_settings_rejects_a_root_inside_the_bridge(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="bridge"):
        make_settings(tmp_path, approved_directory=str(PROJECT_ROOT / "src"))


def test_settings_rejects_empty_whitelist(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="ALLOWED_USERS"):
        make_settings(tmp_path, allowed_users="")


def test_settings_rejects_non_numeric_user_id(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, allowed_users="alice")


def test_settings_rejects_out_of_range_verbose(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, verbose_level=3)


def test_settings_empty_api_key_becomes_none(tmp_path: Path) -> None:
    assert make_settings(tmp_path, anthropic_api_key="").anthropic_api_key is None
    key = make_settings(tmp_path, anthropic_api_key="sk-x").anthropic_api_key
    assert key is not None and key.get_secret_value() == "sk-x"


def test_settings_relative_paths_anchor_to_project_root(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, db_path="data/x.db")
    assert settings.db_path == PROJECT_ROOT / "data" / "x.db"


def test_settings_token_is_not_in_repr(tmp_path: Path) -> None:
    assert "123:abc" not in repr(make_settings(tmp_path))


def test_format_config_error_never_prints_input_values(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(
            tmp_path, telegram_bot_token="9:TOPSECRET", approved_directory=str(PROJECT_ROOT / "src")
        )
    text = format_config_error(caught.value)
    assert "TOPSECRET" not in text
    assert "inside the bridge" in text
