from pathlib import Path

import pytest

from src.project_manager import ProjectManager, SandboxError, is_within, resolve_in_sandbox


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "sandbox"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    (root / ".hidden").mkdir()
    (root / "notes.txt").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    return root.resolve()


def test_list_projects_returns_only_visible_dirs_inside_sandbox(sandbox: Path) -> None:
    assert ProjectManager(sandbox).list_projects() == ["alpha", "beta"]


def test_resolve_project_returns_resolved_path(sandbox: Path) -> None:
    assert ProjectManager(sandbox).resolve_project("alpha") == sandbox / "alpha"


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "..", ".", "", "alpha/../..", "/etc", ".hidden", "escape"]
)
def test_resolve_project_rejects_unsafe_names(sandbox: Path, name: str) -> None:
    with pytest.raises(SandboxError):
        ProjectManager(sandbox).resolve_project(name)


def test_resolve_project_rejects_missing_project(sandbox: Path) -> None:
    with pytest.raises(SandboxError, match="non esiste"):
        ProjectManager(sandbox).resolve_project("gamma")


def test_resolve_in_sandbox_accepts_relative_path(sandbox: Path) -> None:
    resolved = resolve_in_sandbox(raw="src/app.py", cwd=sandbox / "alpha", root=sandbox)
    assert resolved == sandbox / "alpha" / "src" / "app.py"


def test_resolve_in_sandbox_accepts_sibling_project(sandbox: Path) -> None:
    resolved = resolve_in_sandbox(raw="../beta/x", cwd=sandbox / "alpha", root=sandbox)
    assert resolved == sandbox / "beta" / "x"


@pytest.mark.parametrize("raw", ["../../etc/passwd", "/etc/passwd", "~/.ssh/id_rsa", "../.."])
def test_resolve_in_sandbox_rejects_traversal(sandbox: Path, raw: str) -> None:
    with pytest.raises(SandboxError):
        resolve_in_sandbox(raw=raw, cwd=sandbox / "alpha", root=sandbox)


def test_resolve_in_sandbox_rejects_symlink_escape(sandbox: Path) -> None:
    with pytest.raises(SandboxError):
        resolve_in_sandbox(raw="escape/secret", cwd=sandbox, root=sandbox)


def test_is_within_accepts_root_itself(sandbox: Path) -> None:
    assert is_within(path=sandbox, root=sandbox) is True
    assert is_within(path=sandbox.parent, root=sandbox) is False
