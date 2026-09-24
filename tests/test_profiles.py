from pathlib import Path

import pytest

from src.profiles import DEFAULT_PROFILE, ProfileCatalog, ProfileError


@pytest.fixture
def catalog(tmp_path: Path) -> ProfileCatalog:
    for name in ("sales", "delivery"):
        (tmp_path / name).mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    return ProfileCatalog(tmp_path)


def test_list_profiles_puts_default_first(catalog: ProfileCatalog) -> None:
    assert catalog.list_profiles() == [DEFAULT_PROFILE, "delivery", "sales"]


def test_config_dir_maps_names_to_directories(catalog: ProfileCatalog, tmp_path: Path) -> None:
    assert catalog.config_dir(DEFAULT_PROFILE) is None
    assert catalog.config_dir("sales") == (tmp_path / "sales").resolve()


@pytest.mark.parametrize("name", ["missing", "../sales", ".hidden", "notes.txt", ""])
def test_config_dir_rejects_unknown_profiles(catalog: ProfileCatalog, name: str) -> None:
    with pytest.raises(ProfileError):
        catalog.config_dir(name)


def test_missing_profiles_dir_offers_only_default(tmp_path: Path) -> None:
    assert ProfileCatalog(tmp_path / "nope").list_profiles() == [DEFAULT_PROFILE]
