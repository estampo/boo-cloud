"""MCP server exposing boo-cloud as tools an LLM can call.

Provides five tools over stdio:

- ``list_printers``      configured printers from credentials.toml
- ``get_status``         printer state, temps, progress, AMS trays
- ``get_print_info``     filament/time/weight/layers/bed_type from a .gcode.3mf
- ``validate_3mf``       safety validation via ``bambox.validate``
- ``start_print``        validate + AMS mapping + cloud submit (gated by confirm)

Safety properties enforced here, not in the bridge:

1. ``start_print`` runs ``bambox.validate.validate_3mf`` first.  Any error
   finding refuses the print.
2. If the printer has at least one loaded AMS tray and the caller did not
   pass ``ams_slots`` (and did not set ``skip_ams=True``), the tool returns
   a structured error containing the 3MF's filaments and the printer's
   loaded trays — the LLM must construct the mapping itself rather than
   relying on heuristic auto-mapping.
3. ``confirm`` must be ``True`` to actually submit.  Without it, the tool
   returns the planned mapping for review.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from bambox import extract_print_info
from bambox.validate import validate_3mf as _bambox_validate
from mcp.server.fastmcp import FastMCP

from boocloud import credentials as _creds
from boocloud.bridge import (
    _write_token_json,
    cloud_print,
    load_credentials,
    parse_ams_trays,
    query_status,
)

mcp = FastMCP("boo-cloud")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_device_id(printer: str | None, device: str | None) -> tuple[str, str | None]:
    """Resolve (device_id, printer_name) from either a serial or a name.

    Raises ValueError on ambiguity or unknown name.
    """
    if device and printer:
        raise ValueError("Pass either 'printer' or 'device', not both.")
    if device:
        return device, None
    if printer:
        entry = _creds.list_printers().get(printer)
        if entry is None:
            available = list(_creds.list_printers().keys())
            raise ValueError(f"Unknown printer '{printer}'. Available: {available}")
        serial = entry.get("serial", "")
        if not serial:
            raise ValueError(f"Printer '{printer}' has no serial number in credentials.")
        return serial, printer
    # Fall back to first configured printer
    printers = _creds.list_printers()
    for name, entry in printers.items():
        if entry.get("serial"):
            return entry["serial"], name
    raise ValueError(
        "No printer specified and none configured. "
        "Pass 'printer' or 'device', or run 'boocloud login'."
    )


def _query_ams_trays(device_id: str) -> list[dict[str, Any]]:
    """Return loaded AMS trays for ``device_id`` (empty list = no AMS / none loaded)."""
    creds = load_credentials()
    token_file = _write_token_json(creds)
    try:
        status = query_status(device_id, token_file)
        return parse_ams_trays(status)
    finally:
        try:
            token_file.unlink()
        except OSError:
            pass


def _validate_ams_slots(
    ams_slots: list[int],
    filament_count: int,
    trays: list[dict[str, Any]],
) -> str | None:
    """Return an error message if ams_slots is invalid for the given filaments/trays, else None."""
    if len(ams_slots) != filament_count:
        return (
            f"ams_slots has {len(ams_slots)} entries but 3MF declares "
            f"{filament_count} filament(s). Provide one slot per filament, 1-indexed."
        )
    loaded_slots = {t["phys_slot"] + 1 for t in trays}
    for s in ams_slots:
        if s < 1:
            return f"ams_slots must be 1-indexed; got {s}."
        if s not in loaded_slots:
            return (
                f"ams_slots[{ams_slots.index(s)}] = {s} is not currently loaded. "
                f"Loaded slots: {sorted(loaded_slots)}."
            )
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_printers() -> dict[str, Any]:
    """List printers configured in credentials.toml.

    Returns one entry per printer with its name, masked serial, and optional
    plate_type. Use the ``name`` field as the ``printer`` argument to other
    tools.
    """
    printers = _creds.list_printers()
    items = []
    for name, entry in printers.items():
        serial = entry.get("serial", "")
        items.append(
            {
                "name": name,
                "serial_masked": _creds.mask_serial(serial) if serial else "",
                "plate_type": entry.get("plate_type"),
                "type": entry.get("type", "bambu-cloud"),
            }
        )
    return {"printers": items}


@mcp.tool()
def get_status(
    printer: str | None = None,
    device: str | None = None,
) -> dict[str, Any]:
    """Query live status for a printer.

    Returns gcode_state, task name, temperatures, progress, ETA, and the
    loaded AMS trays. The AMS trays section is what you must consult to
    construct ``ams_slots`` for ``start_print`` on AMS-equipped printers.

    Pass either ``printer`` (name from list_printers) or ``device`` (serial).
    If neither is set, the first configured printer is used.
    """
    device_id, name = _resolve_device_id(printer, device)
    creds = load_credentials()
    token_file = _write_token_json(creds)
    try:
        status = query_status(device_id, token_file)
    finally:
        try:
            token_file.unlink()
        except OSError:
            pass

    trays = parse_ams_trays(status)
    return {
        "printer": name,
        "device_id": device_id,
        "gcode_state": status.get("gcode_state", ""),
        "task": status.get("subtask_name", ""),
        "nozzle_temper": status.get("nozzle_temper", 0),
        "nozzle_target_temper": status.get("nozzle_target_temper", 0),
        "bed_temper": status.get("bed_temper", 0),
        "bed_target_temper": status.get("bed_target_temper", 0),
        "mc_percent": status.get("mc_percent"),
        "mc_remaining_time": status.get("mc_remaining_time"),
        "ams": {
            "loaded": bool(trays),
            "active_slot": int(status.get("ams", {}).get("tray_now", 255)),
            "trays": [
                {
                    "slot": t["phys_slot"] + 1,
                    "ams_id": t["ams_id"],
                    "type": t["type"],
                    "color": t["color"],
                    "tray_info_idx": t["tray_info_idx"],
                }
                for t in trays
            ],
        },
    }


@mcp.tool()
def get_print_info(file_path: str) -> dict[str, Any]:
    """Read metadata from a .gcode.3mf file.

    Returns time, weight, layers, bed_type, printer_model_id, and the list
    of filaments (with type, color, tray_info_idx, used_m, used_g). Use the
    filaments list to plan ``ams_slots`` for ``start_print``.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"3MF not found: {file_path}")
    info = extract_print_info(path)
    return info.to_dict()


