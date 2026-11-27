"""File layout, parsing, locking and atomic writes shared by the scanner and the applier.

The rules mirror the built-in memory tool so both can work on the same files safely:

* entries are separated by ``"\\n§\\n"`` (a line holding only ``§``); parsing strips each
  entry and drops empty ones, so a bare ``§`` inside an entry survives;
* a file's size is ``len("\\n§\\n".join(entries))`` and must stay within the configured budget;
* writers hold an exclusive ``flock`` on a sibling ``<file>.lock`` (e.g. ``MEMORY.md.lock``)
  and replace the file atomically (temp file + rename) so readers never see a partial file.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore
try:  # Windows
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None  # type: ignore

ENTRY_DELIMITER = "\n§\n"
TARGETS = ("memory", "user")
FILE_NAMES = {"memory": "MEMORY.md", "user": "USER.md"}
SOUL_NAME = "SOUL.md"
SKILLS_DIR = "skills"
DATA_DIR = "memory-tidy"

# Hermes' own defaults when config.yaml does not set memory.memory_char_limit / user_char_limit.
DEFAULT_LIMITS = {"memory": 2200, "user": 1375}


def hermes_home() -> Path:
    """Active profile's HERMES_HOME (resolved per call; profile/multiplex safe inside Hermes)."""
    try:
        from hermes_constants import get_hermes_home  # provided by Hermes at runtime

        return Path(get_hermes_home())
    except Exception:
        env = os.environ.get("HERMES_HOME", "").strip()
        return Path(os.path.expanduser(env)) if env else Path.home() / ".hermes"


@dataclass(frozen=True)
class Layout:
    home: Path

    @property
    def memories(self) -> Path:
        return self.home / "memories"

    def memory_file(self, target: str) -> Path:
        if target not in TARGETS:
            raise ValueError(f"unknown target {target!r} (use 'memory' or 'user')")
        return self.memories / FILE_NAMES[target]

    @property
    def soul(self) -> Path:
        return self.home / SOUL_NAME

    @property
    def skills(self) -> Path:
        return self.home / SKILLS_DIR

    @property
    def data(self) -> Path:
        return self.home / DATA_DIR

    @property
    def backups(self) -> Path:
        return self.data / "backups"

    @property
    def state_file(self) -> Path:
        return self.data / "state.json"


_TRUTHY = frozenset({"on", "true", "yes", "1", "approve", "enabled"})


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in _TRUTHY


def write_approval_enabled(home: Path, subsystem: str) -> bool:
    """Whether ``<subsystem>.write_approval`` ("memory" or "skills") is on for ``home``.

    Same source order as :func:`read_limits`: the home's own config.yaml, then Hermes' own gate
    (``tools.write_approval``) when ``home`` is the active profile. Values are read the way Hermes
    reads them (bools and on/true/yes/1/approve/enabled). A config.yaml that exists but cannot be
    parsed counts as ON: an approval gate fails closed.
    """
    cfg_path = Path(home) / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml  # type: ignore

            with open(cfg_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception:
            return True
        section = cfg.get(subsystem) if isinstance(cfg, dict) else None
        if isinstance(section, dict) and "write_approval" in section:
            return _truthy(section.get("write_approval"))
    try:
        same_home = Path(home).resolve() == hermes_home().resolve()
    except OSError:
        same_home = False
    if same_home:
        try:
            from tools.write_approval import write_approval_enabled as _hermes_gate  # type: ignore

            return bool(_hermes_gate(subsystem))
        except Exception:
            pass
    return False


def read_limits(home: Path) -> dict[str, int]:
    """Character budgets the memory tool enforces: the home's config.yaml, else Hermes' loader, else defaults.

    The home's own config.yaml comes first: Hermes' loader only knows the ACTIVE profile (and caches it),
    so scanning another profile or a test home through it would report the active profile's limits.
    """
    section = None
    try:
        import yaml  # type: ignore

        with open(Path(home) / "config.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        section = cfg.get("memory") if isinstance(cfg, dict) else None
    except Exception:
        section = None
    if not isinstance(section, dict):
        try:
            same_home = Path(home).resolve() == hermes_home().resolve()
        except OSError:
            same_home = False
        if same_home:
            try:
                from hermes_cli.config import load_config_readonly  # type: ignore

                cfg = load_config_readonly() or {}
                section = cfg.get("memory") if isinstance(cfg, dict) else None
            except Exception:
                section = None
    section = section if isinstance(section, dict) else {}
    limits = dict(DEFAULT_LIMITS)
    for target, key in (("memory", "memory_char_limit"), ("user", "user_char_limit")):
        try:
            value = int(section.get(key, limits[target]))
            if value > 0:
                limits[target] = value
        except (TypeError, ValueError):
            pass
    return limits


def parse_entries(raw: str) -> list[str]:
    """Stripped, non-empty entries, split on the full delimiter (same rule as the memory tool)."""
    return [e for e in (x.strip() for x in raw.split(ENTRY_DELIMITER)) if e]


def join_entries(entries: list[str]) -> str:
    return ENTRY_DELIMITER.join(entries)


def char_count(entries: list[str]) -> int:
    return len(join_entries(entries))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""


def read_entries(path: Path) -> list[str]:
    return parse_entries(read_text(path))


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive lock on ``<path>.lock``; the same lock file the memory tool uses."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None and msvcrt is None:  # pragma: no cover
        yield
        return
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    raw_fd = os.open(lock_path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(raw_fd, 0o600)
        fh = os.fdopen(raw_fd, "r+", encoding="utf-8")
    except Exception:
        os.close(raw_fd)
        raise
    with fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        else:  # pragma: no cover
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                if fcntl is not None:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                else:  # pragma: no cover
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, fsync, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    with contextlib.suppress(FileNotFoundError):
        mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=".tidy_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def iter_skill_files(skills_root: Path, max_bytes: int = 512 * 1024) -> Iterator[Path]:
    """Markdown files under skills/ (SKILL.md and references), skipping hidden dirs and huge files."""
    if not skills_root.is_dir():
        return
    root = skills_root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
        for name in sorted(filenames):
            if not name.lower().endswith(".md"):
                continue
            p = Path(dirpath) / name
            try:
                if p.is_symlink() or p.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            yield p
