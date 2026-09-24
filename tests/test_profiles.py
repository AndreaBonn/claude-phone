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


def test_readonly_config_dirs_cover_default_and_profiles(tmp_path: Path) -> None:
    home_config = tmp_path / "home" / ".claude"
    (home_config / "skills").mkdir(parents=True)
    (home_config / "rules").mkdir()
    (home_config / "projects").mkdir()
    profiles = tmp_path / "profiles"
    (profiles / "sales" / "plugins").mkdir(parents=True)
    (profiles / "sales" / "skills").symlink_to(home_config / "skills")
    dirs = ProfileCatalog(profiles).readonly_config_dirs(default_config=home_config)
    assert set(dirs) == {
        (home_config / "skills").resolve(),
        (home_config / "rules").resolve(),
        (profiles / "sales" / "plugins").resolve(),
    }
