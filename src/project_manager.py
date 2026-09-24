from pathlib import Path


class SandboxError(ValueError):
    """A path or project name that resolves outside APPROVED_DIRECTORY."""


def is_within(path: Path, root: Path) -> bool:
    """Tell whether `path`, with symlinks resolved, lies inside `root` (already resolved)."""
    return path.resolve().is_relative_to(root)


def resolve_in_sandbox(raw: str, cwd: Path, root: Path) -> Path:
    """Resolve a path coming from Telegram or Claude and enforce the sandbox.

    Parameters
    ----------
    raw : str
        Path as received, absolute, relative to `cwd` or starting with `~`.
    cwd : Path
        Directory relative paths are interpreted against.
    root : Path
        Resolved sandbox root.

    Returns
    -------
    Path
        The resolved absolute path, symlinks followed.

    Raises
    ------
    SandboxError
        If the resolved path is outside `root`.
    """
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise SandboxError(f"Percorso fuori dalla sandbox: {raw}")
    return resolved


class ProjectManager:
    """Projects are the direct, visible sub-directories of the sandbox root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def list_projects(self) -> list[str]:
        return sorted(
            entry.name
            for entry in self.root.iterdir()
            if entry.is_dir() and not entry.name.startswith(".") and is_within(entry, self.root)
        )

    def resolve_project(self, name: str) -> Path:
        if not name or "/" in name or name.startswith("."):
            raise SandboxError(f"Nome progetto non valido: {name!r}")
        path = resolve_in_sandbox(raw=name, cwd=self.root, root=self.root)
        if path == self.root or path.parent != self.root:
            raise SandboxError(f"Nome progetto non valido: {name!r}")
        if not path.is_dir():
            raise SandboxError(f"Il progetto {name!r} non esiste")
        return path
