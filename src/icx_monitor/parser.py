import re
import json
import os
from pathlib import Path
from datetime import datetime

from . import _project_root

PROJECT_ROOT = _project_root()
PARSED_DATA_DIR = PROJECT_ROOT / "data"


def parse_log_file(filepath):
    with open(filepath) as f:
        raw = f.read()

    sections = {}
    pattern = r"--- START (.+?) ---\n(.*?)\n--- END \1 ---"
    for m in re.finditer(pattern, raw, re.DOTALL):
        sections[m.group(1)] = m.group(2).strip()

    data = {}

    if "show running-config" in sections:
        data["config"] = parse_running_config(sections["show running-config"])

    if "show interface brief" in sections:
        data["interfaces"] = parse_interface_brief(sections["show interface brief"])

    if "show lag" in sections:
        data["lags"] = parse_lags(sections["show lag"])

    if "show chassis" in sections:
        data["chassis"] = parse_chassis(sections["show chassis"])

    if "show statistics brief" in sections:
        data["statistics"] = parse_statistics(sections["show statistics brief"])

    return data


def parse_running_config(text):
    result = {
        "version": "",
        "hostname": "",
        "vlans": {},
        "interfaces": {},
        "lags": [],
        "global": {},
    }

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("ver "):
            result["version"] = line[4:]
        elif line.startswith("hostname "):
            result["hostname"] = line[9:]

    vlan_pattern = re.compile(
        r"^vlan (\d+)(?: name (.+?))? by port"
    )
    current_vlan = None
    for line in text.split("\n"):
        line = line.strip()
        m = vlan_pattern.match(line)
        if m:
            vid = int(m.group(1))
            name = m.group(2) or ""
            current_vlan = {
                "id": vid,
                "name": name,
                "tagged": [],
                "untagged": [],
                "router_interface": None,
            }
            result["vlans"][vid] = current_vlan
            continue

        if current_vlan is not None:
            tm = re.match(r"tagged (.+)", line)
            if tm:
                ports = expand_port_range(tm.group(1))
                current_vlan["tagged"].extend(ports)
            um = re.match(r"untagged (.+)", line)
            if um:
                ports = expand_port_range(um.group(1))
                current_vlan["untagged"].extend(ports)
            rm = re.match(r"router-interface ve (\d+)", line)
            if rm:
                current_vlan["router_interface"] = int(rm.group(1))

    intf_pattern = re.compile(r"^interface ethernet ([\d/]+)")
    current_intf = None
    for line in text.split("\n"):
        line = line.strip()
        m = intf_pattern.match(line)
        if m:
            iface = m.group(1)
            current_intf = {
                "port": iface,
                "dual_mode": None,
                "inline_power": False,
                "speed_duplex": None,
                "disabled": False,
            }
            result["interfaces"][iface] = current_intf
            continue
        if current_intf is not None:
            dm = re.match(r"dual-mode\s+(\d+)", line)
            if dm:
                current_intf["dual_mode"] = int(dm.group(1))
            if "inline power" in line:
                current_intf["inline_power"] = True
            sd = re.match(r"speed-duplex\s+(\S+)", line)
            if sd:
                current_intf["speed_duplex"] = sd.group(1)
            if line == "disable":
                current_intf["disabled"] = True
            if line.startswith("flow-control"):
                current_intf["flow_control"] = line
            if line.startswith("!") or line.startswith("interface"):
                current_intf = None

    lag_stanza_pattern = re.compile(
        r"^lag\s+(\S+)\s+dynamic\s+id\s+(\d+)"
    )
    for line in text.split("\n"):
        line = line.strip()
        m = lag_stanza_pattern.match(line)
        if m:
            result["lags"].append({
                "name": m.group(1),
                "id": int(m.group(2)),
            })

    return result


def expand_port_range(text):
    ports = []
    tokens = text.split()
    items = []
    for t in tokens:
        if t == "to":
            items.append(t)
        else:
            items.append(t.replace("ethe ", "").replace("ethe", ""))
    i = 0
    while i < len(items):
        if items[i] == "to":
            i += 1
            continue
        if i + 2 < len(items) and items[i + 1] == "to":
            start = items[i]
            end = items[i + 2]
            prefix = "/".join(start.split("/")[:-1])
            start_num = int(start.split("/")[-1])
            end_num = int(end.split("/")[-1])
            for n in range(start_num, end_num + 1):
                ports.append(f"{prefix}/{n}")
            i += 3
        else:
            if "/" in items[i]:
                ports.append(items[i])
            i += 1
    return ports


