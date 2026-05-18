"""Brocade ICX Web API client.

HTTP-based alternative to SSH CLI scraping. Handles auth, form POST,
HTML table parsing, and exposes every discovered data endpoint.

Usage:
    from icx_monitor.web_api import ICXWebClient

    api = ICXWebClient()
    ports = api.get_port_statistics()
    vlans = api.get_vlans()
    system = api.get_system_info()
"""

import os
import re
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from html.parser import HTMLParser
from urllib.parse import urljoin


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self._table = None
        self._row = None
        self._cell = None
        self._skip = 0

    def _close_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append(self._cell.strip())
        self._cell = None

    def _close_row(self):
        if self._row is not None and self._table is not None and self._row:
            self._table.append(self._row)
        self._row = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._close_row()
            self._table = []
        elif tag == "tr":
            self._close_cell()
            self._close_row()
            self._row = [] if self._table is not None else None
        elif tag in ("td", "th"):
            self._close_cell()
            self._cell = "" if self._row is not None else None

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table" and self._table is not None:
            self._close_row()
            if self._table:
                self.result.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell is not None:
            self._cell += data

    def error(self, message):
        pass


def parse_tables(html):
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.I)
    p = TableParser()
    p.feed(cleaned)
    return p.result


def table_to_dicts(table):
    if not table or len(table) < 2:
        return []
    # Find header: row before first row that looks like data
    # (doesn't repeat the header text and has enough cells)
    header_idx = 0
    max_cells = max(len(r) for r in table)
    for i, row in enumerate(table):
        first = re.sub(r"<[^>]+>", "", (row or [""])[0]).strip()
        is_header_repeat = any(first == re.sub(r"<[^>]+>", "", c).strip()
                               for c in (table[0] if table else []))
        if len(row) >= max_cells - 1 and not is_header_repeat and i > 0:
            header_idx = i - 1
            break
    else:
        header_idx = max(range(len(table)), key=lambda i: len(table[i]))
    raw = table[header_idx]
    headers = [re.sub(r"<[^>]+>", "", c).strip() for c in raw]
    result = []
    for row in table[header_idx + 1:]:
        first = re.sub(r"<[^>]+>", "", (row or [""])[0]).strip()
        # Skip rows that look like headers
        if any(first == h for h in headers if h and len(h) > 2):
            continue
        if len(row) < len(headers) - 1:
            continue
        item = {}
        for i, cell in enumerate(row):
            key = headers[i] if i < len(headers) else f"col{i}"
            val = re.sub(r"<[^>]+>", "", cell).strip()
            item[key] = val
        if any(v for v in item.values()):
            result.append(item)
    return result


