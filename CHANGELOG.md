## 0.2.2 — 2026-05-28

### Bugfixes

- ``_kill_local_daemon`` now records the daemon PID via ``_start_daemon`` writing a PID file (``$XDG_RUNTIME_DIR/boocloud-bridge.pid`` or a per-user temp file) and reads it back to target the exact process. This removes the hard dependency on ``pgrep``, which is missing from slim container images like ``python:3.12-slim``. The ``pgrep`` lookup is still used as a fallback when no PID file exists, so daemons started outside ``_start_daemon`` (e.g., the ``boocloud daemon`` CLI on a host with ``procps`` installed) are still covered. ([#daemon-pid-file](https://github.com/estampo/boo-cloud/pull/daemon-pid-file))


## 0.2.1 — 2026-05-28

### Bugfixes

- Decouple tag/release/bridge-binary publishing from the TestPyPI dry-run gate, so a misconfigured trusted-publishing setup no longer blocks the entire release pipeline. The dry-run gate now only blocks `publish-pypi`, where it protects the PyPI channel as intended. ([#decouple-release-from-testpypi](https://github.com/estampo/boo-cloud/pull/decouple-release-from-testpypi))
- ``query_status`` now routes through the persistent HTTP daemon (auto-starting it if necessary), bringing repeat status polls down from ~30s+ to milliseconds. ``_ensure_daemon`` pings on every call: a healthy daemon replies in milliseconds, so a slow or failing ping means the daemon is wedged or absent and we shut it down (cooperatively via ``POST /shutdown``, falling back to ``SIGTERM``/``SIGKILL`` on the PID via ``pgrep`` if the HTTP handler is stuck) and start a fresh one before using it. ([#query-status-via-daemon](https://github.com/estampo/boo-cloud/pull/query-status-via-daemon))

### Misc

- Add ``docs/llm-integration.md`` documenting boo-cloud quirks an LLM (or LLM-driven workflow) is most likely to be confused by — notably that ``start_print`` returns ``result: "sent"`` with ``return_code: -1`` and ``print_result: -999`` as the **success** path, not an error. Also expand the ``start_print`` MCP docstring with the same explanation so it surfaces in the tool description. ([#llm-integration-docs](https://github.com/estampo/boo-cloud/pull/llm-integration-docs))


## 0.2.0 — 2026-05-28

### Features

- Add `boocloud-mcp` MCP server (stdio) exposing `list_printers`, `get_status`, `get_print_info`, `validate_3mf`, and `start_print` tools so an LLM (Claude Desktop, Claude Code, etc.) can query printer state and submit print jobs. `start_print` requires explicit `ams_slots` when an AMS is loaded and gates submission behind `confirm=true`. Install via `pip install 'boo-cloud[mcp]'`.
- Initial release: `boocloud` CLI for Bambu Lab cloud printing, extracted from bambox to isolate legal risk from the proprietary `libbambu_networking.so` dependency.

### Misc

- Add README, cloud protocol docs, bridge architecture docs, and ADR-002 from bambox.
- Fix `prepare-release` workflow to stage fragment deletions with `git add -A changes/`; previously the commit step failed once all fragments were consumed.
- Replace `git add changes/` with `git commit -am` in the prepare-release workflow — towncrier removes the `changes/` directory itself once all fragments are consumed, so any pathspec on it errors out.


# Changelog

All notable changes to boo-cloud are documented here.
This changelog is managed by [towncrier](https://towncrier.readthedocs.io/).
