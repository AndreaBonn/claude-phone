import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.file_intake import MAX_NAME_BYTES, UPLOAD_DIR, safe_filename, write_unique
from src.project_manager import Sandbox, SandboxError

FALLBACK = "document_abc"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("/etc/shadow", "shadow"),
        ("a/b\\c.txt", "c.txt"),
        (".envrc", "envrc"),
        ("...hidden", "hidden"),
        ("  spaced.md  ", "spaced.md"),
        ("nul\x00byte.txt", "nulbyte.txt"),
        ("line\nbreak.txt", "linebreak.txt"),
        ("evil‮txt.exe", "eviltxt.exe"),
        ("zero​width.md", "zerowidth.md"),
        ("café.txt", "café.txt"),
        ("", FALLBACK),
        (None, FALLBACK),
        ("..", FALLBACK),
        ("dir/", FALLBACK),
        ("​", FALLBACK),
    ],
)
def test_safe_filename_keeps_only_a_plain_basename(raw: str | None, expected: str) -> None:
    assert safe_filename(raw=raw, fallback=FALLBACK) == expected


def test_safe_filename_truncates_long_names_keeping_the_extension() -> None:
    name = safe_filename(raw="a" * 400 + ".pdf", fallback=FALLBACK)
    assert name.endswith(".pdf")
    assert len(name.encode()) == MAX_NAME_BYTES
    assert name.startswith("aaaa")


def test_safe_filename_truncates_on_a_character_boundary() -> None:
    name = safe_filename(raw="è" * 300 + ".txt", fallback=FALLBACK)
    assert len(name.encode()) <= MAX_NAME_BYTES
    assert name.endswith(".txt")
    assert set(name.removesuffix(".txt")) == {"è"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    directory = tmp_path / "root" / "alpha"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(roots=((tmp_path / "root").resolve(),))


def test_write_unique_creates_uploads_and_writes_the_bytes(project: Path, sandbox: Sandbox) -> None:
    saved = write_unique(directory=project, name="report.pdf", data=b"%PDF", sandbox=sandbox)
    assert saved == (project / UPLOAD_DIR / "report.pdf").resolve()
    assert saved.read_bytes() == b"%PDF"
    # 0o644 at most: the umask may only remove bits, and nothing is executable.
    assert stat.S_IMODE(saved.stat().st_mode) & ~0o644 == 0


def test_write_unique_never_overwrites_and_numbers_the_copies(
    project: Path, sandbox: Sandbox
) -> None:
    names = [
        write_unique(directory=project, name="report.pdf", data=bytes([i]), sandbox=sandbox).name
        for i in range(3)
    ]
    assert names == ["report.pdf", "report (1).pdf", "report (2).pdf"]
    assert (project / UPLOAD_DIR / "report.pdf").read_bytes() == b"\x00"


def test_write_unique_numbers_names_without_extension(project: Path, sandbox: Sandbox) -> None:
    for _ in range(2):
        saved = write_unique(directory=project, name="Makefile", data=b"x", sandbox=sandbox)
    assert saved.name == "Makefile (1)"


def test_write_unique_concurrent_writers_get_distinct_files(
    project: Path, sandbox: Sandbox
) -> None:
    def write(index: int) -> Path:
        return write_unique(
            directory=project, name="a.txt", data=str(index).encode(), sandbox=sandbox
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        saved = list(pool.map(write, range(16)))
    assert len(set(saved)) == 16
    assert sorted(p.read_text() for p in saved) == sorted(str(i) for i in range(16))


def test_write_unique_refuses_uploads_symlinked_outside_the_sandbox(
    project: Path, sandbox: Sandbox, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / UPLOAD_DIR).symlink_to(outside)
    with pytest.raises(SandboxError):
        write_unique(directory=project, name="x.txt", data=b"x", sandbox=sandbox)
    assert list(outside.iterdir()) == []


def test_write_unique_does_not_follow_a_dangling_file_symlink(
    project: Path, sandbox: Sandbox, tmp_path: Path
) -> None:
    uploads = project / UPLOAD_DIR
    uploads.mkdir()
    target = tmp_path / "root" / "planted.txt"
    (uploads / "x.txt").symlink_to(target)
    saved = write_unique(directory=project, name="x.txt", data=b"x", sandbox=sandbox)
    assert saved.name == "x (1).txt"
    assert not target.exists()


def test_write_unique_removes_a_partial_file_when_the_write_fails(
    project: Path, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full_disk(_fd: int, _data: bytes) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "write", full_disk)
    with pytest.raises(OSError, match="No space"):
        write_unique(directory=project, name="x.txt", data=b"x", sandbox=sandbox)
    assert list((project / UPLOAD_DIR).iterdir()) == []


def test_write_unique_refuses_uploads_linked_to_another_project(
    project: Path, sandbox: Sandbox
) -> None:
    other = project.parent / "beta"
    other.mkdir()
    (project / UPLOAD_DIR).symlink_to(other)
    with pytest.raises(SandboxError):
        write_unique(directory=project, name="x.txt", data=b"x", sandbox=sandbox)
    assert list(other.iterdir()) == []
