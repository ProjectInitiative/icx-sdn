"""Full switch data ingest via HTTP web API (no SSH).

Mimics the parser.py output schema so the web UI at /api/data
works without changes. Much lighter than SSH scraping.

Usage:
    nix develop -c python scripts/ingest_via_web.py
    nix develop -c python scripts/ingest_via_web.py --watch
"""

import json
import os
import re
import time
import subprocess
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from icx_monitor.web_api import ICXWebClient, parse_tables, table_to_dicts

POLL_INTERVAL = 30


def parse_chassis_from_device(html):
    chassis = {"power_supplies": [], "fans": [], "temperatures": {}, "macs": {}}
    for m in re.finditer(r"Power supply (\d+) \((.*?)\) present, status (\S+)", html):
        chassis["power_supplies"].append({"id": int(m.group(1)), "type": m.group(2), "status": m.group(3)})
    for m in re.finditer(r"Fan (\d+) (\S+), speed", html):
        chassis["fans"].append({"id": int(m.group(1)), "status": m.group(2)})
    for m in re.finditer(r"[Tt]emperature:\s*<[^>]*>\s*([\d.]+)\s*C", html):
        chassis["temperatures"]["chassis"] = float(m.group(1))
    return chassis


def ingest_one(api):
    data = {}
    device_html = api._req("/device.htm")

    # Version
    m = re.search(r"Running Image Version.*?Version\s*<[^>]*>\s*([\w.]+)", device_html)
    version = m.group(1) if m else ""

    # Hostname via SNMP (best-effort)
    hostname = ""
    try:
        host = os.environ.get("ICX_SWITCH_HOST", "172.16.1.15")
        c = open("data/snmp_community.txt").read().strip()
        r = subprocess.run(
            ["snmpget", "-v2c", "-c", c, host, "1.3.6.1.2.1.1.5.0", "-Oqv", "-t", "3", "-r", "1"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            hostname = r.stdout.strip().strip('"')
    except Exception:
        pass

    data["config"] = {
        "version": version,
        "hostname": hostname,
        "vlans": {},
        "interfaces": {},
        "lags": [],
        "global": {},
    }

    # Chassis
    data["chassis"] = parse_chassis_from_device(device_html)

    # Port statistics
    stats_raw = api.get_port_statistics()
    stats = {}
    for row in stats_raw:
        port = row.get("col0", row.get("Port", "")).strip()
        if port.startswith("1/") or port == "mgmt1":
            try:
                stats[port] = {
                    "in_packets": int(row.get("Rx", 0)),
                    "out_packets": int(row.get("Tx", 0)),
                    "in_errors": int(row.get("Align", 0)) + int(row.get("FCS", 0)),
                    "out_errors": int(row.get("Giant", 0)) + int(row.get("Short", 0)),
                }
            except (ValueError, TypeError):
                pass
    data["statistics"] = stats

    # Interface brief (port attributes)
    port_attrib = api._dicts("/Forms/PortAttbStackUnit", {"common_stack_id": "1"})
    interfaces = {}
    for row in port_attrib:
        port = row.get("Port", "").replace(":", "").strip()
        if port.startswith("1/") or port == "mgmt1":
            interfaces[port] = {
                "port": port,
                "link": "Up" if row.get("State", "") == "Forward" else "Down",
                "state": row.get("State", ""),
                "duplex": "",
                "speed": "",
                "trunk": None,
                "tag": row.get("Tag", row.get("VLAN", "")),
                "pvid": None,
                "priority": "",
                "mac": row.get("MAC Address", ""),
                "name": "",
            }
    data["interfaces"] = interfaces
    # MACs from port attributes (first port MAC = base MAC)
    if port_attrib:
        first_mac = port_attrib[0].get("MAC Address", "").replace("-", "").replace(":", "")
        if len(first_mac) == 12:
            formatted = ":".join(first_mac[i:i+2] for i in range(0, 12, 2))
            data["chassis"]["macs"]["boot_prom"] = formatted
            data["chassis"]["macs"]["management"] = formatted

    # Port config (speed, duplex, PoE, flow-control, trunk)
    port_cfg = table_to_dicts(parse_tables(
        api._req("/Forms/PortCfgStackUnit", {"common_stack_id": "1"})
    )[0])
    for row in port_cfg:
        port = row.get("Port", "").strip()
        if not port.startswith("1/"):
            continue
        if port not in data["config"]["interfaces"]:
            data["config"]["interfaces"][port] = {
                "port": port, "dual_mode": None, "inline_power": False,
                "speed_duplex": None, "disabled": False, "flow_control": None,
            }
        cfg = data["config"]["interfaces"][port]
        actual = row.get("Actualspeed/mode", "")
        if actual:
            cfg["speed_duplex"] = actual
        cfg["inline_power"] = row.get("Inline Power", row.get("col15", "")).strip() == "Enabled"
        fc = row.get("FlowCtrl", "").strip()
        if fc:
            cfg["flow_control"] = fc
        trunk = row.get("Trunk", "").strip()
        if trunk and trunk not in ("None", ""):
            if port in interfaces:
                interfaces[port]["trunk"] = trunk

    # LAGs from vShLag page
    lag_tables = parse_tables(api._req("/vShLag.htm"))
    lag_names = set()
    for table in lag_tables:
        for row in table:
            first = re.sub(r"<[^>]+>", "", (row or [""])[0]).strip() if row else ""
            for cell in row:
                cell_txt = re.sub(r"<[^>]+>", "", cell).strip()
                if cell_txt.startswith("beef") or cell_txt.startswith("server"):
                    lag_names.add(cell_txt)
    data["config"]["lags"] = [{"name": n, "id": i + 1} for i, n in enumerate(sorted(lag_names))]
    data["lags"] = [{"name": n, "id": i + 1, "ports": [], "primary_port": None, "type": None, "lacp_key": None, "partner": {}}
                     for i, n in enumerate(sorted(lag_names))]
    data["lag_details"] = data["lags"]

    # Merge config into interfaces
    for pid, port in interfaces.items():
        cfg = data["config"]["interfaces"].get(pid, {})
        port["inline_power"] = cfg.get("inline_power", False)
        port["dual_mode"] = cfg.get("dual_mode")
        port["speed_duplex"] = cfg.get("speed_duplex")
        port["disabled"] = cfg.get("disabled", False)
        port["flow_control"] = cfg.get("flow_control")
        if pid in stats:
            port["stats"] = stats[pid]
        port["vlans"] = []

    data["_meta"] = {"parsed_at": datetime.now().isoformat(), "source": "web_api"}
    return data


def main():
    import sys as _sys
    watch = "-w" in _sys.argv or "--watch" in _sys.argv
    api = ICXWebClient()

    out = Path("data") / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    if watch:
        print(f"Polling every {POLL_INTERVAL}s...")
        while True:
            data = ingest_one(api)
            with open(out, "w") as f:
                json.dump(data, f, indent=2)
            n = len(data.get("interfaces", {}))
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {n} interfaces — {out} ({out.stat().st_size / 1024:.1f} KB)")
            time.sleep(POLL_INTERVAL)
    else:
        data = ingest_one(api)
        with open(out, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Saved to {out} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
