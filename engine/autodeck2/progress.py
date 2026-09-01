"""Stage progress reporting: one line per stage with elapsed time, so a run
that takes minutes on a real scan never looks hung."""

from __future__ import annotations

import time
from typing import Callable

Progress = Callable[[str], None]


def make_progress_printer() -> Progress:
    state = {"last": time.perf_counter(), "start": time.perf_counter()}

    def _print(stage: str) -> None:
        now = time.perf_counter()
        print(f"  [+{now - state['last']:6.1f}s | {now - state['start']:6.1f}s total] {stage}...", flush=True)
        state["last"] = now

    return _print


def silent(_stage: str) -> None:
    return None


class StageTimer:
    """Records how long each stage took, for run.json."""

    def __init__(self, progress: Progress | None = None) -> None:
        self.progress = progress or silent
        self.timings: dict[str, float] = {}
        self._current: str | None = None
        self._started = 0.0

    def start(self, stage: str, label: str | None = None) -> None:
        self.finish()
        self._current = stage
        self._started = time.perf_counter()
        self.progress(label or stage)

    def finish(self) -> None:
        if self._current is not None:
            self.timings[self._current] = round(time.perf_counter() - self._started, 3)
            self._current = None
