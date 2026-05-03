# Design — ICX Monitor

## Why Nix?

The project uses Nix for reproducible builds, hermetic development environments, and declarative deployment. Every dependency — Python packages, SNMP tools, build utilities — is managed through `flake.nix`. No manual `apt install` or `pip install` required.

## Architecture

### Data Flow

```
ICX Switch
  ├── SSH (netmiko)  ──→ grab_info.py ──→ raw log ──→ parser.py ──→ data/latest.json
  └── SNMP (net-snmp) ──→ live.py ──→ data/live.json
                                              ↓
                                        server.py ──→ Web UI :8080
```

- **SSH path**: Deep config data (VLANs, LAGs, PoE) via CLI commands. Heavy — run on-demand.
- **SNMP path**: Lightweight real-time data (interface counters, link status, temperature). Polls every 30s.
- **Web UI**: Single-page app. Fetches config once, polls live data every 15s.

### Python Dependencies

Managed via `uv2nix` + `pyproject.nix`:
- `pyproject.toml` declares project and dependencies
- `uv.lock` pins exact versions
- `flake.nix` builds Python environment from lockfile
- No manual flake edits for Python dependency changes

### Nix Flake Structure

The flake builds everything once per system via `mkPkg` and shares results across all outputs:

- `packages.default` — Wrapped application with net-snmp in PATH
- `packages.docker` — OCI container image
- `devShells.default` — Development environment (inherits venv from package)
- `nixosModules.default` — NixOS module for systemd deployment
- `overlays.default` — nixpkgs overlay for `pkgs.icx-monitor`

### NixOS Module

The module deploys `icx-server` as a hardened systemd service with:
- `DynamicUser` for privilege separation
- `LoadCredential` for secrets (SSH key, SNMP community)
- `ProtectSystem` / `PrivateTmp` sandboxing
- Runtime data at `/var/lib/icx-monitor`

### SSH Key Handling

The ICX 6610 requires legacy SSH algorithms (`diffie-hellman-group1-sha1`, `ssh-rsa`). Netmiko connects with `disabled_algorithms` to force `ssh-rsa` pubkey auth. The SSH key path is configured via `ICX_SSH_KEY` environment variable (no hardcoded paths).

### SNMP Live Polling

Uses `snmpwalk`/`snmpget` via `nix-shell -p net-snmp` for reliable availability across environments. The poller runs as a background subprocess spawned by the server.

### Web UI

Single-page HTML/CSS/JS with:
- Dark theme (Ubiquiti-style)
- Front-panel port layout with alternating odd/even rows
- 180-degree flip for wall-mount switches
- Live port status updates from SNMP
- Per-port detail panel (VLANs, stats, PoE, flow control)

## Why These Patterns

| Decision | Rationale |
|---|---|
| `uv2nix` over `python3.withPackages` | Lockfile-pinned deps, deterministic builds |
| `mkPkg` shared builder | Single source of truth for pkg/venv/docker |
| `nix-shell` for snmp tools | Available on NixOS without system package install |
| `subprocess.Popen` for SNMP poller | Separate process avoids blocking the web server |
| `DynamicUser` in systemd service | No persistent system user needed |
