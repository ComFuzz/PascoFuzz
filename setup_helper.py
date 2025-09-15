from dotenv import dotenv_values
import os, subprocess, time, pathlib, signal
# helper functions for start and kill the components

config = dotenv_values(".env")
IMSI_OFFSET = 0
MAX_IMSI_OFFSET = 98

# +++ 
LOG_DIR     = pathlib.Path("./logs")
PORT_BASE   = 45678                
IMSI_BASE   = 999700000000001    

UE_PROC  = None
UE2_PROC = None
UE3_PROC = None

# +++ 
def init_setup_path(port_base:int, imsi_base:int, logdir:str):
    global PORT_BASE, IMSI_BASE, WID_LOG_DIR
    PORT_BASE  = port_base
    IMSI_BASE  = imsi_base
    WID_LOG_DIR = pathlib.Path(logdir)

def setOffset(new_offset:int):
    global IMSI_OFFSET
    IMSI_OFFSET = new_offset

def getOffset():
    global IMSI_OFFSET
    return IMSI_OFFSET

def startCore():
    with open("./logs/core.log", "w") as out:
        cfg = os.path.join(config["OPEN5GS_PATH"], "build", "configs", "sample.yaml")
        subprocess.Popen(args=["5gc", "-c", cfg], stdout=out, stderr=out, 
                         start_new_session=True)

def startGNB():
    with open("./logs/gnb.log", "w") as out:
        cfg = os.path.join(config["UERANSIM_PATH"], "config", "open5gs-gnb.yaml")
        subprocess.Popen(args=["nr-gnb", "-c", cfg], stdout=out, stderr=out, 
                         start_new_session=True)

# def startUE():
#     with open("./logs/ue.log", "w") as out:
#         cfg = os.path.join(config["UERANSIM_PATH"], "config", "open5gs-ue.yaml")
#         imsi = f"imsi-{999700000000001 + IMSI_OFFSET}"
#         subprocess.Popen(args=["nr-ue", "-c", cfg, "-i", imsi], stdout=out, 
#                          stderr=out, start_new_session=True)
        
def startUE():
    global UE_PROC
    with open(WID_LOG_DIR / "ue.log", "w") as out:
        cfg  = os.path.join(config["UERANSIM_PATH"], "config", "open5gs-ue.yaml")
        imsi = f"imsi-{IMSI_BASE + IMSI_OFFSET}"
        print("ue imsi:", imsi, "port:", PORT_BASE)
        UE_PROC = subprocess.Popen(args=["nr-ue", "-c", cfg, "-i", imsi, "-p", str(PORT_BASE)],
                        stdout=out, stderr=out, start_new_session=True
        )

def startUE2():
    global IMSI_OFFSET, UE2_PROC
    IMSI_OFFSET += 1
    with open(WID_LOG_DIR / "ue2.log", "w") as out:
        cfg = os.path.join(config["UERANSIM_PATH"], "config", "open5gs-ue.yaml")
        imsi = f"imsi-{IMSI_BASE + IMSI_OFFSET}"
        print("ue2 imsi:", imsi, "port:", PORT_BASE + 1)
        UE2_PROC = subprocess.Popen(args=["nr-ue", "-c", cfg, "-i", imsi, "-p", str(PORT_BASE + 1)], 
                        stdout=out, stderr=out, start_new_session=True)
        
def startUE3():
    global IMSI_OFFSET, UE3_PROC
    IMSI_OFFSET += 1
    with open(WID_LOG_DIR / "ue3.log", "w") as out:
        cfg = os.path.join(config["UERANSIM_PATH"], "config", "open5gs-ue.yaml")
        imsi = f"imsi-{IMSI_BASE + IMSI_OFFSET}"
        print("ue3 imsi:", imsi, "port:", PORT_BASE + 2)
        UE3_PROC = subprocess.Popen(args=["nr-ue", "-c", cfg, "-i", imsi, "-p", str(PORT_BASE + 2)], 
                        stdout=out, stderr=out, start_new_session=True)

def killCore():
    subprocess.run(["pkill", "-2", "-f", "5gc"], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc = subprocess.run(["ps", "-ef"], encoding='utf-8', stdout=subprocess.PIPE)
    for line in proc.stdout.split("\n"):
        if "open5gs" not in line:
            continue
        pid = line.split()[1]
        print(f"Killing pid {pid}")
        subprocess.run(["kill", "-2", pid])

def killGNB():
    subprocess.run(["pkill", "-2", "-f", "nr-gnb"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def killUE_all():
    subprocess.run(["pkill", "-2", "-f", "nr-ue"], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def UE_Terminate(proc):
    if proc and proc.poll() is None: 
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            print(f"UE process {proc.pid} did not terminate gracefully, killing.")
            proc.kill()
            proc.wait()
        except ProcessLookupError:
            pass 

def killUE():
    global UE_PROC, UE2_PROC, UE3_PROC
    UE_Terminate(UE_PROC);  UE_PROC  = None
    UE_Terminate(UE2_PROC); UE2_PROC = None
    UE_Terminate(UE3_PROC); UE3_PROC = None


def sendRRCRelease():
    subprocess.Popen(args=["nr-cli", "UERANSIM-gnb-999-70-1", "--exec", "ue-release 1"])
    time.sleep(0.25)
