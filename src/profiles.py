from pathlib import Path

# Claude Code's own ~/.claude, used when no profile directory is selected.
DEFAULT_PROFILE = "default"


class ProfileError(ValueError):
    """A Claude profile name that does not match a profile directory."""


class ProfileCatalog:
    """Claude Code profiles, one directory each (the layout used by cloak).

    Selecting a profile only means running `claude` with CLAUDE_CONFIG_DIR
    pointing at its directory, so the bridge does not need cloak itself.
    """

    def __init__(self, profiles_dir: Path) -> None:
        self.profiles_dir = profiles_dir.expanduser()

    def list_profiles(self) -> list[str]:
        if not self.profiles_dir.is_dir():
            return [DEFAULT_PROFILE]
        names = sorted(
            entry.name
            for entry in self.profiles_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
        return [DEFAULT_PROFILE, *names]

    def config_dir(self, name: str) -> Path | None:
        """Directory for CLAUDE_CONFIG_DIR, or None for the default profile."""
        if name == DEFAULT_PROFILE:
            return None
        if name not in self.list_profiles():
            raise ProfileError(f"Profilo sconosciuto: {name!r}")
        return (self.profiles_dir / name).resolve()
