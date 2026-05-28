# LLM integration notes

Bambu Lab does not publish public API documentation. Most of `boocloud-bridge`
was written against `libbambu_networking.so` via FFI, observed behaviour, and
the open-source `BambuStudio` source. This page documents the quirks an LLM
(or an LLM-driven workflow) is most likely to be confused by when calling
boo-cloud through the MCP server.

## `start_print` — `result: "sent"` is success

The MCP `start_print` tool returns something like:

```json
{
  "result": "sent",
  "bridge_response": {
    "result": "sent",
    "return_code": -1,
    "print_result": -999,
    "device_id": "01P00A451601106",
    "file": "/tmp/job.gcode.3mf"
  },
  ...
}
```

Three fields look like errors but mean the opposite:

| Field | Value | Meaning |
|---|---|---|
| `result` | `"sent"` | The job was submitted to Bambu Cloud and accepted for queueing. **This is success.** |
| `return_code` | `-1` | Bridge sentinel meaning "submitted, awaiting printer ack." `0` is "acknowledged as started", anything else is an error. |
| `print_result` | `-999` | Default sentinel used before the printer's start-print callback fires. With `return_code: -1` it is **normal**, not an error. |

The bridge maps `return_code` to `result` via:

```rust
let status = match result.return_code {
    0  => "success",
    -1 => "sent",
    _  => "error",
};
```

So:

- `result: "success"` or `result: "sent"` → job submitted; call `get_status`
  to track progress.
- `result: "error"` → real failure; the numeric codes carry the printer's
  rejection reason. Common cases include `-3140` (encryption flag not ready;
  the bridge retries this internally up to 5 times) and various MQTT/network
  errors.

## `cancel` is intentionally disabled

`libbambu_networking.so` enforces a code-signing gate that rejects print-class
MQTT commands (stop/pause/resume) from unsigned host processes. boocloud
cannot send these, by design. See [`signed-app-gate.md`](signed-app-gate.md)
for the full investigation.

Practical consequence for LLMs: once `start_print` returns `"sent"` or
`"success"`, the job cannot be cancelled through this MCP. Stop the print
from the printer's touchscreen or the Bambu Handy app.

The bridge's own `/cancel` HTTP endpoint covers an unrelated case
(cancelling an in-flight upload before the printer accepts the job) and is
not exposed via the MCP.

## `get_status` latency

Each `get_status` call talks to Bambu Cloud over MQTT. Without the
persistent daemon, the bridge re-authenticates and reconnects MQTT for
every call — usually 30–90 seconds end-to-end. With the daemon running
(see `boocloud-bridge daemon`, or modern releases that auto-start it
inside `query_status`), repeat polls are sub-second.

If a status call appears to hang for tens of seconds, this is normal
cold-start behaviour, not a fault — wait for it.

## AMS slot indexing

`ams_slots` is **1-indexed** in every public input — the slot numbers the
printer's UI displays. The bridge converts to its internal 0-indexed
form before submitting; you may see `_ams_mapping: [3]` in a response
where you supplied `ams_slots: [4]`. That is intentional and not an
off-by-one error.

The filament colour in the sliced 3MF is informational. The printer
extrudes whatever filament is loaded in the slot you mapped to, regardless
of colour. To print the same model in a different colour, slice once and
map to a slot with that colour loaded.

## Per-printer credentials

`boocloud-bridge` authenticates against a single Bambu Cloud account at a
time. To switch accounts (e.g., between users), `boocloud login` writes a
new `credentials.toml`. The persistent daemon must be restarted after a
credential change — kill the process or POST `/shutdown`; the next call
will auto-start a fresh daemon with the new credentials.
