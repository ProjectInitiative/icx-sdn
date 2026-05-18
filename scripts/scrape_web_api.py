"""Brocade ICX web UI data scraper.

Replaces SSH scraping with HTTP form-based data extraction.
Faster, no SSH overhead, no key exchange issues.

Usage:
    nix develop -c python scripts/scrape_web_api.py
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

BASE_URL = f"http://{os.environ.get('ICX_SWITCH_HOST', '172.16.1.15')}"
USER = os.environ.get("ICX_SWITCH_USER", "admin")
PASS = os.getenv("ICX_WEB_PASSWORD")
auth_header = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()


def fetch(path, data=None):
    url = urljoin(BASE_URL, path)
    req = urllib.request.Request(url)
    req.add_header("Authorization", auth_header)
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        body = urllib.parse.urlencode(data).encode()
    else:
        body = None
    try:
        resp = urllib.request.urlopen(req, data=body, timeout=15)
        return {
            "ok": True,
            "status": resp.status,
            "text": resp.read().decode("utf-8", errors="replace"),
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "text": ""}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row = []
        self.current_cell = ""
        self.skip_tags = {"script", "style", "a", "img", "form", "input", "select", "option", "button"}

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.current_row = []
            self.in_row = True
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.current_cell = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr":
            if self.in_row and self.current_row:
                self.tables[-1].append(self.current_row)
            self.in_row = False
        elif tag in ("td", "th") and self.in_cell:
            self.current_row.append(self.current_cell.strip())
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell and not self.in_table:
            pass
        if self.in_cell:
            self.current_cell += data

    def error(self, message):
        pass


def parse_tables(html):
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.I)
    p = TableParser()
    p.feed(cleaned)
    return [t for t in p.tables if t]


# Pages that return data directly via GET
GET_PAGES = {
    "/rsystem.htm": "system",
    "/memory.htm": "memory",
    "/flash.htm": "flash",
    "/stackdetails.htm": "stack_details",
    "/stackmodules.htm": "stack_modules",
    "/stackneig.htm": "stack_neighbors",
    "/stackresource.htm": "stack_resource",
}

# POST endpoints with their parameters
POST_ENDPOINTS = [
    ("port_statistics", "/Forms/PortStatStackUnit", {"common_stack_id": "1"}),
    ("port_config", "/Forms/PortCfgStackUnit", {"common_stack_id": "1"}),
    ("stack_ports_stats", "/Forms/StackPortsStats", {"common_stack_id": "1"}),
    ("vlan", "/Forms/StackVlan", {"stackvlan_per_page": "20"}),
    ("stp_status", "/Forms/STPStatStackUnit", {"common_stack_id": "1"}),
    ("stp_config", "/Forms/STPCfgStackUnit", {"common_stack_id": "1"}),
    ("poe", "/Forms/StackPOECfg", {"common_stack_id": "1"}),
]

# Sub-pages accessed via POST after stack unit selection
SUB_PAGES = [
    ("arp", "/showARP.htm", {"common_stack_id": "1"}),
    ("mac", "/showmac.htm", {"common_stack_id": "1"}),
    ("log", "/shlog.htm", {"common_stack_id": "1"}),
    ("device", "/device.htm", {"common_stack_id": "1"}),
    ("rmon_stats", "/shrmonethstat.htm", {}),
    ("port_attributes", "/portattb.htm", {"common_stack_id": "1"}),
    ("port_utilization", "/portutil.htm", {"common_stack_id": "1"}),
]


def main():
    print(f"=== Brocade ICX Web Scraper ===\n")

    result = {}

    print("GET data pages...")
    for path, name in GET_PAGES.items():
        r = fetch(path)
        if r["ok"]:
            tables = parse_tables(r["text"])
            result[name] = {"path": path, "tables": tables}
            print(f"  {name:20s} ({len(tables)} tables)")

    print("\nPOST data endpoints...")
    for name, path, params in POST_ENDPOINTS:
        r = fetch(path, params)
        if r["ok"]:
            tables = parse_tables(r["text"])
            result[name] = {"path": path, "params": params, "tables": tables}
            nz = sum(1 for t in tables if len(t) > 1)
            print(f"  {name:20s} ({len(tables)} tables, {nz} with data)")
        else:
            print(f"  {name:20s} (HTTP {r['status']})")

    print("\nPOST data sub-pages...")
    for name, path, params in SUB_PAGES:
        r = fetch(path, params)
        if r["ok"]:
            tables = parse_tables(r["text"])
            result[name] = {"path": path, "tables": tables}
            nz = sum(1 for t in tables if len(t) > 1)
            print(f"  {name:20s} ({len(tables)} tables, {nz} with data)")
        else:
            print(f"  {name:20s} (HTTP {r['status']})")

    summary = {}
    for k, v in result.items():
        tcount = len(v["tables"])
        rowcount = sum(len(t) for t in v["tables"])
        cols = [len(r) for t in v["tables"] for r in (t[:1] if t else [])]
        summary[k] = {
            "path": v["path"],
            "tables": tcount,
            "rows": rowcount,
            "sample_cols": cols[:5],
        }

    print(f"\n=== Summary ===")
    for k, v in sorted(summary.items()):
        print(f"  {k:20s}  {v['tables']:2d} tables, {v['rows']:4d} rows")

    with open("web_data.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved to web_data.json")


if __name__ == "__main__":
    main()
