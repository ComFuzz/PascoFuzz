from pymongo.mongo_client import MongoClient
from dotenv import dotenv_values
from objects import Seed, PowerSchedule
from bson import ObjectId
import os, time, random, datetime

config = dotenv_values(".env")

# Replace the placeholder with your Atlas connection string
uri = config["MONGO_URI"]
# Set the Stable API version when creating a new client
client = MongoClient(uri)

PARALLEL = int(config["PARALLEL"])
col = client["CoreFuzzer"][config["DB_NAME"]]
col_fields = client["CoreFuzzer"]["fields"]
last_ts = 0 

COUNT_REWARD = 1
LEN_REWARD = 0.5
BACK_REWARD = 0.2

# +++ 
def init_db_path(worker_id: int):
    global WORKER_ID, col
    WORKER_ID = worker_id
    if PARALLEL:
        print(f"{config['DB_NAME']}_w{WORKER_ID}")
        col = client["CoreFuzzer"][f"{config['DB_NAME']}_w{WORKER_ID}"]
    else:
        col = client["CoreFuzzer"][config["DB_NAME"]]
    col.create_index([("state"), ("new_msg"), ("sht"), ("secmod")], unique=True)
    col.create_index([("is_interesting"), ("mutate_count", 1)])

def clear_db_col(worker_id: int):
    col_wid = client["CoreFuzzer"][f"{config['DB_NAME']}_w{worker_id}"]
    col_wid.delete_many({})

def begin_field_window():
    base_ts = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
    base_id = ObjectId("000000000000000000000000")
    tail = (col_fields.find({}, {"_id":1, "ts":1})
            .sort([("ts",-1),("_id",-1)]).limit(1))
    for d in tail:
        base_ts, base_id = d["ts"], d["_id"]
    return base_ts, base_id

def count_window_fields(wid: int, base_ts, base_id) -> int:
    q = {
        "wid": wid,
        "$or": [
            {"ts": {"$gt": base_ts}},
            {"ts": base_ts, "_id": {"$gt": base_id}},
        ],
    }
    return col_fields.count_documents(q)

def read_new_fields():
    global last_ts
    q = {"ts": {"$gt": last_ts}}
    docs = list(col_fields.find(q, {"_id": 0, "ts": 1}))
    if not docs:
        print("new fields is null")
        return 0
    last_ts = max(d["ts"] for d in docs)
    print("new fields: ", len(docs))
    return len(docs)   

def store_new_message(worker_id: int, if_fuzz: bool, state: str, send_type: str, ret_type: str, if_crash: bool, if_crash_sm: bool, is_interesting: bool, if_error: bool, error_cause: str, sht: int, secmod: int, base_msg: str, new_msg: str, ret_msg: str, violation: bool, mm_status: str, byte_mut: bool):
    try:
        col.insert_one({
            "timestamp": time.time(),
            "worker_id": worker_id,
            "if_fuzz": if_fuzz,
            "state": state,
            "send_type": send_type,
            "ret_type": ret_type,
            "if_crash": if_crash,
            "if_crash_sm": if_crash_sm,
            "if_error": if_error,
            "error_cause": error_cause,
            "is_interesting": is_interesting,
            "sht": sht,
            "secmod": secmod,
            "size": len(new_msg),
            "base_msg": base_msg,
            "new_msg": new_msg,
            "ret_msg": ret_msg,
            "energy": 1.0,
            "mutate_count": 0,
            "violation": violation,
            "mm_status": mm_status,
            "byte_mut": byte_mut
            })
    except Exception as e:
        # print(e)
        print("Duplicated message!")

def check_seed_msg(state: str):
    msg_count = col.count_documents(filter={"state": state, "is_interesting": True})
    if msg_count >= 5:
        return True
    else:
        return False

def get_insteresting_msg(state: str):
    doc = col.find({"state": state, "is_interesting": True}) \
            .sort([("energy", -1)]) \
            .limit(10)
    docs = list(doc)
    if not docs:
        raise RuntimeError(f"No interesting messages for state {state}")
    chosen = random.choice(docs)
    col.update_one({"_id": chosen["_id"]}, {"$inc": {"mutate_count": 1}})
    return chosen

def update_msg_reward(msg, reward):
    msg_mutate_count = msg['mutate_count']
    msg_len = msg['size']
    msg_reward = COUNT_REWARD * (1 / max(1, msg_mutate_count)) + LEN_REWARD * (1 / max(1, msg_len)) + BACK_REWARD * reward
    col.update_one({"_id": msg["_id"]}, {"$inc": {"energy": msg_reward}})

def get_msg_by_id(id: str):
    return col.find_one(filter={"_id": id})
    
def msg_add_energy(msg, energy):
    col.update_one(filter={"_id": msg["_id"]},
                   update={"$inc": {"energy": energy}})

def reset_insteresting(msg):
    col.update_one(filter={"_id": msg["_id"]},
                   update={"$set": {"is_interesting": False}})

def check_new_resopnse(state: str, send_type: str, ret_msg: str, mm_status: str):
    if "7E0056" in ret_msg: # exclude duplicated authentication request
        if col.find_one(filter={"state": state, "send_type": send_type, "ret_type": "authenticationRequest"}) != None:
            return False
        else:
            return True
    else:
        if col.find_one(filter={"state": state, "send_type": send_type, "ret_msg": ret_msg, "mm_status": mm_status}) != None:
            return False
        else:
            return True

def check_new_cause(state: str, send_type: str, error_cause: str):
    if col.find_one(filter={"state": state, "send_type": send_type, "error_cause": error_cause}) != None:
        return False
    else:
        return True

# if the violation is unique, return True
def check_new_violation(state: str, send_type: str, ret_type: str, sht: int, secmod: int):
    if col.find_one(filter={"violation": True, "state": state, 
                            "send_type": send_type, "ret_type": ret_type,
                            "sht": sht, "secmod": secmod}) != None:
        return False
    else:
        return True
    
class BaseMsg(Seed):
    def __init__(self, id: str, count: int, energy: float):
        super().__init__()
        self.id = id
        self.count = count
        self.energy = energy
