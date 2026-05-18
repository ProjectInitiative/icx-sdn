# Agent Working Guide — ICX Monitor

## Environments

### Dev shell (switch monitoring)
```bash
nix develop                    # Enter dev shell
export ICX_SWITCH_HOST=<switch-ip>
export ICX_SWITCH_USER=<username>
export ICX_SSH_KEY=/path/to/key
icx-ingest                     # Grab + parse switch data
icx-server                     # Start web UI at :8080
```

### Agent shell (document ingestion + TextFSM generation)
```bash
nix develop .#agent            # Enter agent shell (venv with torch + marker-pdf)
python scripts/ingest_docs.py  # Convert PDF → Markdown → RAG chunks
```

The agent shell uses pyproject.nix to declaratively manage its dependencies
(optional group `[project.optional-dependencies.agent]` in pyproject.toml).
Packages like PyTorch, Marker-PDF, netmiko, and textfsm are resolved via
uv2nix from `uv.lock` — no pip in shellHook. It also clones `ntc-templates`
into `vendor/` for TextFSM template reference.

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
| `nix develop .#agent` | Agent shell with docs ingestion pipeline |

## Mandatory Pre-Submission

```bash
nix flake check          # formatting + tests
nix build                # hermetic build
```

Both must pass before committing or requesting review.

## Project Structure

```
├── src/icx_monitor/         # Python package (SSH scraping, SNMP, server)
│   ├── textfsm_parser.py    # TextFSM-based parser (clone of parser.py)
│   └── templates/           # .textfsm template files
├── scripts/
│   └── ingest_docs.py       # PDF → Markdown → RAG chunk pipeline
├── docs/
│   ├── raw_pdfs/            # Drop Brocade/switch PDFs here
│   ├── markdown_out/        # Marker-pdf output (gitignored)
│   └── agent_chunks/        # Header-sliced RAG chunks (gitignored)
├── vendor/
│   ├── textfsm/             # Google TextFSM library (cloned for reference)
│   └── ntc-templates/       # Network To Code TextFSM templates (cloned for ref)
├── static/                  # Web UI (dark theme Ubiquiti-style)
├── data/                    # Runtime data (gitignored)
├── flake.nix                # Nix flake (uv2nix + pyproject-nix)
├── module.nix               # NixOS module for systemd service
├── pyproject.toml           # Python project metadata + deps
└── uv.lock                  # Locked Python dependencies
```

## Document Ingestion Pipeline

Used for feeding switch documentation (PDFs) to an LLM for automated TextFSM template
generation.

1. Place PDF in `docs/raw_pdfs/`
2. `nix develop .#agent` (auto-creates venv, installs torch + marker-pdf)
3. `python scripts/ingest_docs.py <filename.pdf>`
4. Chunks land in `docs/agent_chunks/` as JSON objects keyed by header title

## Adding a Dependency

For the **main package** (switch monitor):
```bash
nix develop --command uv add <package>
nix develop --command uv lock
```

For the **agent shell** (document pipeline):
```bash
nix develop .#agent
pip install <package>
```

The flake reads from `uv.lock` automatically — no manual flake edits for main Python deps.
