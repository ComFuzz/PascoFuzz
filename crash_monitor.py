import os, re

_ANSI_RE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')

_CRASH_RE = re.compile(
    r'(fatal|fata|assert|assertion|backtrace|abort|panic|segfault|sigsegv|core dumped)',
    re.IGNORECASE
)
_TAG_RE   = re.compile(r'\[(amf|smf|core)\]', re.IGNORECASE)
_AMF_RE = re.compile(r'\bamf\b', re.IGNORECASE)
_SMF_RE = re.compile(r'\bsmf\b', re.IGNORECASE)

def last_core_log(log_dir: str = "logs") -> str:
    if not os.path.isdir(log_dir):
        return ''
    logs = sorted(
        (os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")),
        key=os.path.getmtime,
        reverse=True
    )
    return logs[0] if logs else ''

def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub('', s)

def classify_component(lines, idx, lookaround=6, last_seen_component=None):
    line = lines[idx]
    tag = _TAG_RE.search(line)
    if tag:
        t = tag.group(1).lower()
        if t in ('amf', 'smf'):
            return t
        if last_seen_component in ('amf', 'smf'):
            return last_seen_component
        
    if _AMF_RE.search(line) and not _SMF_RE.search(line):
        return "amf"
    if _SMF_RE.search(line) and not _AMF_RE.search(line):
        return "smf"
    start = max(0, idx - lookaround)
    end   = min(len(lines), idx + 1 + lookaround)
    ctx = " ".join(lines[start:end])
    amf_hit = bool(_AMF_RE.search(ctx))
    smf_hit = bool(_SMF_RE.search(ctx))
    if amf_hit and not smf_hit:
        return "amf"
    if smf_hit and not amf_hit:
        return "smf"
    return "unknown"

def scan_crash_incidents(core_log_path: str, lookaround=6):

    if not core_log_path or not os.path.isfile(core_log_path):
        return []

    with open(core_log_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_lines = f.readlines()

    lines_clean = [strip_ansi(x).rstrip('\n\r') for x in raw_lines]

    incidents = []
    last_seen_component = None
    for i, line in enumerate(lines_clean):
        tag = _TAG_RE.search(line)
        if tag and tag.group(1).lower() in ('amf', 'smf'):
            last_seen_component = tag.group(1).lower()

        m = _CRASH_RE.search(line)
        if not m:
            continue
        comp = classify_component(lines_clean, i, lookaround=lookaround, last_seen_component=last_seen_component)
        kw = m.group(1).lower() if m.group(1) else m.group(0).lower()
        kw = (m.group(1) or m.group(0)).strip()
        incidents.append({
            "component": comp,       
            "line_no":   i + 1,      
            "keyword":   kw,          
            "text":      line,         
        })
    return incidents

def check_amf_crash(core_log_path: str = None, log_dir: str = "logs"):
    if not core_log_path:
        core_log_path = last_core_log(log_dir)
    incidents = scan_crash_incidents(core_log_path)
    amf_hits = [x for x in incidents if x["component"] == "amf"]
    return (len(amf_hits) > 0, amf_hits)

def check_smf_crash(core_log_path: str = None, log_dir: str = "logs"):
    if not core_log_path:
        core_log_path = last_core_log(log_dir)
    incidents = scan_crash_incidents(core_log_path)
    smf_hits = [x for x in incidents if x["component"] == "smf"]
    return (len(smf_hits) > 0, smf_hits)