from pathlib import Path

# Claude Code's own ~/.claude, used when no profile directory is selected.
DEFAULT_PROFILE = "default"
DEFAULT_CONFIG_DIR = Path("~/.claude")
# Parts of a Claude config that skills and rules read at runtime. Credentials,
# settings and transcripts are deliberately not listed.
READABLE_CONFIG_SUBDIRS = ("skills", "rules", "rules-detail", "agents", "commands", "plugins")


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

    def readonly_config_dirs(self, default_config: Path = DEFAULT_CONFIG_DIR) -> tuple[Path, ...]:
        """Existing skill/rule/plugin directories of ~/.claude and of every profile.

        Symlinks are resolved, so a profile sharing ~/.claude/skills adds nothing new.
        """
        bases = [default_config.expanduser()]
        bases += [self.profiles_dir / name for name in self.list_profiles()[1:]]
        found = {
            (base / sub).resolve()
            for base in bases
            for sub in READABLE_CONFIG_SUBDIRS
            if (base / sub).is_dir()
        }
        return tuple(sorted(found))
