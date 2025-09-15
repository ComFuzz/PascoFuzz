#!/usr/bin/env python3
# Run queries on Core

import os, time, socket, string, json, atexit, threading, argparse, pathlib, shutil, re, subprocess, shutil, datetime
from collections import defaultdict, deque
from contextlib import closing
from db_helper import *
from fsm_helper import *
from setup_helper import *
from lcov_helper import *
from crash_monitor import *

from dotenv import dotenv_values
config = dotenv_values(".env")

# +++ 
parser = argparse.ArgumentParser()
parser.add_argument('--wid', type=int, default=0,
                    help='Worker ID (0-based)')
args = parser.parse_args()
WID = args.wid     

case_id = f"{WID}_{int(time.time()*1000)}"
pre_cov = f"./logs/pre_{case_id}.info"
post_cov = f"./logs/post_{case_id}.info"
delta_cov = f"./logs/delta_{case_id}.info"

PARALLEL = int(config['PARALLEL'])
LOG_DIR = 'logs'
# +++ 
WORK_DIR   = LOG_DIR / pathlib.Path(f'./worker_{WID}')
WID_LOG_DIR    = WORK_DIR / 'logs'
WID_GCOV_DIR   = WORK_DIR / 'gcov'        
DB_NAME    = f"{config['DB_NAME']}_w{WID}"
UE_PORT_BASE  = int(config['UE_PORT_BASE']) + WID*100          
UE_PORT_AMF = UE_PORT_BASE + 1
UE_PORT_SMF = UE_PORT_BASE + 2
IMSI_BASE  = int(config['IMSI_BASE']) + WID*100
GNB_PORT_BASE = int(config['GNB_PORT_BASE'])
os.makedirs(WID_LOG_DIR, exist_ok=True)
CRASH_DIR = LOG_DIR / pathlib.Path("crash")
CRASH_DIR.mkdir(exist_ok=True, parents=True)
MCTS_CSV = WORK_DIR / "mcts_stats_reward.csv"


# +++ 
init_setup_path(UE_PORT_BASE, IMSI_BASE, WID_LOG_DIR)
init_db_path(WID)

reset_count = 0
local_offset = 0

error_hits = defaultdict(int)

CTRL_DIR = pathlib.Path("ctrl"); 
CTRL_DIR.mkdir(exist_ok=True)
EPOCH_FILE = CTRL_DIR / "epoch"
RESET_REQ_DIR = CTRL_DIR / "reset_requests"; 
RESET_REQ_DIR.mkdir(exist_ok=True)
RESET_PENDING_FILE = CTRL_DIR / "reset_pending"

def get_epoch()->int:
    try: return int(EPOCH_FILE.read_text().strip())
    except: return 0

def wait_for_epoch_change(prev_epoch:int, timeout_sec:int=300):
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        ep = get_epoch()
        if ep > prev_epoch:
            return ep
        time.sleep(0.2 + random.random()*0.3)
    return get_epoch()

def request_global_reset(reason:str):
    print("request_global_reset, reason:", reason)
    fname = RESET_REQ_DIR / f"Worker{WID}_{int(time.time()*1000)}_{reason}.req"
    try: fname.write_text(reason)
    except: pass

def wait_master_reset(prev_epoch:int) -> int:
    while RESET_PENDING_FILE.exists():
        time.sleep(0.2)
    return wait_for_epoch_change(prev_epoch, timeout_sec=600)

def warm_expand_root(schedule, fsm):
    root = schedule.root
    s0 = root.state_path[-1]
    succ = sorted({t[3] for t in fsm.transitions if t[0] == s0 and t[3] != s0})
    for dst in succ:
        if not root.has_child(dst):
            root.add_child(dst)

def mcts_nodes_from_state_seq(schedule, state_seq):
    node = schedule.root
    nodes = [node]
    root_state = node.state_path[-1]
    start = 0
    if state_seq and state_seq[0] != root_state:
        if root_state in state_seq:
            start = state_seq.index(root_state)
        else:
            start = 0
    for j in range(start, len(state_seq) - 1):
        nxt = state_seq[j + 1]
        if not node.has_child(nxt):
            node = node.add_child(nxt)
        else:
            node = node.children[nxt]
        nodes.append(node)
    return nodes

def _iter_mcts_nodes(node, depth=0):
    yield node, depth
    for child in getattr(node, "children", {}).values():
        yield from _iter_mcts_nodes(child, depth+1)

