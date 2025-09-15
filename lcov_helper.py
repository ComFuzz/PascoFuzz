import os, time, subprocess, shlex, pathlib, json
from typing import List, Optional, Tuple, Set
from dotenv import dotenv_values
config = dotenv_values(".env")

OPEN5GS_PATH = config['OPEN5GS_PATH']
LOG_DIR = pathlib.Path("./logs"); LOG_DIR.mkdir(exist_ok=True)

def run(cmd):
    return subprocess.run(shlex.split(cmd), check=True)

def pgrep_all(pat="open5gs-amfd"):
    try:
        out = subprocess.check_output(["pgrep", "-f", pat], text=True).strip()
        return [int(x) for x in out.splitlines() if x.strip()]
    except Exception:
        return []

def gcov_flush_by_gdb(pids):
    for pid in pids:
        try:
            run(f"gdb -batch -ex 'attach {pid}' -ex 'call (void)__gcov_flush()' -ex 'detach' -ex 'quit'")
        except Exception as e:
            print(f"[flush] gdb flush pid={pid} failed: {e}")

def lcov_capture(tag, extract_globs=None):
    out = LOG_DIR / f"{tag}.info"
    run(f"lcov --capture --directory {OPEN5GS_PATH} --output-file {out} --rc lcov_branch_coverage=1")
    if extract_globs:
        run(" ".join(
            ["lcov","--extract",str(out), *extract_globs, "--output-file",str(out), "--rc","lcov_branch_coverage=1"]
        ))
    return out

def lcov_delta(post_tag, pre_tag, out_tag):
    post = LOG_DIR / f"{post_tag}.info"
    pre  = LOG_DIR / f"{pre_tag}.info"
    out  = LOG_DIR / f"{out_tag}.info"
    run(f"lcov --subtract {post} {pre} --output-file {out} --rc lcov_branch_coverage=1")
    return out

def genhtml(info_path, out_dir):
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    run(f"genhtml {info_path} --branch-coverage --output-directory {out_dir} --ignore-errors range")
    return out_dir

def _load_fsm_file(path: pathlib.Path) -> Tuple[Set[str], Set[str], Set[tuple], Set[tuple]]:
    if not path.is_file():
        return set(), set(), set(), set()

    try:
        data = json.loads(path.read_text() or "{}")
    except Exception:
        return set(), set(), set(), set()

    all_states, visited_states = set(), set()
    for st in data.get("states", []):
        name = st.get("name")
        if not name:
            continue
        all_states.add(name)
        if st.get("visited", False):
            visited_states.add(name)
    all_edges4 = set()
    for t in data.get("transitions", []):
        if not isinstance(t, (list, tuple)) or len(t) < 3:
            continue
        src, inp, out = t[0], t[1], t[2]
        dst = t[3] if len(t) >= 4 else None
        all_edges4.add((src, inp, out, dst))

    hit_edges4 = set()
    eh = data.get("edge_hits")
    if isinstance(eh, list):
        for rec in eh:
            if isinstance(rec, (list, tuple)) and len(rec) >= 4:
                src, inp, out, dst = rec[:4]
                hit_edges4.add((src, inp, out, dst))

    return all_states, visited_states, all_edges4, hit_edges4
