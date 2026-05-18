"""Sample script using the Brocade ICX Web API.

Fetches all key data via HTTP (no SSH) and saves to JSON.

Usage:
    nix develop -c python scripts/ingest_via_web.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from icx_monitor.web_api import ICXWebClient


def main():
    api = ICXWebClient()
    data = {}

    print("Fetching switch data via HTTP API...")

    data["system"] = api.get_system_info()
    print(f"  system: {len(data['system'])} fields")

    data["port_statistics"] = api.get_port_statistics()
    print(f"  port_stats: {len(data['port_statistics'])} ports")
    if data["port_statistics"]:
        p = data["port_statistics"][0]
        print(f"    e.g. {p.get('Port', '(no Port col)')}: "
              f"Rx={p.get('Rx', '?')}, Tx={p.get('Tx', '?')}")

    data["arp"] = api.get_arp_table()
    print(f"  arp: {len(data['arp'])} entries")

    data["mac"] = api.get_mac_table()
    print(f"  mac: {len(data['mac'])} entries")

    data["stp_status"] = api.get_stp_status()
    print(f"  stp_status: {len(data['stp_status'])} ports")

    data["stack_details"] = api.get_stack_details()
    print(f"  stack_details: {len(data['stack_details'])} records")

    data["memory"] = api.get_memory()
    data["flash"] = api.get_flash()
    data["device"] = api.get_device()

    out_path = Path("data") / "web_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved to {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