def print_mcts_snapshot(schedule, title="MCTS"):
    print(f"[{title}] MCTS snapshot")
    for node, d in _iter_mcts_nodes(schedule.root, 0):
        state_name = (node.state_path[-1] if getattr(node, "state_path", None) else "<?>")
        nel = int(getattr(node, "n_sel", 0))
        det = int(getattr(node, "n_det", 0))
        reward = float(getattr(node, "reward", 0.0))
        indent = "  " * d
        print(f"{indent}- {state_name:>12s} | depth={d:<2d} | nsel={nel:<5d} | ndet={det:<5d} | reward={reward:>8.3f}")

def rebuild_state_visits_from_tree(schedule):
    schedule.state_visits.clear()
    for node, _ in _iter_mcts_nodes(schedule.root):
        s = node.state_path[-1]
        schedule.state_visits[s] += int(getattr(node, "n_sel", 0))

GNB_LOG_PATH = os.path.join(LOG_DIR, "gnb.log")
gnb_fp, gnb_pos = None, 0

ERR_RE_ERROR_INDICATION = re.compile(r'Error(?:\s+|_)indication(?P<tail>.*)$', re.I)
ERR_RE_CAUSE_BRACKET   = re.compile(r'cause\[(?P<cat>[^\]]+)\]\s*(?P<detail>.+)?$', re.I)
ERR_RE_CAUSE_COLON     = re.compile(r'Cause:\s*(?P<cause>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)', re.I)
ERR_RE_PLAIN_CAUSE     = re.compile(r'([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)')

def ensure_gnb_log_open():
    global gnb_fp, gnb_pos
    if gnb_fp and not gnb_fp.closed:
        return
    t0 = time.time()
    while not os.path.isfile(GNB_LOG_PATH) and time.time() - t0 < 30:
        time.sleep(0.1)
    if not os.path.isfile(GNB_LOG_PATH):
        open(GNB_LOG_PATH, "a").close()
    gnb_fp = open(GNB_LOG_PATH, "r", encoding="utf-8", errors="ignore")
    gnb_fp.seek(0, os.SEEK_END)
    gnb_pos = gnb_fp.tell()

def normalize_cause(s: str) -> str:
    s = s.strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s*/\s*', '/', s)
    return s

def drain_gnb_error_since_last():
    global gnb_fp, gnb_pos
    try:
        ensure_gnb_log_open()
        gnb_fp.seek(gnb_pos)
        lines = gnb_fp.readlines()
        gnb_pos = gnb_fp.tell()
    except Exception:
        return None

    found = None
    for line in lines:
        m = ERR_RE_ERROR_INDICATION.search(line)
        if not m:
            continue
        tail = m.group('tail').strip()

        # 1) Error indication received. Cause: protocol/semantic-error
        mc = ERR_RE_CAUSE_COLON.search(tail)
        if mc:
            found = normalize_cause(mc.group('cause'))
            continue

        # 2) Error indication: cause[protocol] semantic-error  /  cause[protocol] / semantic-error
        mb = ERR_RE_CAUSE_BRACKET.search(tail)
        if mb:
            cat = (mb.group('cat') or '').strip()
            detail = (mb.group('detail') or '').strip()
            cause = f"{cat}/{detail}" if (cat and detail) else (cat or detail)
            found = normalize_cause(cause)
            continue

        # 3) Error indication:protocol/semantic-error 
        mp = ERR_RE_PLAIN_CAUSE.search(tail)
        if mp:
            found = normalize_cause(mp.group(1))
            continue

    return found

# handle exit
def exit_handler(fsm: FSM, fsm_sm: FSM):
    # clean up
    if not PARALLEL:
        killCore()
        killGNB()
    killUE()
    fsm_file = open(WORK_DIR / './savedFSM.json', 'w')
    fsm_file.write(fsm.to_json())
    fsm_file.close()
    fsm_sm_file = open(WORK_DIR / './savedFSM_sm.json', 'w')
    fsm_sm_file.write(fsm_sm.to_json())
    fsm_sm_file.close()
    mcts_amf_file = open(WORK_DIR / 'savedMCTS_amf.json', 'w')
    json.dump(schedule_amf.root.to_dict(), mcts_amf_file)
    mcts_smf_file = open(WORK_DIR / 'savedMCTS_smf.json', 'w')
    json.dump(schedule_smf.root.to_dict(), mcts_smf_file)

