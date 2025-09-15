#!/usr/bin/env python3
import os, time, signal, subprocess, shutil, datetime, pathlib, sys, threading
from db_helper import *
from setup_helper import *
from lcov_helper import *
from dotenv import dotenv_values
config = dotenv_values(".env")

PARALLEL = int(config['PARALLEL'])
N_WORKERS = int(config['N_WORKERS'])
ROUND_SEC = int(config['ROUND_SEC']  )       
HOURS_TOTAL = int(config['HOURS_TOTAL'])
SLOTS_PER_HOUR = int(config['SLOTS_PER_HOUR'])
UE_PORT_BASE = int(config['UE_PORT_BASE'])
IMSI_BASE = int(config['IMSI_BASE'])
OPEN5GS = config['OPEN5GS_PATH']
LOG_ROOT  = pathlib.Path("logs")
LOG_ROOT.mkdir(exist_ok=True)
GCOV_DIR  = LOG_ROOT / pathlib.Path("gcov")
GCOV_DIR.mkdir(exist_ok=True)

CTRL_DIR = pathlib.Path("ctrl"); 
CTRL_DIR.mkdir(exist_ok=True)
EPOCH_FILE = CTRL_DIR / "epoch"
RESET_REQ_DIR = CTRL_DIR / "reset_requests"; 
RESET_REQ_DIR.mkdir(exist_ok=True)
RESET_PENDING_FILE = CTRL_DIR / "reset_pending"

def spawn_worker(wid:int):
    worker_logs_dir = LOG_ROOT / pathlib.Path(f"worker_{wid}") / pathlib.Path('logs')
    worker_logs_dir.mkdir(exist_ok=True, parents=True)
    worker_log = open(worker_logs_dir / 'worker.log', 'a', buffering=1)
    env = os.environ.copy()
    env["COREFUZZER_WID"] = str(wid)
    # return subprocess.Popen(['python3', 'core_fuzzer.py', '--wid', str(wid)], env=env, start_new_session=True)
    return subprocess.Popen(['python3', 'core_fuzzer.py', '--wid', str(wid)],
                            stdout=worker_log, stderr=worker_log, text=True, start_new_session=True)

def collect_gcov(round_tag:str):
    info_file = f"{GCOV_DIR}/app_{round_tag}.info"
    coverage_file = f"{GCOV_DIR}/coverage_{round_tag}.info"
    with open(coverage_file, "w") as f:
        subprocess.run([
            'lcov',
            '--directory', str(OPEN5GS),
            '--capture',
            '--output-file', str(info_file),
            '--rc', 'lcov_branch_coverage=1',
            '--ignore-errors', 'branch,callback,child,corrupt,count,deprecated,empty,excessive,fork,format,gcov,graph,internal,mismatch,missing,negative,package,parallel,parent,range,source,unsupported,unused,usage,utility,version'
        ], stdout=f, stderr=subprocess.STDOUT, check=True)

def collect_outputs(wid:int, round_tag:str):
    wdir   = LOG_ROOT / pathlib.Path(f'worker_{wid}')
    outdir = wdir / pathlib.Path('logs') / f'w{wid}_{round_tag}'
    outdir.mkdir(parents=True, exist_ok=True)

    for name in ("savedFSM.json", "savedFSM_sm.json", "savedMCTS_amf.json", "savedMCTS_smf.json"):
        src = wdir / name
        if src.exists():
            shutil.copy(src, outdir / name)

    subprocess.run([
        'mongoexport',
        f'--db=CoreFuzzer',
        f'--collection={config["DB_NAME"]}_w{wid}',
        f'--out={outdir}/db.json'
    ], check=True)

