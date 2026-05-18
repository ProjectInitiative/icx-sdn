"""Systematic catalog of every Brocade ICX web UI form endpoint.

Probes every page, extracts all forms and fields, submits each with dummy
values, and records what returns data vs what's a write action.

Usage:
    nix develop -c python scripts/catalog_web_api.py
"""

import os
import re
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
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
        resp = urllib.request.urlopen(req, data=body, timeout=10)
        return {
            "ok": True,
            "status": resp.status,
            "type": resp.headers.get("Content-Type", ""),
            "text": resp.read().decode("utf-8", errors="replace"),
        }
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return {"ok": False, "status": e.code, "type": "", "text": text}
    except Exception as e:
        return {"ok": False, "status": 0, "type": "", "text": str(e)}


SITEMAP = [
    "rsystem.htm", "device.htm", "memory.htm", "flash.htm",
    "show.htm", "showARP.htm", "showmac.htm", "shlog.htm",
    "shrmonethstat.htm", "shrmonethErrstat.htm", "shrmonethhist.htm",
    "portstat.htm", "portcfg.htm", "portattb.htm", "portutil.htm",
    "vlan.htm", "vShLag.htm",
    "stpstat.htm", "stpcfg.htm", "rstpstat.htm", "rstpcfg.htm",
    "stackPOEcfg.htm",
    "stackdetails.htm", "stackmodules.htm", "stackneig.htm",
    "stackperip.htm", "stackperitr.htm",
    "stackportsstatus.htm", "stackportsstats.htm", "stackportsinterface.htm",
    "stackresource.htm",
    "bootsq.htm", "prefer.htm",
    "ip.htm", "rip.htm",
    "sst.htm", "qospf.htm",
    "clear.htm", "tcfg.htm", "timg.htm", "tracert.htm",
    "picture.htm",
]

KNOWN_POST_ROUTES = [
    "/Forms/PortStatStackUnit",
    "/Forms/PortCfgStackUnit",
    "/Forms/StackVlan",
    "/Forms/StackPortsStats",
    "/Forms/StackPOECfg",
    "/Forms/STPStatStackUnit",
    "/Forms/STPCfgStackUnit",
    "/Forms/rsystem",
    "/Forms/StatClear",
    "/Forms/clear",
    "/Forms/bootsq",
    "/Forms/ip",
    "/Forms/prefer",
    "/Forms/qos_profile",
    "/Forms/resetconf",
    "/Forms/tcfg",
    "/Forms/timg",
    "/Forms/tracert",
    "/Forms/static",
    "/Forms/CfgStackGeneral",
    "/Forms/CfgStackModule",
    "/Forms/CfgStackPorts",
    "/Forms/CfgStackUnitPri",
]


def extract_form_fields(html, page_url):
    forms = []
    for fm in re.finditer(
        r'<form[^>]*action="([^"]*)"[^>]*>(.*?)</form>', html, re.DOTALL | re.I
    ):
        action = fm.group(1) or page_url
        body = fm.group(2)

        fields = []
        for inp in re.finditer(
            r'<input[^>]+name="([^"]*)"[^>]*>', body, re.I
        ):
            name = inp.group(1)
            i_type = (re.search(r'type="([^"]*)"', inp.group(0), re.I) or "").group(1) if re.search(r'type="([^"]*)"', inp.group(0), re.I) else "text"
            value = (re.search(r'value="([^"]*)"', inp.group(0), re.I) or "").group(1) if re.search(r'value="([^"]*)"', inp.group(0), re.I) else ""
            fields.append({"name": name, "type": i_type, "value": value})

        for sel in re.finditer(
            r'<select[^>]+name="([^"]*)"[^>]*>(.*?)</select>', body, re.DOTALL | re.I
        ):
            name = sel.group(1)
            options = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>', sel.group(2), re.I)
            selected = re.findall(r'<option[^>]*selected[^>]*>([^<]+)', sel.group(2), re.I)
            fields.append({
                "name": name,
                "type": "select",
                "options": options,
                "default": options[0] if options else "",
            })

        forms.append({"action": action, "fields": fields})
    return forms


