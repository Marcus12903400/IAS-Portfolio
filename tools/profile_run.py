"""Profile an AutoDeck outline run: wall-clock per stage plus a cProfile
breakdown of the functions that actually dominate.

    python tools/profile_run.py --scan inputs/testboat/scan.obj
    python tools/profile_run.py --scan ... --no-cache   (force a cold engine run)

Prints the stage timings the engine already records, then the top cumulative
and top self-time functions, which is what tells you whether the cost is a
pure-Python loop (self time) or downstream library work (cumulative only).
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import shutil
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", required=True, type=Path)
    parser.add_argument("--units", default=None)
    parser.add_argument("--pattern", default="teak")
    parser.add_argument("--layout", default="nest")
    parser.add_argument("--no-cache", action="store_true", help="clear the engine cache first")
    parser.add_argument("--top", type=int, default=28)
    parser.add_argument("--stats-out", type=Path, default=None)
    parser.add_argument("--no-profile", action="store_true",
                        help="wall-clock stage timings only; cProfile inflates pure-Python "
                             "loops several-fold and distorts the ranking")
    args = parser.parse_args()

    from autodeck2 import config as config_mod
    from autodeck2 import paths, pipeline

    if args.no_cache:
        cache_dir = paths.cache_dir()
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
            print(f"cleared cache: {cache_dir}")

    config = config_mod.load_config()
    lines: list[tuple[float, str]] = []
    started = time.perf_counter()

    def progress(message: str) -> None:
        stamp = time.perf_counter() - started
        lines.append((stamp, str(message)))
        print(f"  [{stamp:7.2f}s] {message}", flush=True)

    profiler = cProfile.Profile()
    if not args.no_profile:
        profiler.enable()
    summary = pipeline.run_outline(
        args.scan.resolve(), config, units=args.units,
        layout_mode=args.layout, pattern=args.pattern, progress=progress,
    )
    if not args.no_profile:
        profiler.disable()
    total = time.perf_counter() - started

    print()
    print("=" * 78)
    print(f"TOTAL {total:.2f}s   status={summary['status']}   cache_hit={summary['cache_hit']}")
    print(f"run_dir={summary['run_dir']}")
    print(f"panels={summary['panels']}")
    if summary.get("warnings"):
        print(f"warnings ({len(summary['warnings'])}):")
        for w in summary["warnings"][:12]:
            print(f"  - {w}")
    print("=" * 78)

    print("\nSTAGE GAPS (time between successive progress messages)")
    gaps = []
    for (t0, m0), (t1, _m1) in zip(lines, lines[1:]):
        gaps.append((t1 - t0, m0))
    gaps.append((total - lines[-1][0], lines[-1][1]) if lines else (total, "(no messages)"))
    for dt, msg in sorted(gaps, reverse=True)[:18]:
        if dt > 0.05:
            print(f"  {dt:7.2f}s  {msg[:96]}")

    if args.no_profile:
        return 0

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.strip_dirs()

    print(f"\nTOP {args.top} BY CUMULATIVE TIME")
    stats.sort_stats("cumulative").print_stats(args.top)
    print("\n".join(stream.getvalue().splitlines()[4:]))

    stream.truncate(0); stream.seek(0)
    print(f"\nTOP {args.top} BY SELF TIME  (pure-Python hot loops show up here)")
    stats.sort_stats("tottime").print_stats(args.top)
    print("\n".join(stream.getvalue().splitlines()[4:]))

    if args.stats_out:
        args.stats_out.parent.mkdir(parents=True, exist_ok=True)
        stats.dump_stats(str(args.stats_out))
        print(f"\nwrote {args.stats_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
