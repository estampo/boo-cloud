# ADR-002: Rust `boocloud-bridge` Replaces C++ `estampo/cloud-bridge`

**Status:** Accepted (migration complete — cloud printing extracted to boo-cloud)
**Date:** 2026-04-11
**Updated:** 2026-04-24 (renamed from `bambox-bridge` to `boocloud-bridge` on extraction to boo-cloud)
**Supersedes:** `docs/daemon-bridge-design.md` (Python-HTTP-over-C++ design)

## Context

Cloud printing communicates with Bambu Lab printers through a "bridge" — a
binary that loads `libbambu_networking.so`, authenticates with Bambu Cloud,
subscribes to the printer's MQTT topics, and handles status queries and
print dispatch.

Two bridge implementations existed, and the project migrated between them:

1. **Legacy C++ bridge.** Distributed as the Docker image
   `estampo/cloud-bridge:bambu-02.05.00.00`. Built from
   `scripts/bambu_cloud_bridge.cpp` in the estampo repository.
2. **Rust `boocloud-bridge`.** Source in `bridge/` in this repository. Calls
   `libbambu_networking.so` via a thin C++ shim (`shim/shim.cpp`) and FFI.
   Implements `status`, `watch`, `print`, `cancel`, and `daemon` subcommands.
   Credentials are passed via the global `-c/--credentials` flag.

This ADR was originally written when the cloud code lived in `bambox` and the
binary was called `bambox-bridge`. On extraction to `boo-cloud` (April 2026),
the binary was renamed to `boocloud-bridge` and the Docker image to
`estampo/boocloud-bridge`.

## Decision

**The Rust `boocloud-bridge` is the only bridge. The C++ bridge is
deprecated and removed.**

Concretely:

1. `boocloud-bridge` (Rust, in `bridge/`) is the single source of truth for
   Bambu cloud communication. All new bridge functionality lands in the Rust crate.
2. The Python client (`src/boocloud/bridge.py`) targets the Rust
   `estampo/boocloud-bridge` Docker image built from `bridge/Dockerfile`.
3. Python talks to the bridge over the HTTP daemon API (`axum`, port 8765)
   rather than stdout JSON — this eliminates bind-mount gymnastics and the
   20-second MQTT-per-call cost.
4. Credentials are passed via the global `-c/--credentials` flag or the
   `BOO_CLOUD_CREDENTIALS` env var.

The full implementation plan — FFI shim design, HTTP API endpoints, Docker
packaging — lives in `docs/bridge-migration-plan.md`.

## Consequences

### Benefits

- **One bridge, one CLI contract.** Eliminates the class of bug that triggered
  this ADR: Python can no longer silently route to a bridge with an
  incompatible CLI.
- **No bind-mount gymnastics.** Uploading 3MFs over HTTP removes baked-Docker-
  image fallback and all sandbox workarounds.
- **Persistent MQTT connection.** Status queries return cached state instantly
  instead of paying a ~20s MQTT connect+subscribe cost per call.
- **Memory-safe bridge.** The C++ binary replaced by Rust behind a narrow FFI
  surface.

### Costs

- **Rust toolchain in CI.** The bridge build requires this — the Dockerfile
  uses `rust:1.88-bookworm`. Contributors touching the bridge need Rust locally.
- **FFI fragility.** The `.so` exports C++ types; the shim wraps each function
  in `extern "C"`. ABI drift in a future Bambu Studio release could break the
  shim.

### Non-goals

- This ADR does not cover LAN printing or Moonraker.

## Alternatives considered

### Keep the C++ bridge, wrap it in a Python HTTP daemon

Original design in `docs/daemon-bridge-design.md`. Rejected because it leaves
two languages in the critical path and doesn't address bind-mount issues.

### Rewrite Python `bridge.py` to match the C++ CLI more faithfully

Rejected. The C++ binary depends on estampo's build system, is hard to
distribute, and carries the bind-mount failure modes that motivated the
migration.

### Keep both bridges long-term, select by config

Rejected. Two CLI contracts, two test matrices, two release pipelines. The bug
that triggered this ADR is exactly what happens when two bridges coexist.

## References

- `docs/bridge-migration-plan.md` — full 4-phase implementation plan
- `bridge/src/main.rs` — Rust bridge entry point
- `bridge/Dockerfile` — Rust bridge Docker image (`estampo/boocloud-bridge`)
- `src/boocloud/bridge.py` — Python client
