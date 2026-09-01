"""Build the self-contained AutoDeck V0.3.5 Windows x64 distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath


BUNDLE_NAME = "AutoDeck"
PROJECT_DIRS = (
    "config",
    "docs",
    "src",
    "test-data",
    "tests",
    ".autodeck_packages",
)
PROJECT_FILES = (
    "Start_AutoDeck.bat",
    "README.md",
    "PORTABLE_README.md",
    "pyproject.toml",
)
VENDOR_DIRS = (
    "rhino3dm",
    "rhino3dm-8.32.0.dist-info",
)


def is_ignored(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return (
        "__pycache__" in parts
        or ".pytest_cache" in parts
        or path.suffix.lower() in {".pyc", ".pyo"}
        or path.name.lower().endswith(".whl")
    )


def iter_files(root: Path):
    if not root.exists():
        raise FileNotFoundError(root)
    if root.is_file():
        if not is_ignored(root):
            yield root
        return
    for directory, dirnames, filenames in os.walk(root):
        directory_path = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not is_ignored(directory_path / name)
        )
        for filename in sorted(filenames):
            path = directory_path / filename
            if not is_ignored(path):
                yield path


def arcname(relative: Path) -> str:
    return str(PurePosixPath(BUNDLE_NAME, *relative.parts))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(project: Path, runtime: Path, output: Path) -> dict[str, object]:
    project = project.resolve()
    runtime = runtime.resolve()
    output = output.resolve()

    if not (runtime / "python.exe").is_file():
        raise FileNotFoundError(f"Python runtime not found: {runtime / 'python.exe'}")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing archive: {output}")

    sources: list[tuple[Path, Path]] = []
    for name in PROJECT_FILES:
        path = project / name
        sources.extend((item, item.relative_to(project)) for item in iter_files(path))
    for name in PROJECT_DIRS:
        path = project / name
        sources.extend((item, item.relative_to(project)) for item in iter_files(path))
    for name in VENDOR_DIRS:
        path = project / "vendor_packages" / name
        sources.extend(
            (item, Path("vendor_packages") / item.relative_to(project / "vendor_packages"))
            for item in iter_files(path)
        )
    sources.extend(
        (item, Path("runtime") / item.relative_to(runtime))
        for item in iter_files(runtime)
    )

    sources.sort(key=lambda pair: str(pair[1]).lower())
    total_bytes = sum(source.stat().st_size for source, _ in sources)
    started = time.perf_counter()

    with zipfile.ZipFile(
        output,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for index, (source, relative) in enumerate(sources, start=1):
            archive.write(source, arcname(relative))
            if index % 1000 == 0:
                print(f"Packed {index:,}/{len(sources):,} files...", flush=True)

        manifest = {
            "bundle": BUNDLE_NAME,
            "autodeck_version": "0.3.5",
            "platform": "Windows x64",
            "python_version": "3.12",
            "entry_count": len(sources),
            "uncompressed_bytes": total_bytes,
            "contents": [str(PurePosixPath(*relative.parts)) for _, relative in sources],
        }
        archive.writestr(
            arcname(Path("PORTABLE_MANIFEST.json")),
            json.dumps(manifest, indent=2) + "\n",
        )

    with zipfile.ZipFile(output, "r") as archive:
        broken = archive.testzip()
        if broken is not None:
            raise RuntimeError(f"ZIP integrity check failed at {broken}")

    result = {
        "archive": str(output),
        "files": len(sources) + 1,
        "uncompressed_bytes": total_bytes,
        "compressed_bytes": output.stat().st_size,
        "sha256": file_sha256(output),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    print(json.dumps(result, indent=2), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.project, args.runtime, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
