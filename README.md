# boo-cloud

> **Legal notice:** boo-cloud depends on `libbambu_networking.so`, a
> proprietary shared library distributed by Bambu Lab inside BambuStudio and
> Bambu Connect. Redistribution of this library may carry legal risk — review
> your jurisdiction's laws before distributing. This is why cloud printing is
> kept in a separate project from [bambox](https://github.com/estampo/bambox).

Cloud printing for Bambu Lab printers via the `boocloud-bridge` daemon.

## What boo-cloud does

boo-cloud provides the `boocloud` CLI and the `boocloud-bridge` Rust daemon
that wraps `libbambu_networking.so` via FFI. Together they handle:

- Bambu Cloud authentication and credential storage
- Cloud print job submission (`.gcode.3mf` → printer)
- Real-time printer status and AMS tray queries
- A persistent background daemon for fast status polling (no 20s wait per call)

Cloud printing lives here; `.gcode.3mf` archive construction lives in
[bambox](https://github.com/estampo/bambox).

## Installation

```bash
pip install boo-cloud
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install boo-cloud
```

### Bridge Setup

All commands that talk to a printer require `boocloud-bridge`. Two options:

**Option A — Native binary (Linux x86_64 only):**

```bash
curl -fsSL https://github.com/estampo/boo-cloud/releases/latest/download/install.sh | sh
```

Installs `boocloud-bridge` to `~/.local/bin`. macOS, Windows, and Linux ARM64
users must use Option B.

**Option B — Docker (all other platforms):**

If Docker is installed and running, boo-cloud uses `estampo/boocloud-bridge`
automatically. No extra setup needed. This is the **only** supported option
on macOS, Windows, and Linux ARM64.

### Known limitations

- **`boocloud cancel` is disabled.** `libbambu_networking` rejects print-class
  MQTT commands (stop, pause, resume) from unsigned hosts. See
  [`docs/signed-app-gate.md`](docs/signed-app-gate.md) for the full
  investigation. The daemon `/cancel` endpoint handles in-flight upload
  cancellation and is unaffected.
- **macOS always uses Docker.** The native binary hits the same signing gate
  and cannot send `start_print` from an unsigned process.
- **No LAN mode.** Cloud connectivity required. LAN-direct printing is a
  future goal.
- **No Windows native bridge.** Windows and Linux ARM64 require Docker.

| Feature | Linux x86_64 | Linux ARM64 | macOS | Windows |
|---------|-------------|-------------|-------|---------|
| `login`, `status` (native) | Yes | No | No | No |
| `print`, `daemon` (native) | Yes | No | No | No |
| All commands (Docker) | Yes | Yes¹ | Yes | Yes |

¹ Via QEMU emulation (amd64 image on ARM64 host).

## CLI

```
boocloud [-V] [-v] {login,print,cancel,status,daemon}
```

### `boocloud login` — Configure credentials

Authenticate with Bambu Cloud and save printer credentials.

```bash
boocloud login
```

Credentials are stored in `~/.config/boo-cloud/credentials.toml`. The env var
`BOO_CLOUD_CREDENTIALS` overrides the default path; `BAMBOX_CREDENTIALS` and
`ESTAMPO_CREDENTIALS` are supported as legacy fallbacks.

### `boocloud print` — Send to printer

Send a `.gcode.3mf` to a Bambu printer via cloud.

```bash
# Print by device serial
boocloud print output.gcode.3mf -d DEVICE_SERIAL

# Print by named printer from credentials
boocloud print output.gcode.3mf -p my_printer

# Dry run — show AMS mapping without sending
boocloud print output.gcode.3mf -d DEVICE_SERIAL -n

# Manual AMS tray assignment
boocloud print output.gcode.3mf -d DEVICE_SERIAL \
  --ams-tray 2:PETG-CF:2850E0
```

Options:

| Flag | Description |
|------|-------------|
| `-d, --device` | Printer serial number |
| `-p, --printer` | Named printer from `credentials.toml` |
| `-c, --credentials` | Path to `credentials.toml` |
| `--project` | Project name shown in Bambu Cloud |
| `--timeout` | Upload timeout in seconds |
| `--no-ams-mapping` | Skip AMS filament mapping |
| `--ams-tray` | Manual tray spec: `SLOT:TYPE:COLOR` (repeatable) |
| `-n, --dry-run` | Show print info without sending |
| `-y, --yes` | Skip confirmation prompt |

To enable the first-layer bed-type mismatch warning, record the plate
installed on each printer in `credentials.toml`:

```toml
[printers.my_printer]
serial = "00M201234567890"
plate_type = "Textured PEI Plate"
```

### `boocloud status` — Query printer

Query printer status and AMS tray info.

```bash
# One-shot status
boocloud status DEVICE_SERIAL

# By named printer
boocloud status -p my_printer

# Live watch mode
boocloud status DEVICE_SERIAL -w -i 5
```

Options:

| Flag | Description |
|------|-------------|
| `-p, --printer` | Named printer from `credentials.toml` |
| `-c, --credentials` | Path to `credentials.toml` |
| `-w, --watch` | Continuously refresh status display |
| `-i, --interval` | Seconds between refreshes (default: 10) |

### `boocloud cancel` — Cancel print

Currently disabled due to the signed-app gate. See
[`docs/signed-app-gate.md`](docs/signed-app-gate.md).

### `boocloud daemon` — Manage bridge daemon

Start, stop, and check the background `boocloud-bridge` daemon.

```bash
boocloud daemon status
boocloud daemon start
boocloud daemon start -f   # foreground (blocking)
boocloud daemon stop
boocloud daemon restart
```

Options (for `start`):

| Flag | Description |
|------|-------------|
| `-c, --credentials` | Path to `credentials.toml` |
| `-f, --foreground` | Run in foreground (blocking) |

### Global options

| Flag | Description |
|------|-------------|
| `-V, --version` | Show installed version |
| `-v, --verbose` | Enable debug logging |

## MCP server

`boocloud-mcp` exposes status queries and print submission as MCP tools, so
an LLM (Claude Desktop, Claude Code, etc.) can drive the printer. Install
with the `mcp` extra:

```bash
pip install 'boo-cloud[mcp]'
```

Tools:

| Tool | Purpose |
|------|---------|
| `list_printers` | Names + masked serials from `credentials.toml` |
| `get_status` | Live state, temps, progress, ETA, **AMS trays** |
| `get_print_info` | Filaments, time, weight, layers, bed_type from a `.gcode.3mf` |
| `validate_3mf` | Safety validation via `bambox.validate` |
| `start_print` | Validate + AMS mapping + cloud submit (gated by `confirm`) |

Safety: `start_print` runs bambox validation first (refuses on errors).
If the printer has loaded AMS trays it **requires** explicit
`ams_slots=[...]` (1-indexed, one per filament in the 3MF) — there is no
heuristic auto-mapping in the MCP path. `confirm=true` is required to
actually submit; otherwise the tool returns the planned mapping for review.

Claude Code / Claude Desktop config snippet:

```json
{
  "mcpServers": {
    "boo-cloud": {
      "command": "boocloud-mcp"
    }
  }
}
```

Run `boocloud login` first so the server can read `credentials.toml`.

## Python API

```python
from boocloud.bridge import cloud_print, query_status
from boocloud.credentials import load_credentials
```

## Modules

| Module | Purpose |
|--------|---------|
| `cli` | Typer commands — delegates to bridge/credentials |
| `bridge` | HTTP client for `boocloud-bridge` daemon, cloud print dispatch |
| `credentials` | Credential loading and storage (`~/.config/boo-cloud/credentials.toml`) |
| `auth` | Bambu Cloud authentication |

## Bridge architecture

`boocloud-bridge` is a Rust binary that wraps `libbambu_networking.so` via a
thin C++ shim (`shim/shim.cpp`). It exposes an HTTP API on `localhost:8765`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Daemon health + MQTT connection state |
| GET | `/status/{device_id}` | Cached printer status |
| GET | `/ams/{device_id}` | AMS tray info |
| POST | `/print` | Upload 3MF + start print job |
| POST | `/cancel/{device_id}` | In-flight upload cancellation |
| WS | `/watch/{device_id}` | Live status stream |

The daemon keeps a persistent MQTT connection so status queries return cached
data instantly rather than paying a ~20s connect-subscribe cost per call.

See [`docs/bridge-migration-plan.md`](docs/bridge-migration-plan.md) for the
full bridge design and [`docs/decisions/002-rust-bridge-replaces-cpp.md`](docs/decisions/002-rust-bridge-replaces-cpp.md)
for the decision record.

## Technical docs

| Document | Contents |
|----------|---------|
| [`docs/cloud-print-research.md`](docs/cloud-print-research.md) | Bambu Cloud REST API, MQTT protocol, X.509 signing |
| [`docs/signed-app-gate.md`](docs/signed-app-gate.md) | Why `cancel` is disabled; libbambu signing gate |
| [`docs/sdk-send-message-investigation.md`](docs/sdk-send-message-investigation.md) | send_message -2 investigation |
| [`docs/bridge-migration-plan.md`](docs/bridge-migration-plan.md) | Bridge architecture and phases |
| [`docs/daemon-bridge-design.md`](docs/daemon-bridge-design.md) | Original daemon design (superseded by bridge-migration-plan) |
| [`docs/decisions/002-rust-bridge-replaces-cpp.md`](docs/decisions/002-rust-bridge-replaces-cpp.md) | ADR: Rust bridge replaces C++ bridge |

## Development

```bash
uv sync --extra dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src/boocloud
uv run pytest
```

## License

MIT
