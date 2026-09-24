from pathlib import Path

import pytest

from src.project_manager import ProjectManager, Sandbox, SandboxError


@pytest.fixture
def base(tmp_path: Path) -> Path:
    work = tmp_path / "Progetti"
    (work / "alpha").mkdir(parents=True)
    (work / ".hidden").mkdir()
    (work / "notes.txt").write_text("x")
    personal = tmp_path / "Personali"
    (personal / "alpha").mkdir(parents=True)
    (personal / "bridge").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / "escape").symlink_to(outside)
    return tmp_path.resolve()


@pytest.fixture
def sandbox(base: Path) -> Sandbox:
    return Sandbox(
        roots=(base / "Progetti", base / "Personali"),
        excluded=(base / "Personali" / "bridge",),
    )


def test_list_projects_spans_roots_and_skips_excluded(sandbox: Sandbox) -> None:
    assert ProjectManager(sandbox).list_projects() == ["Progetti/alpha", "Personali/alpha"]


def test_resolve_project_returns_path_in_the_right_root(sandbox: Sandbox, base: Path) -> None:
    projects = ProjectManager(sandbox)
    assert projects.resolve_project("Personali/alpha") == base / "Personali" / "alpha"
    assert projects.resolve_project("Progetti/alpha") == base / "Progetti" / "alpha"


@pytest.mark.parametrize(
    "project_id",
    [
        "../../etc/passwd",
        "Progetti/..",
        "Progetti/.",
        "Progetti/",
        "Progetti",
        "",
        "Progetti/alpha/..",
        "/etc/passwd",
        "Progetti/.hidden",
        "Progetti/escape",
        "Personali/bridge",
        "Unknown/alpha",
    ],
)
def test_resolve_project_rejects_unsafe_ids(sandbox: Sandbox, project_id: str) -> None:
    with pytest.raises(SandboxError):
        ProjectManager(sandbox).resolve_project(project_id)


def test_resolve_project_rejects_missing_project(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxError, match="non esiste"):
        ProjectManager(sandbox).resolve_project("Progetti/gamma")


def test_sandbox_resolve_accepts_relative_path(sandbox: Sandbox, base: Path) -> None:
    cwd = base / "Progetti" / "alpha"
    assert sandbox.resolve(raw="src/app.py", cwd=cwd) == cwd / "src" / "app.py"


def test_sandbox_resolve_accepts_another_root(sandbox: Sandbox, base: Path) -> None:
    cwd = base / "Progetti" / "alpha"
    target = base / "Personali" / "alpha" / "x"
    assert sandbox.resolve(raw="../../Personali/alpha/x", cwd=cwd) == target


@pytest.mark.parametrize(
    "raw", ["../../etc/passwd", "/etc/passwd", "~/.ssh/id_rsa", "../..", "escape/secret"]
)
def test_sandbox_resolve_rejects_paths_outside_roots(
    sandbox: Sandbox, base: Path, raw: str
) -> None:
    with pytest.raises(SandboxError):
        sandbox.resolve(raw=raw, cwd=base / "Progetti")


def test_sandbox_resolve_rejects_excluded_directory(sandbox: Sandbox, base: Path) -> None:
    with pytest.raises(SandboxError):
        sandbox.resolve(raw="../bridge/src/permission_hook.py", cwd=base / "Personali" / "alpha")


def test_sandbox_contains_roots_but_not_their_parent(sandbox: Sandbox, base: Path) -> None:
    assert sandbox.contains(base / "Progetti") is True
    assert sandbox.contains(base) is False
    assert sandbox.contains(base / "Personali" / "bridge" / "x") is False
