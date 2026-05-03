# Agent Working Guide — ICX Monitor

## Environment

```bash
nix develop             # Enter dev shell (direnv handles this automatically)
export ICX_SWITCH_HOST=<switch-ip>
export ICX_SWITCH_USER=<username>
export ICX_SSH_KEY=/path/to/key
icx-ingest              # Grab + parse switch data
icx-server              # Start web UI at :8080
```

## Available Commands

| Command | Description |
|---|---|
| `icx-server` | Start web UI |
| `icx-ingest` | Full pipeline: SSH → parse → JSON |
| `icx-grab` | SSH into switch, save raw log |
| `icx-parse` | Parse most recent log |
| `icx-live` | One-shot SNMP poll |
| `icx-live --watch` | Continuous SNMP polling |
| `nix flake check` | Run formatting checks and tests |
| `nix build` | Hermetic sandbox build |

## Mandatory Pre-Submission

```bash
nix flake check          # formatting + tests
nix build                # hermetic build
```

Both must pass before committing or requesting review.

## Project Structure

```
├── src/icx_monitor/      # Python package (SSH scraping, SNMP, server)
├── static/               # Web UI (dark theme Ubiquiti-style)
├── data/                 # Runtime data (gitignored)
├── flake.nix             # Nix flake (uv2nix + pyproject-nix)
├── module.nix            # NixOS module for systemd service
├── pyproject.toml        # Python project metadata + deps
└── uv.lock               # Locked Python dependencies
```

## Adding a Dependency

```bash
nix develop --command uv add <package>
nix develop --command uv lock
```

The flake reads from `uv.lock` automatically — no manual flake edits for Python deps.
