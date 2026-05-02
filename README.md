# ICX Monitor

A Brocade ICX switch monitor with SSH scraping, SNMP live polling, and a web UI. Built for the ICX 6610 but works with any Brocade/Foundry switch that supports the same CLI commands.

## Project Structure

```
├── src/icx_monitor/          # Python package
│   ├── __init__.py           # Path resolution (ICX_MONITOR_ROOT env var)
│   ├── grab_info.py          # SSH into switch, dump config + stats
│   ├── parser.py             # Parse switch output → structured JSON
│   ├── ingest.py             # Orchestrate grab + parse
│   ├── live.py               # SNMP polling daemon (interface counters, temp)
│   └── server.py             # HTTP server + JSON API
├── static/                   # Web UI (dark theme, Ubiquiti-style)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/                     # Runtime data (gitignored)
│   ├── latest.json           # Parsed switch config/stats
│   ├── live.json             # Real-time SNMP data
│   └── snmp_community.txt    # SNMP read community string
├── flake.nix                 # Nix flake (uv2nix + pyproject-nix)
├── pyproject.toml            # Python project metadata
├── uv.lock                   # Locked Python dependencies
├── module.nix                # NixOS module
└── .github/workflows/
    └── build-and-push.yml    # CI: multi-arch container build
```

## Configuration

All switch-specific settings are configured via environment variables (no hardcoded defaults):

| Variable | Required | Description |
|---|---|---|
| `ICX_SWITCH_HOST` | Yes | Switch IP address or hostname |
| `ICX_SWITCH_USER` | Yes | SSH username |
| `ICX_SSH_KEY` | One of | Path to SSH private key |
| `ICX_SSH_PASSWORD` | One of | SSH password (use key auth for production) |
| `ICX_SERVER_PORT` | No | HTTP port (default: 8080) |
| `ICX_MONITOR_ROOT` | No | Data directory (default: autodetected) |

## Quick Start

### Nix (Development)

```bash
nix develop
cp .env.example .env        # fill in your switch details
direnv reload               # or re-cd into the directory
icx-ingest                  # grab + parse switch data
icx-server                  # start web UI at :8080
```

### Docker

```bash
docker run -d --name icx-monitor \
  -p 8080:8080 \
  -e ICX_SWITCH_HOST=192.168.1.1 \
  -e ICX_SWITCH_USER=admin \
  -e ICX_SSH_KEY=/data/ssh_key \
  -v /path/to/data:/data \
  ghcr.io/projectinitiative/icx-monitor:latest
```

### NixOS Module

```nix
{
  imports = [ icx-monitor.nixosModules.default ];
  services.icx-monitor = {
    enable = true;
    switch = {
      host = "192.168.1.1";
      username = "admin";
      sshKeyFile = "/run/secrets/ssh_key";
    };
    snmpCommunityFile = "/run/secrets/snmp_community";
  };
}
```

## CLI Commands

| Command | Description |
|---|---|
| `icx-server` | Start web UI |
| `icx-ingest` | Full pipeline: SSH → parse → JSON |
| `icx-grab` | SSH into switch, save raw log |
| `icx-parse` | Parse most recent log → data/latest.json |
| `icx-live` | One-shot SNMP poll |
| `icx-live --watch` | Continuous SNMP polling every 30s |

## SNMP Live Polling

1. On the switch: `snmp-server community public ro`
2. Create `data/snmp_community.txt` with the community string
3. Start `icx-server` — the poller auto-launches

## Building from Source

```bash
# Build the app
nix build .#default                  # result/bin/icx-server etc.

# Build the Docker container
nix build .#docker                   # result (Docker image tarball)
docker load < result                 # → icx-monitor:latest

# Build and load in one step
nix run .#build-docker               # builds + docker load
```

## CI/CD

Pushes to `main` trigger a multi-arch container build via GitHub Actions:
- Builds for `x86_64-linux` and `aarch64-linux`
- Creates a multi-arch manifest
- Pushes to `ghcr.io/projectinitiative/icx-monitor:latest`

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/data` | Parsed switch config + interface data |
| `GET /api/live` | Latest SNMP poll results |
| `GET /api/ingest` | Trigger re-ingestion from the switch |

## Architecture

### Data Flow

```
ICX 6610 Switch
  ├── SSH (netmiko/paramiko) ──→ grab_info.py ──→ raw log
  │                                                  │
  │                                                  ▼
  │                                             parser.py ──→ data/latest.json
  │                                                  │
  │                                                  ▼
  │                                             server.py ──→ GET /api/data
  │
  └── SNMP (net-snmp) ──→ live.py ──→ data/live.json ──→ GET /api/live
```

The SSH path provides deep config data (VLANs, LAGs, PoE config) from CLI commands.
The SNMP path provides lightweight real-time data (interface counters, link status, temperature) every 30 seconds.

### Web UI

The frontend is a single-page app that:
- Fetches config data once on load and renders the switch front-panel
- Polls `/api/live` every 15 seconds to update port status LEDs
- Shows port details on click (VLANs, stats, PoE, live counters)
- Dark theme with alternating odd/even port rows matching physical layout

### Port Layout (ICX 6610-48P)

```
Front Panel:            Back Panel:
┌──────────┬──────────┐ ┌──────────────────┐
│ SFP+ 10G │ PoE 1G  │ │ Stack 1/2/1      │
│ [1] [3]  │ Row 1   │ │ Breakout 1/2/2-5  │
│ [2] [4]  │ Row 2   │ │ Stack 1/2/6      │
│ [5] [7]  │         │ │ Breakout 1/2/7-10│
│ [6] [8]  │         │ └──────────────────┘
└──────────┴──────────┘
```
Supports 180-degree flip for vertical/wall-mount deployments.

### Nix Flake

The project uses `uv2nix` for Python dependency management:
- `pyproject.toml` declares project + dependencies
- `uv.lock` pins exact versions
- `flake.nix` builds a wrapped application with net-snmp bundled
- Dev shell (`nix develop`) provides all tools for development
- `ops-utils` integration for multi-arch container builds and registry pushes
