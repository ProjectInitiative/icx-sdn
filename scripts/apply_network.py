"""Declarative network state — apply network.yaml to Brocade ICX.

Reads the desired network state and reconciles the switch via web API
(for VLANs, port config) and SSH (for 802.1x, ACLs, routing, CLI-only).

Usage:
    export ICX_WEB_PASSWORD=...
    nix develop -c python scripts/apply_network.py network.yaml
"""

import os
import sys
import re
import json
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from icx_monitor.web_api import ICXWebClient


def expand_ports(spec):
    """Expand '1/1/44-48' into ['1/1/44', ..., '1/1/48']."""
    ports = []
    for token in spec.split(","):
        token = token.strip()
        if "-" in token and token.count("/") == 1:
            raise ValueError(f"Port range must be in form 1/1/X-Y, got: {token}")
        parts = token.split("/")
        if "-" in parts[-1]:
            start, end = parts[-1].split("-")
            prefix = "/".join(parts[:-1])
            for n in range(int(start), int(end) + 1):
                ports.append(f"{prefix}/{n}")
        else:
            ports.append(token)
    return ports


class BrocadeReconciler:
    def __init__(self, web: ICXWebClient):
        self.web = web
        self._changes = []

    def log(self, action, target, detail=""):
        self._changes.append({"action": action, "target": target, "detail": detail})
        print(f"  {action:10s} {target} {detail}")

    # ── VLANs ──────────────────────────────────────────────────

    def ensure_vlan(self, vid, name=None, tagged=None, untagged=None, ri=None):
        # Web API: VLAN config is CLI-only (no write endpoint discovered)
        # Fall back to SSH via netmiko
        cmds = [f"vlan {vid}"]
        if name:
            cmds.append(f" name {name}")
        for p in (tagged or []):
            cmds.append(f" tagged ethernet {p}")
        for p in (untagged or []):
            cmds.append(f" untagged ethernet {p}")
        if ri:
            cmds.append(f" router-interface ve {ri}")
        cmds.append("!")
        self._ssh_cmds.extend(cmds)
        self.log("VLAN", f"{vid} ({name})")

    # ── Port config ────────────────────────────────────────────

    def ensure_port(self, port, vlan=None, speed=None, poe=None, disabled=None):
        cmds = [f"interface ethernet {port}"]
        if vlan:
            cmds.append(f" dual-mode {vlan}")
        if speed:
            cmds.append(f" speed-duplex {speed}")
        if poe:
            cmds.append(" inline power")
        if poe is False:
            cmds.append(" no inline power")
        if disabled:
            cmds.append(" disable")
        if disabled is False:
            cmds.append(" enable")
        cmds.append("!")
        self._ssh_cmds.extend(cmd for cmd in cmds if cmd != "!")
        self._ssh_cmds.append("!")
        self.log("PORT", port, f"vlan={vlan} speed={speed} poe={poe}")

    # ── LAGs ───────────────────────────────────────────────────

    def ensure_lag(self, name, ports, primary=None):
        cmds = [f"lag {name} dynamic id {self._lag_ids.get(name, 1)}"]
        port_str = " ".join(f"ethernet {p}" for p in ports)
        cmds.append(f" ports {port_str}")
        if primary:
            cmds.append(f" primary-port {primary}")
        cmds.append(" deploy")
        cmds.append("!")
        self._ssh_cmds.extend(cmds)
        self.log("LAG", name, f"ports={ports}")

    # ── 802.1x ─────────────────────────────────────────────────

    def ensure_dot1x(self, config):
        cmds = ["aaa authentication dot1x default local", "dot1x-enable"]
        radius = config.get("radius", {})
        if radius.get("server"):
            cmds.append(f"radius-server host {radius['server']} key {radius.get('secret', '')}")
        default_vlan = config.get("default_vlan")
        if default_vlan:
            cmds.append(f"dot1x default-vlan-id {default_vlan}")
        for port_spec, rules in config.get("ports", {}).items():
            for p in expand_ports(port_spec):
                auth = rules.get("auth", "")
                cmds.append(f"interface ethernet {p}")
                if auth == "dot1x":
                    cmds.append(" dot1x-enable")
                fallback = rules.get("fallback_vlan")
                if fallback:
                    cmds.append(f" dot1x port-vlan {fallback}")
                cmds.append("!")
        self._ssh_cmds.extend(cmds)
        self.log("DOT1X", "enabled")

    # ── Apply ──────────────────────────────────────────────────

    def apply(self, state):
        self._ssh_cmds = []
        self._changes = []

        print("Reconciling network state...")

        for vid, vcfg in state.get("vlans", {}).items():
            self.ensure_vlan(
                vid, vcfg.get("name"),
                tagged=vcfg.get("tagged"),
                untagged=vcfg.get("untagged"),
            )

        for port_spec, pcfg in state.get("ports", {}).items():
            for port in expand_ports(port_spec):
                self.ensure_port(port, **pcfg)

        for name, lcfg in state.get("lags", {}).items():
            self.ensure_lag(name, lcfg.get("ports", []), lcfg.get("primary_port"))

        dot1x = state.get("dot1x", {})
        if dot1x.get("enabled"):
            self.ensure_dot1x(dot1x)

        # Apply via SSH
        if self._ssh_cmds:
            print(f"\n{len(self._ssh_cmds)} CLI commands to apply")
            self._apply_ssh(self._ssh_cmds)
        else:
            print("No changes needed.")

    def _apply_ssh(self, cmds):
        from netmiko import ConnectHandler
        device = {
            "device_type": "brocade_fastiron",
            "host": os.environ.get("ICX_SWITCH_HOST", "172.16.1.15"),
            "username": os.environ.get("ICX_SWITCH_USER", "admin"),
            "use_keys": True,
            "key_file": os.path.expanduser(os.environ.get("ICX_SSH_KEY", "~/.ssh/id_rsa_brocade")),
            "disabled_algorithms": {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]},
            "allow_agent": False,
        }
        with ConnectHandler(**device) as conn:
            if not conn.check_enable_mode():
                conn.enable()
            for cmd in cmds:
                # Only send non-empty, non-comment lines
                cmd = cmd.strip()
                if not cmd or cmd.startswith("#"):
                    continue
                print(f"  > {cmd}")
                conn.send_command(cmd, expect_string=r"#", delay_factor=1)
            conn.send_command("write memory", expect_string=r"#", delay_factor=2)
        print("Configuration applied and saved.")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <network.yaml>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    state = yaml.safe_load(path.read_text())
    web = ICXWebClient()
    reconciler = BrocadeReconciler(web)
    reconciler.apply(state)


if __name__ == "__main__":
    main()
