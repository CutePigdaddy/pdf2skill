"""M4 alignment verification: byte-diff py_out/ vs ts_out/ (and prompts).

Known, accepted differences normalized before comparison:
- Line endings: Python text-mode writes CRLF on Windows; the SDK writes LF.
- chunks_index.json 'file' field: Python uses os.sep (backslash on Windows),
  the SDK always uses forward slashes.
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent


def normalize(data: bytes, path: Path) -> bytes:
    text = data.decode("utf-8").replace("\r\n", "\n")
    if path.suffix == ".json":
        text = text.replace("chunks\\\\", "chunks/").replace("chunks\\", "chunks/")
    return text.encode("utf-8")


def collect(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root)).replace("\\", "/")] = normalize(p.read_bytes(), p)
    return out


def main() -> None:
    py = collect(BASE / "py_out")
    ts = collect(BASE / "ts_out")

    py_names, ts_names = set(py), set(ts)
    ok = True
    if py_names != ts_names:
        ok = False
        print("FILE SET MISMATCH")
        print("  only in py:", sorted(py_names - ts_names))
        print("  only in ts:", sorted(ts_names - py_names))

    for name in sorted(py_names & ts_names):
        if py[name] != ts[name]:
            ok = False
            print(f"DIFF: {name} (py {len(py[name])}B vs ts {len(ts[name])}B)")
            pyt, tst = py[name].decode("utf-8"), ts[name].decode("utf-8")
            for i, (a, b) in enumerate(zip(pyt, tst)):
                if a != b:
                    lo = max(0, i - 60)
                    print(f"  first diff at char {i}:")
                    print(f"    py: ...{pyt[lo:i+80]!r}")
                    print(f"    ts: ...{tst[lo:i+80]!r}")
                    break
            else:
                print(f"  length differs: {len(pyt)} vs {len(tst)}")
                print(f"  py tail: {pyt[-120:]!r}")
                print(f"  ts tail: {tst[-120:]!r}")

    py_prompts = json.loads((BASE / "py_prompts.json").read_text(encoding="utf-8"))
    ts_prompts = json.loads((BASE / "ts_prompts.json").read_text(encoding="utf-8"))
    if py_prompts != ts_prompts:
        ok = False
        print(f"PROMPTS MISMATCH: py {len(py_prompts)} vs ts {len(ts_prompts)}")
        for i, (a, b) in enumerate(zip(py_prompts, ts_prompts)):
            if a != b:
                for j, (x, y) in enumerate(zip(a, b)):
                    if x != y:
                        print(f"  prompt {i} differs at char {j}:")
                        print(f"    py: ...{a[max(0,j-50):j+60]!r}")
                        print(f"    ts: ...{b[max(0,j-50):j+60]!r}")
                        break
                break
    else:
        print(f"prompts identical ({len(py_prompts)} prompts)")

    if ok:
        print(f"ALIGNMENT OK: {len(py)} files byte-identical (after normalizations)")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