def probe_form_action(action, fields, page_url):
    # Build test params with dummy values
    params = {}
    for f in fields:
        v = f.get("default") or f.get("value") or ""
        if f["type"] == "submit":
            continue
        if f["name"]:
            params[f["name"]] = v
    if not params:
        params = {"Submit": "Display"}

    r = fetch(action, params)
    has_tables = bool(re.findall(r"<table[^>]*>", r["text"])) if r["ok"] else False
    return {
        "action": action,
        "params": params,
        "status": r["status"],
        "ok": r["ok"],
        "size": len(r["text"]),
        "has_tables": has_tables,
        "is_html": "html" in r["type"] or not r["type"],
    }


def main():
    catalog = {}

    # Phase 1: GET all pages, extract forms and links
    print("=== Phase 1: Crawling pages ===\n")
    for page in SITEMAP:
        r = fetch(page)
        if not r["ok"]:
            continue
        forms = extract_form_fields(r["text"], page)
        catalog[page] = {
            "method": "GET",
            "status": r["status"],
            "size": len(r["text"]),
            "forms": forms,
        }

    # Phase 2: Probe each form action
    print("=== Phase 2: Probing form actions ===\n")
    probed = set()
    for page, entry in sorted(catalog.items()):
        for form in entry.get("forms", []):
            action = form["action"]
            action_key = action.split("?")[0]
            if action_key in probed:
                continue
            probed.add(action_key)

            result = probe_form_action(action, form["fields"], page)
            if result["ok"]:
                tag = "DATA" if result["has_tables"] else "PAGE"
            else:
                tag = f"ERR{result['status']}"
            print(f"  {tag:6s} {result['status']:3d} {action_key:35s} params={result['params']}")
            catalog[f"POST:{action_key}"] = result

    # Phase 3: Try raw POST to known routes with minimal params
    print("\n=== Phase 3: Testing raw POST routes ===\n")
    formless_params = [{"common_stack_id": "1", "Submit": "Display"}, {"Submit": "Display"}]
    for route in KNOWN_POST_ROUTES:
        if route in probed:
            continue
        probed.add(route)
        for params in formless_params:
            r = fetch(route, params)
            if r["ok"]:
                has_tables = bool(re.findall(r"<table[^>]*>", r["text"]))
                tag = "DATA" if has_tables else "PAGE"
                print(f"  {tag:6s} {r['status']:3d} {route:35s} params={params}")
                catalog[f"POST:{route}"] = {
                    "action": route, "params": params, "status": r["status"],
                    "ok": True, "size": len(r["text"]), "has_tables": has_tables,
                }
                break
            else:
                if params == formless_params[-1]:
                    print(f"  FAIL    {r['status']:3d} {route:35s} (no params worked)")

    # Summary
    print(f"\n=== Catalog Summary ===\n")
    data_endpoints = [(k, v) for k, v in catalog.items()
                      if v.get("has_tables") and v.get("ok")]
    action_endpoints = [(k, v) for k, v in catalog.items()
                        if "POST:" in k and v.get("ok") and not v.get("has_tables")]
    failed = [(k, v) for k, v in catalog.items()
              if not v.get("ok")]

    print(f"Data endpoints (return HTML tables): {len(data_endpoints)}")
    for k, v in sorted(data_endpoints):
        sz = v.get("size", 0)
        params = v.get("params", {})
        print(f"  GET  {k:35s} ({sz} bytes, params={params})")

    print(f"\nAction endpoints (POST, no data table): {len(action_endpoints)}")
    for k, v in sorted(action_endpoints):
        print(f"  POST {k:35s} status={v['status']}")

    print(f"\nFailed endpoints: {len(failed)}")
    for k, v in sorted(failed):
        print(f"  {k:35s} status={v['status']}")

    with open("web_api_catalog.json", "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"\nFull catalog saved to web_api_catalog.json")


if __name__ == "__main__":
    main()
