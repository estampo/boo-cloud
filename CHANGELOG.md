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
