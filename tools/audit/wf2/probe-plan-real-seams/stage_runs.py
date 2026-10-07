"""Stage one isolated copy per seam set, the way engine/tests/test_seamplan.py
make_run does (final_auto.dxf + run.json, plus panels.json), writing the chosen
seam file as seams.json.  Re-runnable: wipes the copy's files first.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/stage_runs.py
"""
import hashlib
import json
import shutil

from common import SETS, copy_dir


def main():
    for name, (source, seams_path) in SETS.items():
        work = copy_dir(name)
        work.mkdir(parents=True, exist_ok=True)
        for old in work.iterdir():
            if old.is_file():
                old.unlink()
        for fn in ("final_auto.dxf", "run.json", "panels.json"):
            shutil.copyfile(source / fn, work / fn)
        payload = json.loads(seams_path.read_text(encoding="utf-8"))
        (work / "seams.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        md5 = hashlib.md5((work / "final_auto.dxf").read_bytes()).hexdigest()
        print(f"set {name}: {work}  seams={len(payload['seams'])} from {seams_path.name} "
              f"(source run {source.name}, final_auto.dxf md5 {md5})")


if __name__ == "__main__":
    main()
