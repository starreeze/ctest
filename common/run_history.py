"""Compact persistent summaries for profile pipeline invocations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Literal

if os.name == "nt":
    import msvcrt
else:
    import fcntl


RunOrigin = Literal["auto", "cron", "manual"]
REDACTED = "[REDACTED]"
SENSITIVE_ARG_FLAGS = {"--controller_password"}


def redact_argv(argv: list[str]) -> list[str]:
    """Redact sensitive option values while retaining invocation structure."""
    redacted: list[str] = []
    redact_next = False
    for argument in argv:
        if redact_next:
            redacted.append(REDACTED)
            redact_next = False
            continue
        if argument in SENSITIVE_ARG_FLAGS:
            redacted.append(argument)
            redact_next = True
            continue
        flag, separator, _value = argument.partition("=")
        if separator and flag in SENSITIVE_ARG_FLAGS:
            redacted.append(f"{flag}={REDACTED}")
            continue
        redacted.append(argument)
    return redacted


@contextmanager
def _exclusive_file_lock(lock_file):
    """Hold an advisory one-byte lock on Windows or a flock elsewhere."""
    if os.name == "nt":
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
    try:
        yield
    finally:
        if os.name == "nt":
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def resolve_code_revision() -> dict | None:
    """Return the repository revision and whether tracked/untracked changes exist."""
    repository = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return None
    if revision.returncode != 0:
        return None
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return {"commit": revision.stdout.strip(), "dirty": bool(status.stdout)}


def _ancestor_process_names(pid: int | None = None) -> list[str]:
    """Return Linux ancestor command names, nearest parent first."""
    current = os.getppid() if pid is None else pid
    names: list[str] = []
    while current > 1:
        try:
            with open(f"/proc/{current}/comm", encoding="utf-8") as process_file:
                names.append(process_file.read().strip().lower())
            with open(f"/proc/{current}/stat", encoding="utf-8") as process_file:
                stat = process_file.read()
        except OSError:
            break
        current = int(stat[stat.rfind(")") + 2 :].split()[1])
    return names


def resolve_run_origin(origin: RunOrigin) -> Literal["cron", "manual"]:
    if origin != "auto":
        return origin
    if sys.platform.startswith("linux"):
        names = _ancestor_process_names()
        if any(name in {"cron", "crond"} for name in names):
            return "cron"
    return "manual"


class RunRecorder:
    """Collect stage metrics and append exactly one terminal JSONL record."""

    def __init__(
        self,
        history_path: str,
        origin: RunOrigin = "auto",
        argv: list[str] | None = None,
        pid: int | None = None,
    ):
        self.history_path = history_path
        self._started_monotonic = time.monotonic()
        self._finished = False
        self._record: dict = {
            "schema_version": 1,
            "run_id": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}-{uuid.uuid4().hex[:8]}",
            "origin": resolve_run_origin(origin),
            "pid": os.getpid() if pid is None else pid,
            "argv": redact_argv(list(sys.argv if argv is None else argv)),
            "revision": resolve_code_revision(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stages": {},
        }

    @property
    def run_id(self) -> str:
        return str(self._record["run_id"])

    @property
    def origin(self) -> str:
        return str(self._record["origin"])

    @property
    def pid(self) -> int:
        return int(self._record["pid"])

    def set_profile(self, profile_path: str) -> None:
        self._record["profile"] = os.path.abspath(profile_path)

    def record_stage(self, name: str, metrics: dict) -> None:
        self._record["stages"][name] = metrics

    def finish(self, error: BaseException | None = None) -> dict:
        if self._finished:
            raise RuntimeError(f"Run {self.run_id} was already recorded")
        self._record["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._record["elapsed_seconds"] = round(time.monotonic() - self._started_monotonic, 3)
        self._record["status"] = "failed" if error is not None else "success"
        if error is not None:
            self._record["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
        self._append()
        self._finished = True
        return self._record.copy()

    def _append(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.history_path))
        os.makedirs(directory, exist_ok=True)
        line = json.dumps(self._record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(f"{self.history_path}.lock", "a+b") as lock_file:
            with _exclusive_file_lock(lock_file):
                with open(self.history_path, "a", encoding="utf-8") as history_file:
                    history_file.write(line)
                    history_file.flush()
                    os.fsync(history_file.fileno())


def run_single_stage(
    name: str,
    operation: Callable[[], dict],
    history_path: str,
    origin: RunOrigin,
    profile_path: str,
    logger,
) -> dict:
    """Run one mutation-stage CLI with the same history contract as main."""
    run = RunRecorder(history_path, origin)
    run.set_profile(profile_path)
    logger.info(f"Run {run.run_id} started (origin={run.origin}, pid={run.pid}, stage={name})")
    try:
        metrics = operation()
        run.record_stage(name, metrics)
    except BaseException as exc:
        summary = run.finish(exc)
        logger.error(
            f"Run {run.run_id} failed after {summary['elapsed_seconds']:.3f}s: "
            f"{type(exc).__name__}: {exc}"
        )
        raise
    summary = run.finish()
    logger.info(f"Run {run.run_id} succeeded after {summary['elapsed_seconds']:.3f}s")
    return metrics