@mcp.tool()
def validate_3mf(file_path: str) -> dict[str, Any]:
    """Run bambox safety validation on a .gcode.3mf file.

    Returns ``{valid, errors, warnings}``. ``start_print`` calls this
    internally and refuses to submit on errors; you can call it separately
    to inspect findings without committing to print.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"3MF not found: {file_path}")
    return _bambox_validate(path).to_dict()


@mcp.tool()
def start_print(
    file_path: str,
    printer: str | None = None,
    device: str | None = None,
    ams_slots: list[int] | None = None,
    skip_ams: bool = False,
    project_name: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Submit a .gcode.3mf to a Bambu printer via cloud.

    Workflow:
    1. Validates the 3MF via bambox. Errors abort with ``{result: "invalid"}``.
    2. Resolves the printer and queries live AMS state.
    3. If the AMS has loaded trays and ``ams_slots`` is None and
       ``skip_ams`` is False, returns ``{result: "needs_ams_slots"}`` with
       the filaments and loaded trays. Re-call with ``ams_slots`` set to
       a 1-indexed slot per filament (length must equal filament count).
    4. With ``confirm=False`` (default), returns the planned mapping for
       review without submitting.
    5. With ``confirm=True``, submits the print job via the bridge.

    ``skip_ams=True`` forces a print without AMS mapping (e.g., single-color
    external spool on an AMS-equipped printer).

    On a successful submit the response looks like::

        {
          "result": "sent",
          "bridge_response": {
            "result": "sent",
            "return_code": -1,
            "print_result": -999,
            ...
          },
          ...
        }

    The numeric fields are reported by Bambu's proprietary networking
    library and have no published API documentation. The values above
    look like errors but mean the opposite — ``result: "sent"`` is the
    authoritative success signal. Specifically:

    - ``return_code: -1`` is the bridge's internal sentinel for "queued
      and submitted to Bambu Cloud" — distinct from ``0`` ("acknowledged
      as started") and any other value ("error"). Treat ``"sent"`` and
      ``"success"`` as both successful submits.
    - ``print_result: -999`` is the default sentinel value used before
      the printer's start-print callback fires. With ``return_code: -1``
      it is normal and expected — the printer hasn't acknowledged yet.

    Once submitted, call ``get_status`` to watch ``gcode_state``,
    ``mc_percent``, and ``task`` reflect the new job. ``cancel`` is
    disabled upstream by Bambu's signing gate, so a submitted print must
    be stopped from the printer's screen or the Bambu app.
    """
    path = Path(file_path)
    if not path.exists():
        return {"result": "error", "message": f"3MF not found: {file_path}"}

    validation = _bambox_validate(path).to_dict()
    if not validation["valid"]:
        return {
            "result": "invalid",
            "message": "3MF failed validation; refusing to print.",
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }

    try:
        device_id, name = _resolve_device_id(printer, device)
    except ValueError as e:
        return {"result": "error", "message": str(e)}

    info = extract_print_info(path)
    filaments_payload = [asdict(f) for f in info.filaments]

    trays: list[dict[str, Any]] = []
    if not skip_ams:
        try:
            trays = _query_ams_trays(device_id)
        except Exception as e:
            return {
                "result": "error",
                "message": f"Could not query AMS state: {e}",
            }

    ams_loaded = bool(trays)

    if ams_loaded and ams_slots is None:
        return {
            "result": "needs_ams_slots",
            "message": (
                "Printer has an AMS with loaded trays. Provide 'ams_slots' as a "
                "list of 1-indexed slot numbers, one per filament in the 3MF, "
                "matching each filament to a loaded tray. Or pass skip_ams=True "
                "to print without AMS mapping."
            ),
            "filaments": filaments_payload,
            "loaded_trays": [
                {
                    "slot": t["phys_slot"] + 1,
                    "type": t["type"],
                    "color": t["color"],
                    "tray_info_idx": t["tray_info_idx"],
                }
                for t in trays
            ],
            "validation_warnings": validation["warnings"],
        }

    if ams_slots is not None:
        err = _validate_ams_slots(ams_slots, len(info.filaments), trays)
        if err:
            return {
                "result": "error",
                "message": err,
                "filaments": filaments_payload,
                "loaded_trays": [
                    {
                        "slot": t["phys_slot"] + 1,
                        "type": t["type"],
                        "color": t["color"],
                        "tray_info_idx": t["tray_info_idx"],
                    }
                    for t in trays
                ],
            }

    planned = {
        "printer": name,
        "device_id": device_id,
        "file": str(path),
        "filaments": filaments_payload,
        "ams_slots": ams_slots,
        "skip_ams": skip_ams,
        "validation_warnings": validation["warnings"],
    }

    if not confirm:
        return {
            "result": "plan",
            "message": "Re-call with confirm=true to submit.",
            "plan": planned,
        }

    creds = load_credentials()
    mapping_override = [s - 1 for s in ams_slots] if ams_slots is not None else None

    try:
        bridge_resp = cloud_print(
            path,
            device_id,
            credentials=creds,
            project_name=project_name or path.stem,
            skip_ams_mapping=skip_ams,
            ams_mapping_override=mapping_override,
        )
    except Exception as e:
        return {"result": "error", "message": f"Bridge error: {e}"}

    return {
        "result": bridge_resp.get("result", "unknown"),
        "bridge_response": bridge_resp,
        "plan": planned,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
