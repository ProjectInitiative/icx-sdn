"""Deep crawler — recursively discovers every page and form in the Brocade ICX web UI.

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
from urllib.parse import urljoin, urlparse

BASE_URL = f"http://{os.environ.get('ICX_SWITCH_HOST', '172.16.1.15')}"
USER = os.environ.get("ICX_SWITCH_USER", "admin")
PASS = os.getenv("ICX_WEB_PASSWORD")
auth_header = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()


def fetch(url, data=None):
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
        t = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return {"ok": False, "status": e.code, "type": "", "text": t}
    except Exception as e:
        return {"ok": False, "status": 0, "type": "", "text": str(e)}


def is_html(r):
    return r["ok"] and ("html" in r["type"] or not r["type"])


def links_in(html, base):
    seen = set()
    for m in re.finditer(r'(?:href|src)=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1).split("?")[0].split("#")[0]
        if href and not href.startswith("telnet"):
            full = urljoin(base, href)
            seen.add(full)
    return sorted(seen)


def forms_in(html, page_url):
    forms = []
    for fm in re.finditer(r'<form[^>]*action="([^"]*)"[^>]*>(.*?)</form>', html, re.DOTALL | re.I):
        action = urljoin(page_url, fm.group(1)) if fm.group(1) else page_url
        body = fm.group(2)
        fields = []
        for inp in re.finditer(r'<input[^>]+name="([^"]*)"[^>]*>', body, re.I):
            m = inp.group(0)
            fields.append({
                "name": inp.group(1),
                "type": re.search(r'type="([^"]*)"', m, re.I).group(1) if re.search(r'type="([^"]*)"', m, re.I) else "text",
                "value": re.search(r'value="([^"]*)"', m, re.I).group(1) if re.search(r'value="([^"]*)"', m, re.I) else "",
            })
        for sel in re.finditer(r'<select[^>]+name="([^"]*)"[^>]*>(.*?)</select>', body, re.DOTALL | re.I):
            opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>', sel.group(2), re.I)
            sel_opts = re.findall(r'<option[^>]*selected[^>]*>([^<]+)', sel.group(2), re.I)
            fields.append({
                "name": sel.group(1),
                "type": "select",
                "options": opts,
                "default": opts[0] if opts else "",
            })
        forms.append({"action": action, "fields": fields, "page": page_url})
    return forms


def make_dummy_params(fields):
    params = {}
    for f in fields:
        if f["type"] == "submit":
            continue
        if f["name"]:
            v = f.get("default") or f.get("value") or ""
            params[f["name"]] = v
    if not params:
        params = {"Submit": "Display"}
    return params


catalog = {}
visited = set()
POST_ATTEMPTED = set()
CONTEXT = {}  # page -> parent nav section


def crawl(url, depth=0, parent="root"):
    if url in visited:
        return
    visited.add(url)

    r = fetch(url)
    if not is_html(r):
        return

    path = url.replace(BASE_URL, "")
    CONTEXT[path] = parent
    print(f"{'  ' * depth}{'[P]' if depth == 0 else '[L]'} {path} ({len(r['text'])}b)")

    catalog[path] = {
        "parent": parent,
        "status": r["status"],
        "size": len(r["text"]),
        "links": [],
        "forms": [],
        "post_results": [],
    }

    # Extract and probe forms
    forms = forms_in(r["text"], url)
    catalog[path]["forms"] = forms
    for form in forms:
        action = form["action"]
        action_path = action.replace(BASE_URL, "")
        if action_path in POST_ATTEMPTED:
            continue
        POST_ATTEMPTED.add(action_path)

        params = make_dummy_params(form["fields"])
        pr = fetch(action, params)
        catalog[path]["post_results"].append({
            "action": action_path,
            "params": params,
            "status": pr["status"] if pr["ok"] else pr["status"],
            "ok": pr["ok"],
            "size": len(pr["text"]),
            "has_tables": bool(re.findall(r"<table[^>]*>", pr.get("text", ""))),
        })
        tag = "DATA" if pr["ok"] and re.findall(r"<table[^>]*>", pr["text"]) else ("PAGE" if pr["ok"] else f"ERR{pr['status']}")
        print(f"{'  ' * (depth + 1)}  POST {action_path:40s} -> {tag} ({len(pr.get('text',''))}b) params={params}")

    # Follow links (only .htm pages, depth limited)
    if depth < 4:
        for link in links_in(r["text"], url):
            lp = urlparse(link)
            if not lp.path.endswith(".htm"):
                continue
            if lp.path in visited:
                continue
            # Skip images, binary, logout, telnet
            if any(x in lp.path for x in ["Logout", "telnet", "Images/", "logout"]):
                continue
            crawl(link, depth + 1, path)


def main():
    print("=== Deep Brocade ICX Web UI Crawl ===\n")

    # Start from nav menu
    crawl(urljoin(BASE_URL, "/index.htm"), 0, "nav")

    # Separately crawl Home if not visited
    if urljoin(BASE_URL, "/Home") not in visited:
        crawl(urljoin(BASE_URL, "/Home"), 0, "root")

    # Summary
    pages = [p for p in catalog if "Forms" not in p]
    post_endpoints = set()
    for p, entry in catalog.items():
        for pr in entry.get("post_results", []):
            post_endpoints.add((pr["action"], pr["status"], pr["ok"], pr["has_tables"]))

    data_eps = [e for e in post_endpoints if e[3]]
    page_eps = [e for e in post_endpoints if e[2] and not e[3]]
    fail_eps = [e for e in post_endpoints if not e[2]]

    print(f"\n=== Summary ===")
    print(f"Pages discovered: {len(pages)}")
    print(f"POST endpoints found: {len(post_endpoints)}")
    print(f"  DATA (returns tables): {len(data_eps)}")
    for e in sorted(data_eps):
        print(f"    {e[0]}")
    print(f"  PAGE (config forms): {len(page_eps)}")
    for e in sorted(page_eps):
        print(f"    {e[0]}")
    print(f"  FAILED: {len(fail_eps)}")

    # Print page tree
    print(f"\n=== Page Tree ===")
    by_parent = {}
    for p, entry in catalog.items():
        by_parent.setdefault(entry["parent"], []).append(p)
    
    def print_tree(parent, indent=0):
        for child in sorted(by_parent.get(parent, [])):
            forms = len(catalog[child].get("forms", []))
            posts = catalog[child].get("post_results", [])
            data_count = sum(1 for p in posts if p["has_tables"])
            print(f"{'  ' * indent}{child} ({forms} forms, {data_count} data endpoints)")
            print_tree(child, indent + 1)
    
    print_tree("nav")
    print_tree("root")

    with open("web_api_catalog_deep.json", "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"\nFull catalog saved to web_api_catalog_deep.json")


if __name__ == "__main__":
    main()
