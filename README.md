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
└── uv.lock                   # Locked Python dependencies
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

## Usage

### Development

```bash
nix develop
export ICX_SWITCH_HOST=192.168.1.1
export ICX_SWITCH_USER=admin
export ICX_SSH_KEY=/path/to/id_rsa
icx-ingest             # Grab + parse switch data
icx-server             # Start web UI at :8080
```

### CLI Commands

| Command | Description |
|---|---|
| `icx-server` | Start web UI |
| `icx-ingest` | Full pipeline: SSH → parse → JSON |
| `icx-grab` | SSH into switch, save raw log |
| `icx-parse` | Parse most recent log → data/latest.json |
| `icx-live` | One-shot SNMP poll |
| `icx-live --watch` | Continuous SNMP polling every 30s |

### SNMP Live Polling

1. On the switch: `snmp-server community public ro`
2. Create `data/snmp_community.txt` with the community string
3. Start `icx-server` — the poller auto-launches

### Production (NixOS Module)

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

### Docker

```bash
nix build .#docker
docker load < result
docker run -v /path/to/data:/data -e ICX_SWITCH_HOST=192.168.1.1 ... icx-monitor
```

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
┌──────────────────┐    ┌──────────────────┐
│ SFP+ 1/3/1-8     │    │ Stack 1/2/1      │
├──────────────────┤    │ Breakout 1/2/2-5  │
│ Row 1: 1,3,5..47 │    │ Stack 1/2/6      │
│ Row 2: 2,4,6..48 │    │ Breakout 1/2/7-10│
└──────────────────┘    └──────────────────┘
```

### Nix Flake

The project uses `uv2nix` for Python dependency management:
- `pyproject.toml` declares project + dependencies
- `uv.lock` pins exact versions
- `flake.nix` builds a wrapped application with net-snmp bundled
- Dev shell (`nix develop`) provides all tools for development
