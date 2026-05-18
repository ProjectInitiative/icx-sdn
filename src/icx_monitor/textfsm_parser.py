"""Parser using TextFSM templates instead of hand-written regex."""

import json
import os
import re
import io
from pathlib import Path
from datetime import datetime
from textfsm import TextFSM

from . import _project_root

PROJECT_ROOT = _project_root()
PARSED_DATA_DIR = PROJECT_ROOT / "data"
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(name):
    path = TEMPLATES_DIR / name
    with open(path) as f:
        return TextFSM(f)


_TEMPLATES = {}


def _get_template(name):
    if name not in _TEMPLATES:
        _TEMPLATES[name] = _load_template(name)
    return _TEMPLATES[name]


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

    global_fsm = _get_template("show_running_config_global.textfsm")
    for row in global_fsm.ParseTextToDicts(text):
        if row.get("VERSION"):
            result["version"] = row["VERSION"]
        if row.get("HOSTNAME"):
            result["hostname"] = row["HOSTNAME"]

    for m in re.finditer(r"^vlan (\d+)(?: name (.+?))? by port", text, re.MULTILINE):
        vid = int(m.group(1))
        if vid not in result["vlans"]:
            result["vlans"][vid] = {
                "id": vid,
                "name": (m.group(2) or "").strip(),
                "tagged": [],
                "untagged": [],
                "router_interface": None,
            }

    vlan_fsm = _get_template("show_running_config_vlans.textfsm")
    for row in vlan_fsm.ParseTextToDicts(text):
        vid = row.get("VLAN_ID", "")
        if not vid:
            continue
        vid = int(vid)
        if vid not in result["vlans"]:
            result["vlans"][vid] = {
                "id": vid,
                "name": row.get("VLAN_NAME", "") or "",
                "tagged": [],
                "untagged": [],
                "router_interface": None,
            }
        vlan = result["vlans"][vid]
        if row.get("VLAN_NAME") and not vlan["name"]:
            vlan["name"] = row["VLAN_NAME"]
        if row.get("TAGGED"):
            vlan["tagged"].extend(expand_port_range(row["TAGGED"]))
        if row.get("UNTAGGED"):
            vlan["untagged"].extend(expand_port_range(row["UNTAGGED"]))
        if row.get("ROUTER_INTERFACE"):
            vlan["router_interface"] = int(row["ROUTER_INTERFACE"])

    intf_fsm = _get_template("show_running_config_interfaces.textfsm")
    for row in intf_fsm.ParseTextToDicts(text):
        port = row.get("PORT", "")
        if not port or not any(
            [
                row.get("DUAL_MODE"),
                row.get("INLINE_POWER"),
                row.get("SPEED_DUPLEX"),
                row.get("DISABLED"),
                row.get("FLOW_CONTROL"),
            ]
        ):
            continue
        if port not in result["interfaces"]:
            result["interfaces"][port] = {
                "port": port,
                "dual_mode": None,
                "inline_power": False,
                "speed_duplex": None,
                "disabled": False,
                "flow_control": None,
            }
        intf = result["interfaces"][port]
        if row.get("DUAL_MODE"):
            intf["dual_mode"] = int(row["DUAL_MODE"])
        if row.get("INLINE_POWER"):
            intf["inline_power"] = True
        if row.get("SPEED_DUPLEX"):
            intf["speed_duplex"] = row["SPEED_DUPLEX"]
        if row.get("DISABLED") == "disable":
            intf["disabled"] = True
        if row.get("FLOW_CONTROL"):
            intf["flow_control"] = row["FLOW_CONTROL"]

    lag_fsm = _get_template("show_running_config_lags.textfsm")
    for row in lag_fsm.ParseTextToDicts(text):
        if row.get("LAG_NAME") and row.get("LAG_ID"):
            result["lags"].append(
                {
                    "name": row["LAG_NAME"],
                    "id": int(row["LAG_ID"]),
                }
            )

    return result


def expand_port_range(text):
    cleaned = text.replace("ethe ", "").replace("ethe", "")
    tokens = cleaned.split()
    ports = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "to":
            i += 1
            continue
        if i + 2 < len(tokens) and tokens[i + 1] == "to":
            start = tokens[i]
            end = tokens[i + 2]
            prefix = "/".join(start.split("/")[:-1])
            for n in range(int(start.split("/")[-1]), int(end.split("/")[-1]) + 1):
                ports.append(f"{prefix}/{n}")
            i += 3
        else:
            if "/" in tokens[i]:
                ports.append(tokens[i])
            i += 1
    return ports