def wait_port_listen(port: int, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except Exception:
            time.sleep(0.1)
    return False


def check_ue_ports():
    cmd = f"ss -ltnp | egrep ':(%d|%d|%d)\\s'" % (UE_PORT_BASE, UE_PORT_AMF, UE_PORT_SMF)
    try:
        result = subprocess.check_output(cmd, shell=True, text=True)
        print("[Port Check] Listening sockets found:\n", result)
    except subprocess.CalledProcessError:
        print("[Port Check] No matching ports found.")

# restart Core or release UE context
def reset(full: bool):   
    global local_offset
    if PARALLEL:
        killUE()
        time.sleep(0.2)
        startUE()
        time.sleep(0.1)
        startUE2()
        time.sleep(0.1)
        startUE3()
        time.sleep(0.1)
        for p in (UE_PORT_BASE, UE_PORT_AMF, UE_PORT_SMF):
            if not wait_port_listen(p, timeout=8.0):
                print(f"[Worker{WID}] UE cmd-port {p} not ready in time")
        check_ue_ports()
        local_offset = (local_offset + 1) % 100000
        setOffset(getOffset() + 1)
        if(getOffset() > MAX_IMSI_OFFSET):
            setOffset(0)
        return
    else:
        return

# connect to UE
def connectUE():
    global UEsocket
    UEsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # UEsocket = socket.create_connection(("127.0.0.1", UE_PORT_BASE), timeout=5.0)
    UEsocket.settimeout(5)
    UEsocket.connect(("localhost", UE_PORT_BASE))
    # print("UEsocket.recv:", UEsocket.recv(1024))
    try:
        print("UEsocket.recv:", UEsocket.recv(1024))
    except socket.timeout:
        pass

def connectUE2():
    global UEsocket
    UEsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # UEsocket = socket.create_connection(("127.0.0.1", UE_PORT_AMF), timeout=5.0)
    UEsocket.settimeout(5)
    UEsocket.connect(("localhost", UE_PORT_AMF))
    try:
        print("UE2socket.recv:", UEsocket.recv(1024))
    except socket.timeout:
        pass

def connectUE3():
    global UEsocket
    UEsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # UEsocket = socket.create_connection(("127.0.0.1", UE_PORT_SMF), timeout=5.0)
    UEsocket.settimeout(5)
    UEsocket.connect(("localhost", UE_PORT_SMF))
    try:
        print("UE3socket.recv:", UEsocket.recv(1024))
    except socket.timeout:
        pass

# connect to gNB
def connectGNB():
    global gNBsocket
    gNBsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    gNBsocket.settimeout(1)
    gNBsocket.connect(("localhost", GNB_PORT_BASE))
    print(gNBsocket.recv(1024))

# +++ 
def canonical_ret(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "null_action"
    low = s.lower()
    if "unknown fuzzing message name" in low:
        return "null_action"
    if low in ("null_action", "decode error", "error", "timeout"):
        return "null_action"
    
    if s.startswith("{") and s.endswith("}"):
        try:
            j = json.loads(s)
            rt = j.get("ret_type") or j.get("ret_msg") or ""
            rt = (rt or "").strip()
            return rt if rt else "null_action"
        except Exception:
            return "null_action"

    return s

def sendSymbol(symbol: string):
    print("symbol:", symbol)
    if "serviceRequest" in symbol:
        sendRRCRelease()
        time.sleep(0.1)
    if ":" in symbol:
        print("send Symbol-fuzzing")
        i = symbol.find(":")
        sendSymbol("testMessage")
        testMsg = symbol[i+1:]
        return sendFuzzingMessage(testMsg.encode())
    print("send normal nas")
    UEsocket.send(symbol.encode())
    msg_out = ""
    for i in range(3):
        try:
            msg_out = UEsocket.recv(1024).decode().strip()
            if msg_out: 
                break
        except socket.timeout:
            # msg_out = "null_action"
            pass
        time.sleep(0.05)
    print("msg_out:", msg_out)
    return msg_out

symbols_enabled = [
                   "registrationRequest", 
                   "registrationComplete",
                   "deregistrationRequest", 
                   "serviceRequest", 
                   "securityModeReject",
                   "authenticationResponse",
                   "authenticationFailure",
                   "deregistrationAccept",
                   "securityModeComplete",
                   "identityResponse",
                   "configurationUpdateComplete",
                   "gmmStatus",
                   "ulNasTransport",
                   "PDUSessionEstablishmentRequest",
                   "PDUSessionAuthenticationComplete",
                   "PDUSessionModificationRequest",
                   "PDUSessionModificationComplete",
                   "PDUSessionModificationCommandReject",
                   "PDUSessionReleaseRequest",
                   "PDUSessionReleaseComplete",
                   "gsmStatus"]

symbols_fsm = ["registrationRequest", 
               "registrationRequestGUTI", 
               "registrationComplete",
               "deregistrationRequest", 
               "serviceRequest", 
               "securityModeReject",
               "authenticationResponse",
               "authenticationFailure",
               "deregistrationAccept",
               "securityModeComplete",
               "identityResponse",
               "configurationUpdateComplete"]

symbols_sm = ["PDUSessionEstablishmentRequest",
              "PDUSessionAuthenticationComplete",
              "PDUSessionModificationRequest",
              "PDUSessionModificationComplete",
              "PDUSessionModificationCommandReject",
              "PDUSessionReleaseRequest",
              "PDUSessionReleaseComplete",
              "gsmStatus"]

# send a message to UERANSIM
def sendFuzzingMessage(msg):
    UEsocket.send(msg)
    print("send fuzzing msg context:", msg)
    return UEsocket.recv(1024).decode().strip()

# get a message from UERANSIM
def getFuzzingMessage(msg_len: int):
    return UEsocket.recv(msg_len + 1)

# +++
def exec_sequence_align(fsm: FSM, start_state: str, path: Path):
    if path is None:
        return True, [start_state], []
    s = start_state
    state_seq = [s]
    ret_seq = []
    for i, act in enumerate(path.input_symbols):
        out = sendSymbol(act)
        out_canonical = canonical_ret(out)
        print("msg_out_canonical:", out_canonical)
        ret_seq.append(out_canonical)
        cand = [t for t in fsm.transitions if t[0] == s and t[1] == act and t[2] == out_canonical]
        if cand:
            t = random.choice(cand)
        if not cand:
            cand = [t for t in fsm.transitions if t[0] == s and t[1] == act]
            if not cand:
                print(f"[ALIGN] no edge for {s} --{act}/{out_canonical}--> ?")
                print("exec_sequence_align false, state_seq:", state_seq, "ret_seq:", ret_seq)
                return False, state_seq, ret_seq
            t = random.choice(cand)
        s = t[3]
        state_seq.append(s)
    print("exec_sequence_align true, state_seq:", state_seq, "ret_seq:", ret_seq)
    return True, state_seq, ret_seq

# +++
def send_symbol_on(sock: socket.socket, symbol: str, timeout=3.0) -> str:
    sock.settimeout(timeout)
    sock.send(symbol.encode())
    try:
        return sock.recv(1024).decode().strip()
    except socket.timeout:
        return "null_action"

def check_amf():
    print("check amf is crash or not")

    out = sendSymbol("registrationRequest")
    time.sleep(0.5)
    if out != "authenticationRequest":
        print("AMF Crashed")
        return True
    return False

def check_smf():
    path = Path([],[],[])
    path.input_symbols = ["registrationRequest",
                          "authenticationResponse",
                          "securityModeComplete",
                          "registrationComplete",
                          "PDUSessionEstablishmentRequest"]
    path.output_symbols = ["authenticationRequest",
                           "securityModeCommand",
                           "registrationAccept",
                           "configurationUpdateCommand",
                           "pduSessionEstablishmentAccept"]
    out_list = []

    for i in range(len(path.input_symbols) - 1):
        out = sendSymbol(path.input_symbols[i])
        out_list.append(out)
        time.sleep(0.5)
        if out != path.output_symbols[i]:
            print("SMF Crashed")
            print(path.input_symbols)
            print(path.output_symbols)
            print(out_list)
            return True
    return False

if __name__ == '__main__':
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") 
    print(f"start time: {now}")
    setOffset(0)
    # load FSM
    if os.path.exists(WORK_DIR / "./savedFSM.json") and os.path.exists(WORK_DIR / "./savedFSM_sm.json"):
        fsm_file = open(WORK_DIR / './savedFSM.json', 'r')
        fsm_json = fsm_file.read()
        fsm_sm_file = open(WORK_DIR / './savedFSM_sm.json', 'r')
        fsm_sm_json = fsm_sm_file.read()
        if fsm_json != "":
            fsm = FSM.from_json(fsm_json)
            fsm.refresh_paths()
            fsm_sm = FSM.from_json(fsm_sm_json)
            fsm_sm.refresh_paths()
        else:
            fsm = load_fsm(config['FSM_PATH'])
            fsm_sm = load_fsm(config['FSM_SM_PATH'])
        fsm_file.close()
    else:
        fsm = load_fsm(config['FSM_PATH'])
        fsm_sm = load_fsm(config['FSM_SM_PATH'])
    atexit.register(exit_handler, fsm, fsm_sm)

    # +++ 
    schedule_amf = MCTSSchedule(init_state=fsm.init_state)
    schedule_smf = MCTSSchedule(init_state=fsm_sm.init_state)
    mcts_amf_file = WORK_DIR / "savedMCTS_amf.json"
    mcts_smf_file = WORK_DIR / "savedMCTS_smf.json"
    if os.path.exists(mcts_amf_file):
        with open(mcts_amf_file, "r") as fpa:
            schedule_amf.root = MCTSNode.from_dict(json.load(fpa))
            rebuild_state_visits_from_tree(schedule_amf)

    if os.path.exists(mcts_smf_file):
        with open(mcts_smf_file, "r") as fps:
            schedule_smf.root = MCTSNode.from_dict(json.load(fps))
            rebuild_state_visits_from_tree(schedule_smf)
    warm_expand_root(schedule_amf, fsm)
    warm_expand_root(schedule_smf, fsm_sm)
    
    is_fresh_start = False

    if PARALLEL:
        print(f"[Worker{WID}] waiting for master epoch...")
        while get_epoch() < 1:
            time.sleep(0.2)
        reset(False)
        is_fresh_start = True
        prev_epoch = get_epoch()
    else:
        reset(True)
        full_reset = False
    
    stuck_root = 0

    while True:
        # +++ 
        if PARALLEL and RESET_PENDING_FILE.exists():
            print(f"[Worker{WID}] master reset pending, pausing...")
            try: UEsocket.close()
            except: pass
            t_ep = get_epoch()
            new_ep = wait_master_reset(t_ep)
            reset(False)
            prev_epoch = new_ep
            continue

        try:
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") 
            print(f"{now}: [Worker{WID}] loop start")
            # +++ 
            if PARALLEL:
                curr_ep = get_epoch()
                print("epoch:", curr_ep)
                if curr_ep > prev_epoch:
                    print(f"[Worker{WID}] epoch {prev_epoch}->{curr_ep}, reset UEs")
                    reset(False)
                    prev_epoch = curr_ep
            else:
                reset(full_reset)
                print("IMSI_OFFSET:", getOffset())
                full_reset = False
            try:
                # +++ 
                if PARALLEL and RESET_PENDING_FILE.exists():
                    print(f"[Worker{WID}] reset pending before connect, pausing...")
                    t_ep = get_epoch()
                    new_ep = wait_master_reset(t_ep)
                    reset(False)
                    prev_epoch = new_ep
                    continue             

                print(f"[Worker{WID}] connectUE start - 1")
                connectUE()
                print(f"[Worker{WID}] connectUE done - 1")
            except (socket.timeout, ConnectionRefusedError, ConnectionResetError):
                print("UE Connection failed(timeout/refused/reset), retrying...")
                reset_count += 1
                # +++ 
                if PARALLEL:
                    if reset_count > 10:
                        pe = get_epoch()
                        request_global_reset("init_connect_timeout")
                        wait_for_epoch_change(pe, timeout_sec=180)
                        reset_count = 0
                        reset(False)
                else:
                    if reset_count > 10:
                        full_reset = True
                        reset_count = 0
                continue
            # +++ 
            leaf_amf, mcts_path_amf = schedule_amf.choose_state(fsm, lambda name: fsm.get_state(name))

            print("[MCTS] picked leaf path:", leaf_amf.state_path)
            print("[MCTS] root children:", list(schedule_amf.root.children.keys()))
            print("[MCTS] root fully_expanded?:", 
                len(schedule_amf.root.children) >= len({t[3] for t in fsm.transitions if t[0]==fsm.init_state and t[3]!=fsm.init_state}))
            
            curr_state = fsm.get_state(leaf_amf.state_path[-1])

            if leaf_amf == schedule_amf.root:
                stuck_root += 1
            else:
                stuck_root = 0

            if stuck_root >= 3:
                if schedule_amf.root.children:
                    import random
                    leaf_amf = random.choice(list(schedule_amf.root.children.values()))
                    mcts_path_amf = [schedule_amf.root, leaf_amf]
                    curr_state = fsm.get_state(leaf_amf.state_path[-1])
                    print("[ANTI-STICKY] force pick child:", curr_state.name)
                stuck_root = 0

            print("init_state:", repr(fsm.init_state))
            print("state_names:", [repr(s.name) for s in fsm.states])
            print("Transitions out of init state:", {t[3] for t in fsm.transitions if t[0] == fsm.init_state})
            curr_state_sm = None
            used_smf = False
            if curr_state.oracle.state == "R":
                used_smf = True
                leaf_smf, mcts_path_smf = schedule_smf.choose_state(
                    fsm_sm, lambda n: fsm_sm.get_state(n)
                )
                curr_state_sm = fsm_sm.get_state(leaf_smf.state_path[-1])
            if curr_state_sm == None:
                state = curr_state.name
            else:
                state = curr_state.name + ":" + curr_state_sm.name
            print(f"[Worker{WID}] select state {state}")
            path = curr_state.select_path()
            print("path for", curr_state.name, ":", None if path is None else path.path_states)
            path_exec_amf, state_seq_amf, ret_seq_amf = exec_sequence_align(fsm, fsm.init_state, path)
            reached = state_seq_amf[-1]
            target  = leaf_amf.state_path[-1]
            if reached != target:
                schedule_amf.sink_hits[reached] += 2
                schedule_amf.state_visits[target] += 3
            print(f"[ALIGN] amf target={leaf_amf.state_path[-1]} reached={state_seq_amf[-1]}")
            mcts_path_exec_amf = mcts_nodes_from_state_seq(schedule_amf, state_seq_amf) if state_seq_amf else [schedule_amf.root]
            print("mcts_path_exec_amf:", mcts_path_exec_amf)
            if path_exec_amf != True:
                curr_state.count -= 1
                reset_count += 1
                continue
            else:
                is_fresh_start = False
                # +++ 
                curr_state.set_visited()
                ins_seq_amf = (path.input_symbols if path else [])
                fsm.mark_edges_from_seq(state_seq_amf, ins_seq_amf, ret_seq_amf)    
                # +++ 
                for sn in (state_seq_amf or []):
                    schedule_amf.state_visits[sn] += 1
                if path != None:
                    path.add_succ()

            if curr_state_sm != None:
                path_sm = curr_state_sm.select_path()
                path_exec_smf, state_seq_smf, ret_seq_smf = exec_sequence_align(fsm_sm, fsm_sm.init_state, path_sm)
                reached_sm = state_seq_smf[-1]
                target_sm  = leaf_smf.state_path[-1]
                if reached_sm != target_sm:
                    schedule_smf.sink_hits[reached_sm] += 2
                    schedule_smf.state_visits[target_sm] += 3
                print(f"[ALIGN] smf target={leaf_smf.state_path[-1]} reached={state_seq_smf[-1]}")
                mcts_path_exec_smf = mcts_nodes_from_state_seq(schedule_smf, state_seq_smf) if state_seq_smf else [schedule_smf.root]
                print("mcts_path_exec_smf:", mcts_path_exec_smf) 
                if path_exec_smf != True:
                    curr_state_sm.count -= 1
                    reset_count += 1
                    continue
                else:
                    curr_state_sm.set_visited()
                    ins_seq_smf = (path_sm.input_symbols if path_sm else [])
                    fsm_sm.mark_edges_from_seq(state_seq_smf, ins_seq_smf, ret_seq_smf)
                    for sn in (state_seq_smf or []):
                        schedule_smf.state_visits[sn] += 1                
                    if path_sm != None:
                        path_sm.add_succ()

            out = sendSymbol("enableFuzzing")
            print(out)
            if out == "Start fuzzing":
                print("Fuzzing enabled")
                if not curr_state.is_init:
                    for symbol in symbols_enabled:
                        send_msg = sendSymbol(symbol)
                        resp_json = json.loads(send_msg)
                        print("resp_json:", resp_json)
                        store_new_message(worker_id=WID,
                                        if_fuzz=False,
                                        state=state,
                                        send_type=symbol,
                                        ret_type="",
                                        if_crash=False,
                                        if_crash_sm=False,
                                        is_interesting=True,
                                        if_error=False,
                                        error_cause="",
                                        sht=resp_json.get("sht"),
                                        secmod=resp_json.get("secmod"),
                                        base_msg="",
                                        new_msg=resp_json.get("new_msg"),
                                        ret_msg="",
                                        violation=False,
                                        mm_status=resp_json.get("mm_status"),
                                        byte_mut=False)
                if check_seed_msg(state):
                    print("msg ount enough")
                    curr_state.is_init = True
                else:
                    curr_state.is_init = False
                    continue
                
                fuzzing = True
                violation = False
                if_crash = False
                if_crash_sm = False
                resp_json = {}
                ins_msg = ""
                is_new_state = False
                is_new_transition = False
                while fuzzing:
                    if not PARALLEL:
                        try:
                            print("start connect gNB")
                            connectGNB()
                            print("connected gNB")
                        except socket.timeout:
                            print("gNB Connection timeout, retrying...")
                            break

                    # +++ 
                    sendSymbol("syncDown")
                    print("syncDown done")

                    ins_msg = get_insteresting_msg(state)
                    if_crash=False
                    if_crash_sm=False
                    is_interesting=False
                    if_error=False
                    error_cause=""
                    print(sendSymbol("incomingMessage_"+str(ins_msg.get("size"))))
                    if ins_msg.get("send_type") == "serviceRequest":
                        sendRRCRelease()
                    try:
                        base_ts, base_id = begin_field_window()
                        print("start send fuzzing msg")
                        send_msg = sendFuzzingMessage(ins_msg.get("new_msg").encode())
                    except socket.timeout:
                        print("UE may crashed")
                        break
                    if send_msg == "":
                        print("UE may crashed")
                        break
                    print("send msg:", send_msg)
                    if send_msg == "decode error":
                        reset_insteresting(ins_msg)
                        break
                    resp_json = json.loads(send_msg)
                    byte_mut = bool(resp_json.get("byte_mut"))
                    if not byte_mut:
                        is_interesting = check_new_resopnse(state, ins_msg.get("send_type"), resp_json.get("ret_msg"), resp_json.get("mm_status"))
                    if is_interesting:
                        curr_state.addEnergy(1)
                        msg_add_energy(ins_msg, 1)

                    # +++ 
                    err_resp = drain_gnb_error_since_last()
                    if err_resp:
                        if_error = True
                        error_cause = err_resp
                        print("feedback from gNB log", err_resp)
                        if not byte_mut:
                            is_interesting = check_new_cause(state, ins_msg.get("send_type"), error_cause)
                        if is_interesting:
                            curr_state.addEnergy(0.5)
                            msg_add_energy(ins_msg, 0.5)

                    # probe AMF
                    print("send probe to AMF")
                    pending_global_reset = False
                    # if_crash = check_amf()
                    if_crash, amf_crash_list = check_amf_crash(core_log_path="logs/core.log")
                    if if_crash:
                        print("amf crashed")
                        fuzzing = False
                        pending_global_reset = True
                        print(f"[AMF] Detect {len(amf_crash_list)} crash:")
                        for it in amf_crash_list[:3]:
                            print(f"L{it['line_no']} {it['keyword']}: {it['text']}")
                        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        crash_log = f"logs/crash/crash_amf_worker{WID}_{now}.log"
                        shutil.copy("logs/core.log", crash_log)
                        print(f"{now}: [AMF] Crash log saved to {crash_log}")

                    if resp_json.get("ret_type") != "":
                        fuzzing = False
                    violation = curr_state.oracle.query_message(ins_msg.get("send_type"), resp_json.get("ret_type"), resp_json.get("sht"), resp_json.get("secmod"))
                    print("violation: ", violation)
                    if violation:
                        violation = check_new_violation(state, ins_msg.get("send_type"), resp_json.get("ret_type"), resp_json.get("sht"), resp_json.get("secmod"))
                    # send probe to SMF
                    if ins_msg.get("send_type") in symbols_sm:
                        print("send probe to SMF")
                        # if_crash_sm = check_smf()
                        if_crash_sm, smf_crash_list = check_smf_crash(core_log_path="logs/core.log")
                        if if_crash_sm:
                            print(f"[SMF] Detect {len(smf_crash_list)} crash:")
                            for it in smf_crash_list[:3]:
                                print(f"L{it['line_no']} {it['keyword']}: {it['text']}") 
                            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                            crash_smf_log = f"logs/crash/crash_smf_worker{WID}_{now}.log"
                            shutil.copy("logs/core.log", crash_smf_log)
                            print(f"{now}: [SMF] Crash log saved to {crash_smf_log}")
                    store_new_message(worker_id=WID,
                                      if_fuzz=True,
                                      state=state,
                                      send_type=ins_msg.get("send_type"),
                                      ret_type=resp_json.get("ret_type"),
                                      if_crash=if_crash,
                                      if_crash_sm=if_crash_sm,
                                      is_interesting=is_interesting,
                                      if_error=if_error,
                                      error_cause=error_cause,
                                      sht=resp_json.get("sht"),
                                      secmod=resp_json.get("secmod"),
                                      base_msg=ins_msg.get("new_msg"),
                                      new_msg=resp_json.get("new_msg"),
                                      ret_msg=resp_json.get("ret_msg"),
                                      violation=violation,
                                      mm_status=resp_json.get("mm_status"),
                                      byte_mut=byte_mut)
                    if resp_json.get("ret_type") != "" and not fsm.search_new_transition(state, ins_msg.get("send_type"), resp_json.get("ret_type")) and not byte_mut:
                        print("get a different return msg")
                        message_str = ins_msg.get("send_type")+":"+resp_json.get("new_msg")+":"+str(resp_json.get("secmod"))+":"+str(resp_json.get("sht"))
                        responses = []
                        new_state_error = False
                        for symbol in symbols_fsm:
                            i = 0
                            while i < 10:
                                if not PARALLEL:
                                    reset(full_reset)
                                    full_reset = False
                                i = i + 1
                                try:
                                    if not PARALLEL:
                                        connectGNB()
                                    print(f"[Worker{WID}] connectUE start - 2")
                                    connectUE()
                                    print(f"[Worker{WID}] connectUE done - 2")
                                except socket.timeout:
                                    print("UE Connection timeout2, retrying...")
                                    continue
                                if sendSymbol(message_str) != resp_json.get("ret_type"):
                                    print("response to new symbol not match, retrying...")
                                    continue
                                res = sendSymbol(symbol)
                                if res == "":
                                    print("UE may crashed, retrying...")
                                    continue
                                responses.append(res)
                                break
                            if i == 10:
                                print("error in learning new state, giving up...")
                                new_state_error = True
                                break
                        if new_state_error:
                            break
                        print(responses)
                        # check if new state
                        map_state = ""
                        for s in fsm.states:
                            for i in range(len(symbols_fsm)):
                                if not fsm.search_transition(s.name, symbols_fsm[i], responses[i]):
                                    break
                                if i == len(symbols_fsm) - 1:
                                    map_state = s.name
                            if map_state != "":
                                break
                        if map_state != "":
                            is_new_state = False
                            is_new_transition = True
                            new_transition = [state, message_str, resp_json.get("ret_type"), map_state]
                            fsm.transitions.append(new_transition)
                            for s in fsm.states:
                                get_all_paths(fsm, s)
                            print("new transition added")
                            print(new_transition)
                        else:
                            is_new_state = True
                            is_new_transition = True
                            new_state = fsm.add_new_state()
                            new_transition = [state, message_str, resp_json.get("ret_type"), new_state.name]
                            fsm.transitions.append(new_transition)
                            # append learned input/output transitions as self loop
                            for i in range(len(symbols_fsm)):
                                new_transition = [new_state.name, symbols_fsm[i], responses[i], new_state.name]
                                fsm.transitions.append(new_transition)
                            for s in fsm.states:
                                get_all_paths(fsm, s)
                            new_state.oracle.decide_state(new_state)
                            print("new state added")
                    
                    if pending_global_reset:
                        if PARALLEL:
                            request_global_reset("amf_crash")
                        else:
                            full_reset = True
                    break

                # +++ 
                sendSymbol("syncUp")
                print("syncUp done")

                if not PARALLEL:
                    gNBsocket.close()
                UEsocket.close()
                print("socket closed")

                is_interesting_state = violation or if_crash or if_crash_sm \
                                or (resp_json.get("ret_type") not in ("", None)
                                    and not fsm.search_new_transition(state,
                                                                        ins_msg.get("send_type"),
                                                                        resp_json.get("ret_type")))
                error_bonus = 0.0
                error_flag = violation or if_crash or if_crash_sm     
                if error_flag:
                    error_hits[state] += 1
                    error_bonus = 1.0 / (error_hits[state] ** 0.5)

                new_trans_path = is_new_transition
                new_fields = count_window_fields(int(WID), base_ts, base_id)
                print("new_fields: ", new_fields)
                # +++ 
                mcts_reward = schedule_amf.backpropagate(path=mcts_path_exec_amf, new_state=is_new_state, new_transition=new_trans_path, error_reward=error_bonus, new_fields_cnt=new_fields)
                if used_smf:
                    schedule_smf.backpropagate(path=mcts_path_exec_smf, new_state=is_new_state, new_transition=new_trans_path, error_reward=error_bonus, new_fields_cnt=new_fields)
                update_msg_reward(ins_msg, mcts_reward)

                fsm_file = open(WORK_DIR / './savedFSM.json', 'w')
                fsm_file.write(fsm.to_json())
                fsm_file.close()

            else:
                print("start fuzzing error, resetting...")
        except Exception as e:
            print(e)
            error_file = open('./logs/error.log', 'a')
            error_file.write(time.strftime("%Y-%m-%d %H:%M:%S ", time.localtime()))
            error_file.write(str(e)+"\n")
            error_file.close()
            if not PARALLEL:
                full_reset = True
            continue