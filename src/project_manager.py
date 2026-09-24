from dataclasses import dataclass
from pathlib import Path

PROJECT_SEPARATOR = "/"


class SandboxError(ValueError):
    """A path or project that resolves outside the sandbox roots."""


@dataclass(frozen=True)
class Sandbox:
    """The directories Claude may touch: several roots minus excluded sub-trees.

    Paths are compared after resolving symlinks, so a link pointing outside a
    root, or into an excluded directory, is treated as what it points to.
    """

    roots: tuple[Path, ...]
    excluded: tuple[Path, ...] = ()
    # Readable but never writable, e.g. the skills and rules of Claude's own config.
    read_only: tuple[Path, ...] = ()

    def contains(self, path: Path, read_only_ok: bool = False) -> bool:
        resolved = path.resolve()
        allowed = self.roots + self.read_only if read_only_ok else self.roots
        inside = any(resolved.is_relative_to(root) for root in allowed)
        return inside and not any(resolved.is_relative_to(ex) for ex in self.excluded)

    def resolve(self, raw: str, cwd: Path, read_only_ok: bool = False) -> Path:
        """Resolve a path from Telegram or Claude and enforce the sandbox.

        Parameters
        ----------
        raw : str
            Path as received: absolute, relative to `cwd`, or starting with `~`.
        cwd : Path
            Directory relative paths are interpreted against.
        read_only_ok : bool
            Accept the read-only directories too (for reading tools).

        Returns
        -------
        Path
            The resolved absolute path, symlinks followed.

        Raises
        ------
        SandboxError
            If the path is outside every root or inside an excluded directory.
        """
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve()
        if not self.contains(resolved, read_only_ok=read_only_ok):
            raise SandboxError(f"Percorso fuori dalla sandbox: {raw}")
        return resolved


class ProjectManager:
    """Projects are the visible direct sub-directories of each sandbox root.

    A project id is `<root name>/<directory>`, so equal directory names in
    different roots stay distinct.
    """

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self._roots = {root.name: root for root in sandbox.roots}

    def list_projects(self) -> list[str]:
        return [
            f"{root.name}{PROJECT_SEPARATOR}{entry.name}"
            for root in self.sandbox.roots
            for entry in sorted(root.iterdir())
            if entry.is_dir() and not entry.name.startswith(".") and self.sandbox.contains(entry)
        ]

    def resolve_project(self, project_id: str) -> Path:
        root_name, _, name = project_id.partition(PROJECT_SEPARATOR)
        root = self._roots.get(root_name)
        if root is None or not name or PROJECT_SEPARATOR in name or name.startswith("."):
            raise SandboxError(f"Progetto non valido: {project_id!r}")
        path = self.sandbox.resolve(raw=name, cwd=root)
        if path.parent != root:
            raise SandboxError(f"Progetto non valido: {project_id!r}")
        if not path.is_dir():
            raise SandboxError(f"Il progetto {project_id!r} non esiste")
        return path