def parse_interface_brief(text):
    fsm = _get_template("show_interface_brief.textfsm")
    records = fsm.ParseTextToDicts(text)
    ports = {}
    for row in records:
        port = row.get("PORT", "")
        if not port.startswith("1/") or not port:
            continue
        trunk = row.get("TRUNK")
        pvid_raw = row.get("PVID", "")
        try:
            pvid = int(pvid_raw) if pvid_raw not in ("N/A", "None", "") else None
        except ValueError:
            pvid = None
        ports[port] = {
            "port": port,
            "link": row.get("LINK", ""),
            "state": row.get("STATE", ""),
            "duplex": row.get("DUPLEX", ""),
            "speed": row.get("SPEED", ""),
            "trunk": trunk if trunk and trunk != "None" else None,
            "tag": row.get("TAG", ""),
            "pvid": pvid,
            "priority": row.get("PRI", ""),
            "mac": row.get("MAC", ""),
            "name": row.get("NAME", "").strip(),
        }
    return ports


def parse_lags(text):
    fsm = _get_template("show_lag.textfsm")
    records = fsm.ParseTextToDicts(text)
    lags = {}
    for row in records:
        name = row.get("LAG_NAME", "")
        if not name or not any(
            [
                row.get("LAG_PORTS"),
                row.get("LAG_PRIMARY_PORT"),
                row.get("LAG_TYPE"),
                row.get("LACP_KEY"),
            ]
        ):
            continue
        lid = int(row.get("LAG_ID", 0))

        if name not in lags:
            lags[name] = {
                "name": name,
                "id": lid,
                "ports": [],
                "primary_port": None,
                "type": None,
                "lacp_key": None,
                "partner": {},
            }

        lag = lags[name]
        if row.get("LAG_PORTS"):
            for p in row["LAG_PORTS"].split():
                p_clean = p.replace("e ", "")
                if "/" in p_clean:
                    lag["ports"].append(p_clean)
        if row.get("LAG_PRIMARY_PORT"):
            lag["primary_port"] = row["LAG_PRIMARY_PORT"]
        if row.get("LAG_TYPE"):
            lag["type"] = row["LAG_TYPE"]
        if row.get("LACP_KEY"):
            lag["lacp_key"] = int(row["LACP_KEY"])

    return list(lags.values())


def parse_chassis(text):
    fsm = _get_template("show_chassis.textfsm")
    records = fsm.ParseTextToDicts(text)
    chassis = {
        "power_supplies": [],
        "fans": [],
        "temperatures": {},
        "macs": {},
    }
    current_sensor = ""

    for row in records:
        if row.get("PS_ID"):
            chassis["power_supplies"].append(
                {
                    "id": int(row["PS_ID"]),
                    "type": row.get("PS_TYPE", ""),
                    "status": row.get("PS_STATUS", ""),
                }
            )

        if row.get("FAN_ID"):
            chassis["fans"].append(
                {
                    "id": int(row["FAN_ID"]),
                    "status": row.get("FAN_STATUS", ""),
                }
            )

        if row.get("SENSOR_NAME") and not row.get("TEMP"):
            current_sensor = row["SENSOR_NAME"]

        if row.get("TEMP"):
            sensor = current_sensor or row.get("SENSOR_NAME", "") or "unknown"
            chassis["temperatures"][sensor] = float(row["TEMP"])

        if row.get("MAC_TYPE") and row.get("MAC_ADDR"):
            key = row["MAC_TYPE"].lower().replace(" ", "_")
            chassis["macs"][key] = row["MAC_ADDR"]

    return chassis


def parse_statistics(text):
    fsm = _get_template("show_statistics_brief.textfsm")
    records = fsm.ParseTextToDicts(text)
    stats = {}
    for row in records:
        port = row.get("PORT", "")
        if port.startswith("TOTAL"):
            continue
        try:
            stats[port] = {
                "in_packets": int(row.get("IN_PACKETS", 0)),
                "out_packets": int(row.get("OUT_PACKETS", 0)),
                "in_errors": int(row.get("IN_ERRORS", 0)),
                "out_errors": int(row.get("OUT_ERRORS", 0)),
            }
        except (ValueError, TypeError):
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
                port["vlans"].append(
                    {
                        "id": vid,
                        "name": vlan.get("name", ""),
                        "tagged": tagged,
                        "untagged": untagged,
                        "native": is_native,
                    }
                )

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
