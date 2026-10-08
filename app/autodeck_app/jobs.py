"""Background jobs with a live log, one at a time (the engine is not re-entrant)."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


class JobBusy(RuntimeError):
    """Raised by `start` when another job is already running.

    The routes check `active()` before starting, but the check and the start
    took the manager's lock in two separate steps -- so two requests arriving
    together could both pass the check and both start, and the engine is not
    re-entrant.  `start` now refuses under its own lock, and the server maps
    this to the same 409 the pre-check produces.
    """


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = "queued"          # queued | running | done | error
    log: list[str] = field(default_factory=list)
    started: float | None = None
    finished: float | None = None
    result: Any = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, since: int = 0) -> dict[str, Any]:
        return {
            "job_id": self.job_id, "kind": self.kind, "status": self.status,
            "log": self.log[since:], "log_length": len(self.log),
            "elapsed_s": (round((self.finished or time.time()) - self.started, 1) if self.started else 0.0),
            "error": self.error, "meta": self.meta,
            "result": self.result if isinstance(self.result, (dict, list, str, int, float, type(None))) else None,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._worker_lock = threading.Lock()

    def start(self, kind: str, fn: Callable[[Callable[[str], None]], Any], meta: dict[str, Any] | None = None) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], kind=kind, meta=dict(meta or {}))
        with self._lock:
            # The refusal happens under the same lock that registers this job,
            # so "is anything running" and "book me in" are one decision and
            # not two that a second request can slip between.
            for other in self._jobs.values():
                if other.status in ("queued", "running"):
                    raise JobBusy(f"a job is already running ({other.kind})")
            self._jobs[job.job_id] = job

        def log(message: str) -> None:
            stamp = time.strftime("%H:%M:%S")
            job.log.append(f"[{stamp}] {message}")

        def run() -> None:
            with self._worker_lock:          # serialise: one engine job at a time
                job.status = "running"
                job.started = time.time()
                try:
                    job.result = fn(log)
                    job.status = "done"
                except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                    job.status = "error"
                    # The class name is stripped on purpose: this text goes to
                    # the fabricator, and "ValueError:" is a programmer's word
                    # for what is otherwise a perfectly readable sentence.
                    job.error = str(exc) or type(exc).__name__
                    log(f"ERROR {job.error}")
                    for line in traceback.format_exc().strip().splitlines()[-6:]:
                        log("    " + line)
                finally:
                    job.finished = time.time()

        threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in ("queued", "running"):
                    return job
            return None