def parse_interface_brief(text):
    ports = {}
    header_seen = False
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("Port") and "Link" in line:
            header_seen = True
            continue
        if not header_seen or not line or line.startswith("ve"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        port_id = parts[0]
        if not port_id.startswith("1/"):
            continue
        ports[port_id] = {
            "port": port_id,
            "link": parts[1],
            "state": parts[2],
            "duplex": parts[3],
            "speed": parts[4],
            "trunk": parts[5] if parts[5] != "None" else None,
            "tag": parts[6],
            "pvid": int(parts[7]) if parts[7] not in ("N/A", "None") else None,
            "priority": parts[8],
            "mac": parts[9] if len(parts) > 9 else "",
            "name": " ".join(parts[10:]) if len(parts) > 10 else "",
        }
    return ports


def parse_lags(text):
    lags = []
    current_lag = None
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r'=== LAG "(.+?)" ID (\d+)', line)
        if m:
            if current_lag:
                lags.append(current_lag)
            current_lag = {
                "name": m.group(1),
                "id": int(m.group(2)),
                "ports": [],
                "primary_port": None,
                "type": None,
                "lacp_key": None,
                "partner": {},
            }
            continue

        if current_lag is not None:
            pm = re.match(r"Ports:\s+(.+)", line)
            if pm:
                for p in pm.group(1).split():
                    p = p.replace("e ", "")
                    if "/" in p:
                        current_lag["ports"].append(p)
            ppm = re.match(r"Primary Port:\s+([\d/]+)", line)
            if ppm:
                current_lag["primary_port"] = ppm.group(1)
            tm = re.match(r"Trunk Type:\s+(.+)", line)
            if tm:
                current_lag["type"] = tm.group(1)
            lm = re.match(r"LACP Key:\s+(\d+)", line)
            if lm:
                current_lag["lacp_key"] = int(lm.group(1))

            part_line = re.match(
                r"([\d/]+)\s+(\S+)\s+(\S+)\s+(\d+)", line
            )
            if part_line and current_lag:
                pid = part_line.group(1)
                if pid in current_lag["ports"]:
                    current_lag["partner"][pid] = {
                        "partner_system_id": part_line.group(2),
                        "partner_key": int(part_line.group(4)),
                    }

    if current_lag:
        lags.append(current_lag)
    return lags


def parse_chassis(text):
    chassis = {
        "power_supplies": [],
        "fans": [],
        "temperatures": {},
        "macs": {},
    }

    ps_pattern = re.compile(
        r"Power supply (\d+) \((.*?)\) present, status (\S+)"
    )
    sensor = ""
    for line in text.split("\n"):
        ls = line.strip()
        m = ps_pattern.search(ls)
        if m:
            psu = {
                "id": int(m.group(1)),
                "type": m.group(2),
                "status": m.group(3),
            }
            chassis["power_supplies"].append(psu)
            continue

        fm = re.match(r"Fan (\d+) (\S+), speed", ls)
        if fm:
            chassis["fans"].append({
                "id": int(fm.group(1)),
                "status": fm.group(2),
            })
            continue

        temp_m = re.match(r"Current temperature\s+:\s+([\d.]+) deg-C", ls)
        if temp_m:
            if sensor:
                chassis["temperatures"][sensor] = float(temp_m.group(1))
            continue

        tm = re.match(r"(.+?) Temperature Readings:", ls)
        if tm:
            sensor = tm.group(1).strip()

        mm = re.match(r"(Boot Prom|Management) MAC\s*:\s*(\S+)", ls)
        if mm:
            chassis["macs"][mm.group(1).lower().replace(" ", "_")] = mm.group(2)

    return chassis


def parse_statistics(text):
    stats = {}
    header_seen = False
    for line in text.split("\n"):
        line = line.strip()
        if "In Packets" in line and "Out Packets" in line:
            header_seen = True
            continue
        if not header_seen or not line or line.startswith("TOTAL"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        port = parts[0]
        if not port.startswith("1/") and port != "mgmt1":
            continue
        try:
            stats[port] = {
                "in_packets": int(parts[1]),
                "out_packets": int(parts[2]),
                "in_errors": int(parts[3]),
                "out_errors": int(parts[4]),
            }
        except ValueError:
            continue
    return stats


def merge_data(data):
    interfaces = data.get("interfaces", {})
    config_intfs = data.get("config", {}).get("interfaces", {})
    vlans = data.get("config", {}).get("vlans", {})
    stats = data.get("statistics", {})
    lag_details = data.get("lags", [])

    for port_id, port in interfaces.items():
        cfg = config_intfs.get(port_id, {})
        port["inline_power"] = cfg.get("inline_power", False)
        port["dual_mode"] = cfg.get("dual_mode")
        port["speed_duplex"] = cfg.get("speed_duplex")
        port["disabled"] = cfg.get("disabled", False)
        port["flow_control"] = cfg.get("flow_control")

        if port_id in stats:
            port["stats"] = stats[port_id]

        port["vlans"] = []
        pvid = port.get("pvid")
        dual = port.get("dual_mode")
        for vid, vlan in vlans.items():
            tagged = port_id in vlan.get("tagged", [])
            untagged = port_id in vlan.get("untagged", [])
            is_native = pvid is not None and vid == pvid
            if dual is not None and vid == dual:
                tagged = False
                untagged = True
            if tagged or untagged or is_native:
                port["vlans"].append({
                    "id": vid,
                    "name": vlan.get("name", ""),
                    "tagged": tagged,
                    "untagged": untagged,
                    "native": is_native,
                })

    data["lag_details"] = lag_details
    return data


def ingest(log_path=None):
    if log_path is None:
        logs = sorted(Path.cwd().glob("switch_data_*.log"))
        if not logs:
            print("No log files found")
            return
        log_path = logs[-1]

    print(f"Parsing {log_path}...")
    data = parse_log_file(str(log_path))
    data = merge_data(data)
    data["_meta"] = {
        "parsed_at": datetime.now().isoformat(),
        "source": os.path.basename(str(log_path)),
    }

    PARSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PARSED_DATA_DIR / "latest.json"
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Written to {out_path}")
    return data


def main():
    import sys
    log = sys.argv[1] if len(sys.argv) > 1 else None
    ingest(log)

if __name__ == "__main__":
    main()