class ICXWebClient:
    def __init__(self, host=None, user=None, password=None):
        self.base = f"http://{host or os.environ.get('ICX_SWITCH_HOST', '172.16.1.15')}"
        user = user or os.environ.get("ICX_SWITCH_USER", "admin")
        password = password or os.getenv("ICX_WEB_PASSWORD") or os.getenv("ICX_SSH_PASSWORD", "")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def _req(self, path, data=None):
        url = urljoin(self.base, path)
        req = urllib.request.Request(url)
        req.add_header("Authorization", self.auth)
        if data:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            body = urllib.parse.urlencode(data).encode()
        else:
            body = None
        try:
            resp = urllib.request.urlopen(req, data=body, timeout=15)
            return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {path}") from e

    def _tables(self, path, data=None):
        return parse_tables(self._req(path, data))

    def _dicts(self, path, data=None, idx=0):
        tables = self._tables(path, data)
        if idx < len(tables):
            return table_to_dicts(tables[idx])
        return []

    # ── System Info (GET pages) ───────────────────────────────

    def get_system_info(self):
        tables = self._tables("/rsystem.htm")
        data = {}
        for table in tables:
            for row in table:
                if len(row) >= 2:
                    k = re.sub(r"<[^>]+>", "", row[0]).strip()
                    v = re.sub(r"<[^>]+>", "", row[-1]).strip()
                    if k:
                        data[k] = v
        return data

    def get_device(self):
        return self._dicts("/device.htm")

    def get_memory(self):
        return self._dicts("/memory.htm")

    def get_flash(self):
        return self._dicts("/flash.htm")

    # ── Port Statistics & Config ──────────────────────────────

    def get_port_statistics(self):
        return self._dicts("/Forms/StatClear", {"Submit": "Display"})

    def get_port_config(self, stack_id="1"):
        return self._dicts("/Forms/PortCfgStackUnit", {"common_stack_id": str(stack_id)})

    def get_port_attributes(self, stack_id="1"):
        return self._dicts("/Forms/PortAttbStackUnit", {"common_stack_id": str(stack_id)})

    def get_port_utilization(self):
        return self._dicts("/Forms/UtilClear", {"Submit": "Display"})

    # ── VLAN ──────────────────────────────────────────────────

    def get_vlans(self, per_page="20"):
        return self._dicts("/Forms/StackVlan", {"stackvlan_per_page": str(per_page)})

    # ── LAG ───────────────────────────────────────────────────

    def get_lag_detail(self, lag_name, port1="", port2=""):
        params = {"lag_name": lag_name}
        if port1:
            params["LagLabel1"] = port1
        if port2:
            params["LagLabel2"] = port2
        return self._dicts("/vShLag.htm", params)

    # ── PoE ───────────────────────────────────────────────────

    def get_poe_config(self):
        return self._dicts("/stackPOEcfg.htm")

    # ── STP ───────────────────────────────────────────────────

    def get_stp_status(self, stack_id="1"):
        return self._dicts("/Forms/STPStatStackUnit", {"common_stack_id": str(stack_id)})

    def get_stp_config(self, stack_id="1"):
        return self._dicts("/Forms/STPCfgStackUnit", {"common_stack_id": str(stack_id)})

    # ── IP / Routing (GET pages) ──────────────────────────────

    def get_arp_table(self):
        return self._dicts("/showARP.htm")

    def get_mac_table(self):
        return self._dicts("/showmac.htm")

    def get_system_log(self):
        return self._dicts("/shlog.htm")

    # ── IP Config (POST forms) ────────────────────────────────

    def get_ip_config(self):
        return self._dicts("/Forms/ip")

    def get_dns_config(self):
        return self._dicts("/Forms/DNS")

    def get_static_routes(self):
        return self._dicts("/stroute.htm")

    def get_udp_helper(self):
        return self._dicts("/Forms/UDPHelper")

    def get_static_arp(self):
        return self._dicts("/Forms/starp")

    def get_static_rarp(self):
        return self._dicts("/Forms/strarp")

    def get_ip_prefix_lists(self):
        return self._dicts("/Forms/ipprefix")

    def get_ip_access_filters(self):
        return self._dicts("/Forms/ipapfltr")

    def get_ip_community_filters(self):
        return self._dicts("/Forms/ipcomfltr")

    # ── Stack ─────────────────────────────────────────────────

    def get_stack_details(self):
        return self._dicts("/stackdetails.htm")

    def get_stack_modules(self):
        return self._dicts("/stackmodules.htm")

    def get_stack_neighbors(self):
        return self._dicts("/stackneig.htm")

    def get_stack_ports_stats(self):
        return self._dicts("/Forms/StackPortsStats", {"Submit": "Display"})

    def get_stack_ports_status(self):
        return self._dicts("/stackportsstatus.htm")

    def get_stack_ports_interface(self):
        return self._dicts("/stackportsinterface.htm")

    def get_stack_resource(self):
        return self._dicts("/stackresource.htm")

    # ── QoS ───────────────────────────────────────────────────

    def get_qos_profiles(self):
        return self._dicts("/Forms/qos_profile")

    def get_qos_mapping(self):
        return self._dicts("/Forms/qos_mapping")

    # ── RMON ──────────────────────────────────────────────────

    def get_rmon_statistics(self, stack_id="1"):
        return self._dicts("/Forms/ShRMonEthStatStackUnit", {"common_stack_id": str(stack_id)})

    # ── Other ─────────────────────────────────────────────────

    def get_boot_sequence(self):
        return self._dicts("/Forms/bootsq")

    def get_tftp_config(self):
        return self._dicts("/Forms/tcfg")

    def get_web_preferences(self):
        return self._dicts("/Forms/prefer")

    # ── Bulk ──────────────────────────────────────────────────

    def get_all(self):
        methods = [
            ("system_info", self.get_system_info),
            ("device", self.get_device),
            ("memory", self.get_memory),
            ("flash", self.get_flash),
            ("port_statistics", self.get_port_statistics),
            ("port_config", self.get_port_config),
            ("port_attributes", self.get_port_attributes),
            ("port_utilization", self.get_port_utilization),
            ("vlans", self.get_vlans),
            ("arp", self.get_arp_table),
            ("mac", self.get_mac_table),
            ("stp_status", self.get_stp_status),
            ("stp_config", self.get_stp_config),
            ("poe", self.get_poe_config),
            ("stack_details", self.get_stack_details),
            ("stack_modules", self.get_stack_modules),
            ("stack_neighbors", self.get_stack_neighbors),
            ("stack_ports_stats", self.get_stack_ports_stats),
            ("stack_ports_status", self.get_stack_ports_status),
            ("stack_ports_interface", self.get_stack_ports_interface),
            ("stack_resource", self.get_stack_resource),
            ("dns", self.get_dns_config),
            ("ip_config", self.get_ip_config),
            ("udp_helper", self.get_udp_helper),
            ("static_arp", self.get_static_arp),
            ("static_rarp", self.get_static_rarp),
            ("ip_prefix_lists", self.get_ip_prefix_lists),
            ("ip_access_filters", self.get_ip_access_filters),
            ("ip_community_filters", self.get_ip_community_filters),
            ("qos_profiles", self.get_qos_profiles),
            ("qos_mapping", self.get_qos_mapping),
            ("boot_sequence", self.get_boot_sequence),
            ("tftp_config", self.get_tftp_config),
            ("web_preferences", self.get_web_preferences),
            ("system_log", self.get_system_log),
        ]
        result = {}
        for name, fn in methods:
            try:
                result[name] = fn()
            except Exception as e:
                result[name] = {"error": str(e)}
        return result


def main():
    import sys
    api = ICXWebClient()
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        data = api.get_all()
        summary = {k: (len(v) if isinstance(v, list) else "(dict)") for k, v in data.items()}
        print(json.dumps(summary, indent=2))
        return
    if len(sys.argv) > 1:
        name = sys.argv[1]
        fn_name = f"get_{name}"
        if hasattr(api, fn_name):
            result = getattr(api, fn_name)()
            print(json.dumps(result, indent=2))
        else:
            print(f"Available: --all, or one of:")
            for k in sorted([k.removeprefix("get_") for k in dir(api) if k.startswith("get_")]):
                print(f"  {k}")
        return
    print(f"=== ICX Web API Demo ===")
    s = api.get_system_info()
    print(f"System: {json.dumps(s, indent=2)}")
    for name, fn in [("port_stats", api.get_port_statistics), ("vlans", api.get_vlans),
                     ("arp", api.get_arp_table), ("mac", api.get_mac_table),
                     ("poe", api.get_poe_config)]:
        r = fn()
        print(f"{name}: {len(r)} records" + (f"  e.g. {r[0]}" if r else ""))


if __name__ == "__main__":
    main()