tcpdump_proc = None
def start_pcap():
    global tcpdump_proc
    pcap_file = f"./logs/fuzz_res.pcapng"
    tcpdump_proc = subprocess.Popen(
        ["tcpdump", "-i", "lo",
         "-w", pcap_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def stop_pcap():
    if tcpdump_proc and tcpdump_proc.poll() is None:
        tcpdump_proc.send_signal(signal.SIGINT)
        tcpdump_proc.wait()

CURRENT_EPOCH = 0
def write_epoch(n:int):
    EPOCH_FILE.write_text(str(n))

def read_epoch()->int:
    try: return int(EPOCH_FILE.read_text().strip())
    except: return 0

def clear_reset_requests():
    for f in RESET_REQ_DIR.glob("*.req"):
        try: f.unlink()
        except: pass

def reset_epoch_files():
    try:
        EPOCH_FILE.write_text("0")
    except Exception:
        pass
    if RESET_PENDING_FILE.exists():
        try: RESET_PENDING_FILE.unlink()
        except: pass
    clear_reset_requests()

def wait_nf_procs(names, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            out = subprocess.check_output(["ps", "-eo", "comm"], text=True)
        except Exception:
            time.sleep(0.5); continue
        ok = all(any(n == line.strip() for line in out.splitlines()) for n in names)
        if ok:
            print("Core start done")
            return True
        time.sleep(0.5)
    print("Core start fail")
    return False


def health_check(timeout=10)->bool:
    gnb_log_path = LOG_ROOT / "gnb.log"
    success_message = "NG Setup procedure is successful"

    print("[MASTER] Health Check: Verifying gNB connection to AMF...")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not gnb_log_path.is_file():
            time.sleep(1.0)
            continue
        
        try:
            log_content = gnb_log_path.read_text()
            if success_message in log_content:
                print(f"[MASTER] Health Check PASSED. Found: '{success_message}'")
                return True
        except Exception as e:
            print(f"[MASTER] Health Check: Error reading log file: {e}")

        time.sleep(1.0)

    print("[MASTER] Health Check FAILED: Timed out waiting for gNB to connect.")
    return False

def do_full_reset()->int:
    print("[MASTER] Full reset: restarting Core & gNB")
    RESET_PENDING_FILE.write_text(str(int(time.time())))
    time.sleep(1.0)

    tag = f"epoch{read_epoch()}"

    killUE_all()

    killGNB()
    killCore()
    time.sleep(0.5)
    startCore(); 
    if not wait_nf_procs(['open5gs-amfd','open5gs-smfd'], timeout=10):
        print("[MASTER] WARN: AMF/SMF not detected in time")
    time.sleep(10)
    startGNB();  
    time.sleep(3)
    ok = health_check(timeout=10)
    if not ok:
        print("[MASTER] WARN: gNB health_check failed, continue anyway")

    CURRENT_EPOCH = read_epoch() + 1
    write_epoch(CURRENT_EPOCH)
    if RESET_PENDING_FILE.exists():
        try: RESET_PENDING_FILE.unlink()
        except: pass
    clear_reset_requests()
    print(f"[MASTER] Full reset done. epoch={CURRENT_EPOCH}")
    return CURRENT_EPOCH

def reset_watcher(stop_event:threading.Event):
    while not stop_event.is_set():
        if any(RESET_REQ_DIR.glob("*.req")):
            print("found reset_request, do full reset")
            do_full_reset()
        stop_event.wait(0.2)

PROCS = []
def master_exit_handler(signum, frame):
    print("\n[MASTER] Ctrl+C received, stopping fuzz...")
    stop_pcap()
    print("[MASTER] Stopping worker processes...")
    for p in list(PROCS):
        try:
            p.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
    time.sleep(2)
    for p in list(PROCS):
        if p.poll() is None:
            print(f"[MASTER] Worker PID {p.pid} did not exit gracefully, sending SIGTERM...")
            try:
                p.terminate()
            except ProcessLookupError:
                pass
    
    time.sleep(1)
    for p in list(PROCS):
        if p.poll() is None:
            print(f"[MASTER] Worker PID {p.pid} is stuck, sending SIGKILL...")
            try:
                p.kill()
            except ProcessLookupError:
                pass

    for p in list(PROCS):
        try:
            p.wait(timeout=1)
        except:
            pass


    killGNB()
    killCore()
    reset_epoch_files()
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, master_exit_handler)
    if OPEN5GS:
        os.system(f"lcov --directory {OPEN5GS} --zerocounters")
    for w in range(N_WORKERS):
        clear_db_col(w)

    reset_epoch_files()
    do_full_reset()

    start_pcap()
    if PARALLEL: 
        for hour in range(HOURS_TOTAL):
            for slot in range(SLOTS_PER_HOUR):
                tag = f"{hour:02d}_{slot}"   

                stop_evt = threading.Event()
                watcher = threading.Thread(target=reset_watcher, args=(stop_evt,), daemon=True)
                watcher.start()

                global PROCS
                PROCS = [spawn_worker(w) for w in range(N_WORKERS)]
                print(f"[+] Round {tag} started with {N_WORKERS} workers")
                time.sleep(ROUND_SEC)

                stop_evt.set()
                watcher.join(timeout=2)

                for p in PROCS:
                    p.send_signal(signal.SIGINT)
                for wid,p in enumerate(PROCS):
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    collect_outputs(wid, tag)
                collect_gcov(tag)
                PROCS = []
                do_full_reset()
                print(f"[+] {tag} finished, data stored.")
        killGNB()
        killCore()
        reset_epoch_files()
        time.sleep(0.5)
    stop_pcap()   

if __name__ == "__main__":
    main()