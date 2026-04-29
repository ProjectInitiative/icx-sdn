import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

def _project_root():
    env = os.environ.get("ICX_MONITOR_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent

PROJECT_ROOT = _project_root()
DATA_DIR = PROJECT_ROOT / "data"
COMMUNITY_FILE = DATA_DIR / "snmp_community.txt"
LIVE_FILE = DATA_DIR / "live.json"
HOST = "172.16.1.15"
POLL_INTERVAL = 30

OID_ifDescr = "1.3.6.1.2.1.2.2.1.2"
OID_ifOperStatus = "1.3.6.1.2.1.2.2.1.8"
OID_ifInErrors = "1.3.6.1.2.1.2.2.1.14"
OID_ifOutErrors = "1.3.6.1.2.1.2.2.1.20"
OID_ifHCInOctets = "1.3.6.1.2.1.31.1.1.1.6"
OID_ifHCOutOctets = "1.3.6.1.2.1.31.1.1.1.10"

OID_chasTemp = "1.3.6.1.4.1.1991.1.1.1.1.18.0"
OID_fanStatus = "1.3.6.1.4.1.1991.1.1.1.1.19.0"
OID_psuStatus = "1.3.6.1.4.1.1991.1.1.1.1.20.0"


def _snmp_run(mode, oid):
    c = open(COMMUNITY_FILE).read().strip() if COMMUNITY_FILE.exists() else ""
    if not c:
        return ""
    tool = "snmpwalk" if mode == "walk" else "snmpget"
    args = f"-v2c -c {c} {HOST} {oid} -t 3 -r 1"
    cmd = ["nix-shell", "-p", "net-snmp", "--run", f"{tool} {args}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def _strip_type(val):
    val = val.strip().strip('"')
    if ": " in val:
        _, val = val.split(": ", 1)
    return val.strip().strip('"')

def snmpwalk(oid):
    out = _snmp_run("walk", oid)
    if not out:
        return {}
    results = {}
    for line in out.split("\n"):
        line = line.strip()
        if not line or " = " not in line:
            continue
        oid_str, val = line.split(" = ", 1)
        idx = oid_str.split(".")[-1]
        results[idx] = _strip_type(val)
    return results


def snmpget(oid):
    out = _snmp_run("get", oid)
    if not out or " = " not in out:
        return None
    val = out.split(" = ", 1)[1]
    return _strip_type(val)


def _int(v, default=0):
    if v is None:
        return default
    v = str(v)
    m = __import__("re").search(r"\((-?\d+)\)", v)
    if m:
        return int(m.group(1))
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _walk_safe(oid):
    try:
        return snmpwalk(oid)
    except Exception:
        return {}


def _get_safe(oid):
    try:
        return snmpget(oid)
    except Exception:
        return None


def poll():
    c = open(COMMUNITY_FILE).read().strip() if COMMUNITY_FILE.exists() else None
    if not c:
        return {"error": "Create data/snmp_community.txt with SNMP read community"}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_walk_safe, OID_ifDescr): "if_desc",
            pool.submit(_walk_safe, OID_ifOperStatus): "if_oper",
            pool.submit(_walk_safe, OID_ifHCInOctets): "if_hc_in",
            pool.submit(_walk_safe, OID_ifHCOutOctets): "if_hc_out",
            pool.submit(_walk_safe, OID_ifInErrors): "if_in_errs",
            pool.submit(_walk_safe, OID_ifOutErrors): "if_out_errs",
            pool.submit(_get_safe, OID_chasTemp): "temp",
            pool.submit(_get_safe, OID_fanStatus): "fan",
            pool.submit(_get_safe, OID_psuStatus): "psu",
        }
        results = {}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = {} if "if_" in key else None

    if_desc = results.get("if_desc", {})
    if not if_desc:
        return {"error": "SNMP walk failed — check community and connectivity"}

    interfaces = {}
    for idx, desc in if_desc.items():
        interfaces[desc] = {
            "port": desc,
            "oper_status": _int(results.get("if_oper", {}).get(idx, 0)),
            "in_octets": _int(results.get("if_hc_in", {}).get(idx, 0)),
            "out_octets": _int(results.get("if_hc_out", {}).get(idx, 0)),
            "in_errors": _int(results.get("if_in_errs", {}).get(idx, 0)),
            "out_errors": _int(results.get("if_out_errs", {}).get(idx, 0)),
        }

    chassis = {}
    t = results.get("temp")
    if t:
        chassis["temperature"] = round(_int(t) / 10, 1)

    _fan_raw = results.get("fan")
    if _fan_raw is not None:
        chassis["fans_raw"] = _fan_raw

    _psu_raw = results.get("psu")
    if _psu_raw is not None:
        chassis["psu_raw"] = _psu_raw

    return {
        "timestamp": time.time(),
        "interval": POLL_INTERVAL,
        "interfaces": interfaces,
        "chassis": chassis,
    }


def main():
    if not COMMUNITY_FILE.exists():
        print("Create data/snmp_community.txt with your SNMP read community string", file=sys.stderr)
        sys.exit(1)

    watch = "-w" in sys.argv or "--watch" in sys.argv
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if watch:
        LIVE_FILE.write_text(json.dumps({
            "_mode": "starting",
            "timestamp": time.time(),
            "message": "SNMP poller starting — first poll in progress...",
        }))
        while True:
            data = poll()
            data["_mode"] = "stream"
            LIVE_FILE.write_text(json.dumps(data, indent=2))
            n = len(data.get("interfaces", {}))
            err = data.get("error", "")
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {n} interfaces" + (f" — {err}" if err else ""), file=sys.stderr)
            time.sleep(POLL_INTERVAL)
    else:
        print(json.dumps(poll(), indent=2))


if __name__ == "__main__":
    main()
