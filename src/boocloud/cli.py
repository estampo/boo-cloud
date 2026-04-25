"""CLI entry point for boocloud."""

import json
import logging
import sys
import time
from collections.abc import Callable
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated, Optional

import click
import typer

from boocloud import ui

log = logging.getLogger(__name__)

app = typer.Typer(
    name="boocloud",
    help="Send G-code packages to Bambu Lab printers via cloud",
    no_args_is_help=True,
    add_completion=True,
)

_verbose: bool = False


def _version_callback(value: bool) -> None:
    if value:
        ui.console.print(f"boo-cloud {pkg_version('boo-cloud')}")
        raise typer.Exit()


@app.callback()
def _callback(
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
    version: Annotated[
        bool,
        typer.Option(
            "-V",
            "--version",
            help="Show version and exit",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    global _verbose  # noqa: PLW0603
    _verbose = verbose
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")


_WARNING = (
    "[yellow]WARNING:[/yellow] boo-cloud is experimental. "
    "Incorrect configuration may damage your printer. Use at your own risk."
)


def _warn_experimental() -> None:
    ui.err_console.print(f"  {_WARNING}")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _format_progress_bar(percent: int, width: int = 24) -> str:
    """Render a simple progress bar string from a percentage (0-100)."""
    percent = max(0, min(100, percent))
    filled = round(width * percent / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {percent}%"


_PRINT_STAGES: dict[str, str] = {
    "0": "printing",
    "1": "auto bed leveling",
    "2": "heatbed preheating",
    "3": "sweeping XY mech mode",
    "4": "changing filament",
    "5": "M400 pause",
    "6": "filament runout pause",
    "7": "heating hotend",
    "8": "calibrating extrusion",
    "9": "scanning bed surface",
    "10": "inspecting first layer",
    "11": "identifying build plate type",
    "12": "calibrating micro lidar",
    "13": "homing toolhead",
    "14": "cleaning nozzle tip",
    "17": "calibrating extrusion flow",
    "18": "vibration compensation",
    "19": "motor noise calibration",
}


def _format_status(
    status: dict,
    ams_trays: list[dict] | None = None,
    use_color: bool = True,
) -> str:
    """Format printer status dict into a human-readable string."""
    lines: list[str] = []

    state = status.get("gcode_state", "?")
    if use_color:
        lines.append(f"  State:    {ui.format_state(state)}")
    else:
        lines.append(f"  State:    {state}")

    task_name = status.get("subtask_name", "")
    if task_name:
        lines.append(f"  Task:     {task_name}")

    if state not in ("IDLE", "FINISH", "FAILED", "", "?"):
        layer = status.get("layer_num", 0)
        stage_id = str(status.get("mc_print_stage", ""))
        if layer and int(layer) > 0:
            stage = "printing"
        else:
            stage = _PRINT_STAGES.get(stage_id, "")
        if stage:
            lines.append(f"  Stage:    {stage}")

    nozzle = status.get("nozzle_temper", 0)
    nozzle_target = status.get("nozzle_target_temper", 0)
    bed = status.get("bed_temper", 0)
    bed_target = status.get("bed_target_temper", 0)
    try:
        nozzle_str = f"{float(nozzle):.0f}°C"
        if nozzle_target and float(nozzle_target) > 0:
            nozzle_str += f" → {float(nozzle_target):.0f}°C"
    except (ValueError, TypeError):
        nozzle_str = f"{nozzle}°C"
    try:
        bed_str = f"{float(bed):.0f}°C"
        if bed_target and float(bed_target) > 0:
            bed_str += f" → {float(bed_target):.0f}°C"
    except (ValueError, TypeError):
        bed_str = f"{bed}°C"
    lines.append(f"  Nozzle:   {nozzle_str}")
    lines.append(f"  Bed:      {bed_str}")

    mc_percent = status.get("mc_percent")
    if mc_percent:
        bar = _format_progress_bar(int(mc_percent))
        bar = bar.replace("[", "\\[")
        remaining = status.get("mc_remaining_time", "?")
        if remaining != "?" and remaining is not None:
            try:
                mins = int(remaining)
                hrs, m = divmod(mins, 60)
                eta_str = f"{hrs}h {m:02d}m" if hrs else f"{m}m"
            except (ValueError, TypeError):
                eta_str = f"{remaining}min"
        else:
            eta_str = "?"
        lines.append(f"  Progress: {bar}  ETA {eta_str}")

    if ams_trays:
        tray_now = int(status.get("ams", {}).get("tray_now", 255))
        lines.append("  AMS:")
        for t in ams_trays:
            slot_num = t["phys_slot"] + 1
            active = " <-- printing" if t["phys_slot"] == tray_now else ""
            color_hex = t["color"]
            if use_color:
                swatch = ui.color_swatch(color_hex)
            else:
                swatch = "  "
            lines.append(f"    slot {slot_num}  {t['type']:<12}  {swatch} #{color_hex}{active}")

    return "\n".join(lines)


def _extract_sliced_bed_type(threemf: Path) -> str | None:
    """Return the ``curr_bed_type`` recorded in the 3MF, or ``None`` if unreadable."""
    import zipfile

    try:
        with zipfile.ZipFile(threemf, "r") as z:
            raw = z.read("Metadata/project_settings.config")
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    value = data.get("curr_bed_type")
    return value if isinstance(value, str) and value else None


def _warn_if_bed_type_mismatch(threemf: Path, configured_plate: str | None) -> None:
    """Warn when the sliced bed type differs from the configured physical plate."""
    if not configured_plate:
        return
    sliced = _extract_sliced_bed_type(threemf)
    if not sliced:
        return
    if sliced.casefold() == configured_plate.casefold():
        return
    ui.warn(
        f"This G-code was sliced for '{sliced}' but your printer is configured "
        f"with '{configured_plate}'. Printing as-is risks a nozzle crash on the "
        f"first layer. Re-slice with the correct bed type, or confirm the correct "
        f"plate is installed."
    )


def _get_printer_plate_type(printer_name: str | None, creds_path: Path | None) -> str | None:
    """Return the ``plate_type`` field of the resolved printer, or ``None``."""
    import tomllib

    if creds_path:
        path = creds_path
    else:
        from boocloud.credentials import _credentials_path

        path = _credentials_path()
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    printers = raw.get("printers", {})
    if printer_name:
        entry = printers.get(printer_name)
    else:
        entry = next((p for p in printers.values() if p.get("serial")), None)
    if not entry:
        return None
    plate = entry.get("plate_type")
    return plate if isinstance(plate, str) and plate else None


def _show_print_info(threemf: Path) -> None:
    """Display print metadata (time, weight, layers, filaments) from a 3MF."""
    import re
    import xml.etree.ElementTree as ET
    import zipfile

    from boocloud.bridge import _xml_ns

    try:
        with zipfile.ZipFile(threemf, "r") as z:
            prediction = 0
            weight = 0.0
            filaments: list[tuple[str, str, str, str]] = []

            if "Metadata/slice_info.config" in z.namelist():
                root = ET.fromstring(z.read("Metadata/slice_info.config"))
                ns = _xml_ns(root)
                plate_el = root.find(f"{ns}plate")
                if plate_el is not None:
                    meta = {}
                    for md in plate_el.findall(f"{ns}metadata"):
                        meta[md.get("key", "")] = md.get("value", "")
                    try:
                        prediction = int(meta.get("prediction", "0"))
                    except ValueError:
                        pass
                    try:
                        weight = float(meta.get("weight", "0"))
                    except ValueError:
                        pass
                    for f in plate_el.findall(f"{ns}filament"):
                        filaments.append(
                            (
                                f.get("type", "?"),
                                f.get("color", "?"),
                                f.get("used_m", "0"),
                                f.get("used_g", "0"),
                            )
                        )

            layers = 0
            gcode_name = None
            for name in z.namelist():
                if name.startswith("Metadata/plate_") and name.endswith(".gcode"):
                    gcode_name = name
                    break
            if gcode_name:
                gcode_head = z.read(gcode_name)[:4096].decode(errors="replace")
                m = re.search(r"; total layer number:\s*(\d+)", gcode_head)
                if m:
                    layers = int(m.group(1))
                else:
                    m = re.search(r";LAYER_COUNT:(\d+)", gcode_head)
                    if m:
                        layers = int(m.group(1))

    except (zipfile.BadZipFile, ET.ParseError, KeyError) as e:
        ui.warn(f"could not read 3MF metadata: {e}")
        return

    if prediction > 0:
        hrs, remainder = divmod(prediction, 3600)
        mins = remainder // 60
        time_str = f"{hrs}h{mins}m" if hrs else f"{mins}m"
    else:
        time_str = "unknown"

    ui.console.print()
    ui.console.print(f"Print: {threemf.name}")
    if layers:
        ui.info(f"Layers:    {layers}")
    ui.info(f"Time:      {time_str}")
    ui.info(f"Weight:    {weight:.1f}g")
    if filaments:
        ui.info("Filaments:")
        for ftype, color, used_m, used_g in filaments:
            ui.info(f"  - {ftype} {color}  ({used_m}m / {used_g}g)")
    ui.console.print()


def _show_ams_mapping(threemf: Path, ams_trays: list[dict], mapping: list[int]) -> None:
    """Display the AMS filament mapping that will be used for the print."""
    import xml.etree.ElementTree as ET
    import zipfile

    from boocloud.bridge import _xml_ns

    if not any(v >= 0 for v in mapping):
        return

    filaments: dict[int, tuple[str, str]] = {}
    try:
        with zipfile.ZipFile(threemf, "r") as z:
            if "Metadata/slice_info.config" in z.namelist():
                root = ET.fromstring(z.read("Metadata/slice_info.config"))
                ns = _xml_ns(root)
                plate_el = root.find(f"{ns}plate")
                if plate_el is not None:
                    for f in plate_el.findall(f"{ns}filament"):
                        fid = int(f.get("id", "1"))
                        filaments[fid] = (f.get("type", "?"), f.get("color", "?"))
    except (OSError, KeyError, ET.ParseError):
        log.debug("Failed to parse slice_info for AMS display", exc_info=True)

    tray_by_phys = {t["phys_slot"]: t for t in ams_trays}

    ui.console.print("AMS filament mapping:")
    for idx, phys_slot in enumerate(mapping):
        filament_id = idx + 1
        if phys_slot < 0:
            continue
        fil_type, fil_color = filaments.get(filament_id, ("?", "?"))
        tray = tray_by_phys.get(phys_slot, {})
        tray_type = tray.get("type", "?")
        tray_color = tray.get("color", "?")
        ui.info(
            f"Slot {filament_id} ({fil_type} {fil_color}) "
            f"-> AMS tray {phys_slot} ({tray_type} #{tray_color})"
        )
    ui.console.print()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _resolve_printer(printer_name: str | None, creds_path: Path | None) -> tuple[str, str]:
    """Resolve a printer name to (serial, display_name)."""
    import tomllib

    if creds_path:
        path = creds_path
    else:
        from boocloud.credentials import _credentials_path

        path = _credentials_path()

    if printer_name:
        if not path.exists():
            ui.error(f"credentials file not found: {path}")
            sys.exit(1)
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        printers = raw.get("printers", {})
        if printer_name not in printers:
            ui.error(f"printer '{printer_name}' not found")
            sys.exit(1)
        serial = printers[printer_name].get("serial", "")
        if not serial:
            ui.error(f"printer '{printer_name}' has no serial number")
            sys.exit(1)
        return serial, printer_name

    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        for name, p in raw.get("printers", {}).items():
            if p.get("serial"):
                return p["serial"], name

    ui.error("no printer configured. Run 'boocloud login' or use --device.")
    sys.exit(1)


def _name_printers(token: str) -> None:
    """List bound printers and let user name up to 5."""
    from boocloud.auth import _get_devices
    from boocloud.credentials import mask_serial, save_printer

    try:
        devices = _get_devices(token)
    except (OSError, KeyError):
        ui.warn("Could not fetch printer list.")
        return

    if not devices:
        ui.info("No printers found on this account.")
        return

    ui.console.print(f"\n  Found {len(devices)} printer(s):")
    for i, d in enumerate(devices, 1):
        name = d.get("name", "unnamed")
        model = d.get("dev_product_name", d.get("dev_model_name", "?"))
        serial = d.get("dev_id", "?")
        online = "online" if d.get("online") else "offline"
        ui.console.print(f"  {i}. {name} ({model}) — {mask_serial(serial)} \\[{online}]")

    limit = min(len(devices), 5)
    ui.console.print(f"\n  Name your printer(s) (up to {limit}). Press Enter to skip.")
    for i in range(limit):
        d = devices[i]
        dev_name = d.get("name", f"printer-{i + 1}")
        serial = d.get("dev_id", "")
        default = dev_name.lower().replace(" ", "-")
        raw = ui.prompt_str(f"Name for #{i + 1} [{default}] (enter '-' to skip)")
        if raw == "-":
            continue
        name = raw or default
        save_printer(name, {"type": "bambu-cloud", "serial": serial})
        ui.success(f"Saved '{name}' ({mask_serial(serial)})")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def login() -> None:
    """Log in to Bambu Cloud and configure printers."""
    _warn_experimental()
    import os

    from boocloud.auth import _get_user_profile, _login
    from boocloud.credentials import load_cloud_credentials, save_cloud_credentials

    cloud = load_cloud_credentials()
    if cloud and cloud.get("token"):
        try:
            profile = _get_user_profile(cloud["token"])
            ui.success(f"Already logged in as {profile.get('name') or profile['uid']}")
            if not ui.prompt_yn("Re-login?", default=False):
                _name_printers(cloud["token"])
                return
        except (OSError, KeyError):
            ui.warn("Cached token is invalid or expired.")

    email = os.environ.get("BAMBU_EMAIL") or ui.prompt_str("Email")
    password = os.environ.get("BAMBU_PASSWORD") or ui.prompt_password("Password")
    if not email or not password:
        ui.error("email and password required")
        sys.exit(1)

    token, refresh_token = _login(email, password)
    profile = _get_user_profile(token)

    save_cloud_credentials(
        token=token,
        refresh_token=refresh_token,
        email=email,
        uid=profile["uid"],
    )

    ui.success(f"Login successful! User: {profile.get('name') or profile['uid']}")
    _name_printers(token)


@app.command(name="print")
def print_cmd(
    threemf: Annotated[Path, typer.Argument(help="Input .gcode.3mf file")],
    device: Annotated[str, typer.Option("-d", "--device", help="Printer serial number")] = "",
    printer: Annotated[
        Optional[str], typer.Option("-p", "--printer", help="Named printer from credentials.toml")
    ] = None,
    credentials: Annotated[
        Optional[Path], typer.Option("-c", "--credentials", help="Path to credentials.toml")
    ] = None,
    project: Annotated[
        Optional[str], typer.Option("--project", help="Project name shown in cloud")
    ] = None,
    timeout: Annotated[int, typer.Option("--timeout", help="Timeout in seconds")] = 180,
    no_ams_mapping: Annotated[
        bool, typer.Option("--no-ams-mapping", help="Skip AMS mapping")
    ] = False,
    ams_tray: Annotated[
        Optional[list[str]],
        typer.Option(
            "--ams-tray", metavar="SLOT:TYPE:COLOR", help="Manually specify AMS tray. Repeatable."
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("-n", "--dry-run", help="Show print info without sending")
    ] = False,
    yes: Annotated[
        bool, typer.Option("-y", "--yes", help="Skip AMS mapping confirmation prompt")
    ] = False,
) -> None:
    """Send a .gcode.3mf to a Bambu printer via cloud bridge."""
    _warn_experimental()
    from boocloud.bridge import cloud_print, load_credentials

    if not threemf.exists():
        ui.error(f"{threemf} not found")
        sys.exit(1)

    creds_path = credentials
    try:
        creds = load_credentials(creds_path)
    except (FileNotFoundError, ValueError) as e:
        ui.error(str(e))
        sys.exit(1)

    device_id = device
    if not device_id:
        device_id, _ = _resolve_printer(printer, creds_path)

    project_name = project or threemf.stem

    ams_trays: list[dict] = []
    for spec in ams_tray or []:
        parts = spec.split(":")
        if len(parts) != 3:
            ui.error(f"--ams-tray must be SLOT:TYPE:COLOR, got '{spec}'")
            sys.exit(1)
        slot_str, ftype, color = parts
        phys_slot = int(slot_str)
        ams_trays.append(
            {
                "phys_slot": phys_slot,
                "ams_id": phys_slot // 4,
                "slot_id": phys_slot % 4,
                "type": ftype,
                "color": color.upper(),
                "tray_info_idx": "",
            }
        )

    _show_print_info(threemf)

    configured_plate = _get_printer_plate_type(printer, credentials)
    _warn_if_bed_type_mismatch(threemf, configured_plate)

    if dry_run:
        if not no_ams_mapping:
            from boocloud.bridge import (
                _build_ams_mapping,
                _write_token_json,
                parse_ams_trays,
                query_status,
            )

            token_file = _write_token_json(creds, directory=threemf.parent)
            try:
                if not ams_trays:
                    try:
                        live_status = query_status(device_id, token_file, verbose=_verbose)
                        ams_trays = parse_ams_trays(live_status)
                    except Exception as e:
                        ui.warn(f"could not query AMS state: {e}")
                if ams_trays:
                    try:
                        ams_data = _build_ams_mapping(threemf, ams_trays)
                        mapping = ams_data["amsMapping"]
                        _show_ams_mapping(threemf, ams_trays, mapping)
                    except RuntimeError as e:
                        ui.warn(f"AMS mapping error: {e}")
            finally:
                try:
                    token_file.unlink()
                except OSError:
                    pass
        ui.info("Dry run — not sending to printer.")
        return

    explicit_trays = bool(ams_trays)
    if not no_ams_mapping and not yes and not explicit_trays:
        from boocloud.bridge import (
            _build_ams_mapping,
            _write_token_json,
            parse_ams_trays,
            query_status,
        )

        token_file = _write_token_json(creds, directory=threemf.parent)
        try:
            try:
                live_status = query_status(device_id, token_file, verbose=_verbose)
                ams_trays = parse_ams_trays(live_status)
            except Exception as e:
                ui.warn(f"could not query AMS state: {e}")
            if ams_trays:
                try:
                    ams_data = _build_ams_mapping(threemf, ams_trays)
                    mapping = ams_data["amsMapping"]
                    _show_ams_mapping(threemf, ams_trays, mapping)
                    if not typer.confirm("Proceed with this mapping?", default=True):
                        ui.info("Aborted.")
                        return
                except RuntimeError as e:
                    ui.error(str(e))
                    sys.exit(1)
        finally:
            try:
                token_file.unlink()
            except OSError:
                pass

    ui.info(f"Sending {threemf.name} to {device_id}...")
    try:
        result = cloud_print(
            threemf,
            device_id,
            credentials=creds,
            project_name=project_name,
            timeout=timeout,
            verbose=_verbose,
            skip_ams_mapping=no_ams_mapping,
            ams_trays=ams_trays or None,
        )

        ams_mapping = result.get("_ams_mapping")
        ams_trays_used = result.get("_ams_trays")
        if ams_mapping and ams_trays_used:
            _show_ams_mapping(threemf, ams_trays_used, ams_mapping)

        resp_status = result.get("result", "unknown")
        if resp_status in ("success", "sent"):
            ui.success(f"Print sent successfully! ({resp_status})")
        else:
            ui.console.print(f"Bridge response: {json.dumps(result, indent=2)}", markup=False)
    except Exception as e:
        ui.error(str(e))
        sys.exit(1)


@app.command(hidden=True)
def cancel(
    device: Annotated[str, typer.Option("-d", "--device", help="Printer serial number")] = "",
    printer: Annotated[
        Optional[str], typer.Option("-p", "--printer", help="Named printer from credentials.toml")
    ] = None,
    credentials: Annotated[
        Optional[Path], typer.Option("-c", "--credentials", help="Path to credentials.toml")
    ] = None,
) -> None:
    """Cancel the current print on a Bambu printer."""
    _warn_experimental()
    from boocloud.bridge import cancel_print, load_credentials

    creds_path = credentials
    try:
        creds = load_credentials(creds_path)
    except (FileNotFoundError, ValueError) as e:
        ui.error(str(e))
        sys.exit(1)

    serial = device
    if not serial:
        if printer:
            serial, name = _resolve_printer(printer, creds_path)
        else:
            serial, name = _resolve_printer(None, creds_path)

    if not serial:
        ui.error("No printer specified. Use --device or --printer.")
        sys.exit(1)

    if not ui.prompt_yn("Cancel the current print?", default=False):
        ui.info("Cancelled.")
        return

    try:
        result = cancel_print(serial, credentials=creds, verbose=_verbose)
        resp = result.get("result", "unknown")
        if resp in ("success", "ok"):
            ui.success("Print cancelled.")
        else:
            ui.console.print(f"Bridge response: {json.dumps(result, indent=2)}", markup=False)
    except Exception as e:
        ui.error(str(e))
        sys.exit(1)


@app.command()
def status(
    device: Annotated[str, typer.Argument(help="Printer serial number")] = "",
    printer: Annotated[
        Optional[str], typer.Option("-p", "--printer", help="Named printer from credentials.toml")
    ] = None,
    credentials: Annotated[
        Optional[Path], typer.Option("-c", "--credentials", help="Path to credentials.toml")
    ] = None,
    watch: Annotated[
        bool, typer.Option("-w", "--watch", help="Continuously refresh status display")
    ] = False,
    interval: Annotated[
        int,
        typer.Option("-i", "--interval", help="Poll-mode refresh interval (daemon uses 1s)"),
    ] = 5,
) -> None:
    """Query printer status."""
    from boocloud.bridge import _write_token_json, load_credentials, parse_ams_trays, query_status

    creds_path = credentials

    device_id = device
    printer_name = ""
    if not device_id:
        device_id, printer_name = _resolve_printer(printer, creds_path)

    def _print_header() -> None:
        if printer_name:
            ui.console.print(f"[bold]{printer_name}[/bold]  (bambu-cloud)")
        else:
            ui.console.print(f"[bold]{device_id}[/bold]")

    creds = load_credentials(creds_path)
    token_file = _write_token_json(creds)
    try:
        if watch:
            _status_watch(device_id, token_file, interval, _print_header, _verbose)
        else:
            _print_header()
            st = query_status(device_id, token_file, verbose=_verbose)
            trays = parse_ams_trays(st)
            ui.console.print(_format_status(st, ams_trays=trays))
    finally:
        try:
            token_file.unlink()
        except OSError:
            pass


def _status_watch(
    device_id: str,
    token_file: Path,
    interval: int,
    print_header: Callable,
    verbose: bool,
) -> None:
    """Watch mode: use daemon for fast polling, refresh display in-place."""
    from datetime import datetime, timezone

    from boocloud.bridge import (
        _ensure_daemon,
        parse_ams_trays,
        query_status,
        query_status_daemon,
    )

    use_daemon = _ensure_daemon(token_file, verbose=verbose)
    if use_daemon:
        ui.console.print("[dim]Connected via daemon (fast mode)[/dim]")
    else:
        ui.console.print("[dim]Daemon not available, using poll mode[/dim]")

    last_lines = 0
    try:
        while True:
            try:
                if use_daemon:
                    st = query_status_daemon(device_id)
                else:
                    st = query_status(device_id, token_file, verbose=verbose)
                trays = parse_ams_trays(st)
            except Exception as e:
                if use_daemon:
                    log.debug("Daemon query failed (%s), retrying", e)
                    time.sleep(interval)
                    use_daemon = _ensure_daemon(token_file, verbose=verbose)
                    if not use_daemon:
                        ui.console.print("[dim]Daemon lost — switched to poll mode[/dim]")
                    continue
                ui.error(f"Query failed: {e}")
                time.sleep(interval)
                continue

            if last_lines > 0:
                ui.console.print(f"\033[{last_lines}A\033[J", end="")

            print_header()
            output = _format_status(st, ams_trays=trays)
            now = datetime.now(tz=timezone.utc).astimezone()
            timestamp = now.strftime("%H:%M:%S")
            mode = "daemon" if use_daemon else "poll"
            output += f"\n  [dim]Updated {timestamp} [{mode}]  (Ctrl-C to exit)[/dim]"
            ui.console.print(output)

            last_lines = output.count("\n") + 2

            time.sleep(1 if use_daemon else interval)
    except KeyboardInterrupt:
        ui.console.print()


# ---------------------------------------------------------------------------
# Daemon management
# ---------------------------------------------------------------------------

daemon_app = typer.Typer(
    name="daemon", help="Manage the background bridge daemon", no_args_is_help=True
)
app.add_typer(daemon_app)


@daemon_app.command(name="status")
def daemon_status() -> None:
    """Check if the bridge daemon is running."""
    import urllib.request

    from boocloud.bridge import DAEMON_URL

    try:
        req = urllib.request.Request(f"{DAEMON_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            ui.success("Daemon is running")
            if data.get("connected"):
                ui.console.print("  MQTT: [green]connected[/green]")
            else:
                ui.console.print("  MQTT: [yellow]not connected[/yellow]")
    except (OSError, Exception):
        ui.info("Daemon is not running")
        sys.exit(1)


@daemon_app.command()
def stop() -> None:
    """Stop the bridge daemon."""
    import urllib.request

    from boocloud.bridge import DAEMON_URL, _stop_daemon_docker

    try:
        req = urllib.request.Request(f"{DAEMON_URL}/shutdown", method="POST")
        urllib.request.urlopen(req, timeout=5)
    except (OSError, Exception):
        pass
    _stop_daemon_docker()
    ui.success("Daemon stopped")


@daemon_app.command()
def start(
    credentials: Annotated[
        Optional[Path], typer.Option("-c", "--credentials", help="Path to credentials.toml")
    ] = None,
    foreground: Annotated[
        bool, typer.Option("--foreground", "-f", help="Run in foreground (blocking)")
    ] = False,
) -> None:
    """Start the bridge daemon in the background."""
    import subprocess as sp

    from boocloud.bridge import (
        DOCKER_DAEMON_CONTAINER,
        DOCKER_IMAGE,
        _daemon_ping,
        _find_local_bridge,
        _start_daemon,
        _write_token_json,
        load_credentials,
    )

    if _daemon_ping():
        ui.info("Daemon is already running")
        return

    creds = load_credentials(credentials)
    token_file = _write_token_json(creds)

    if foreground:
        binary = _find_local_bridge()
        if binary:
            cmd = [binary]
            if _verbose:
                cmd.append("-v")
            cmd.extend(["-c", str(token_file.resolve()), "daemon", "--port", "8765"])
            ui.info("Starting daemon in foreground (Ctrl-C to stop)")
            try:
                result = sp.run(cmd)
                sys.exit(result.returncode)
            except KeyboardInterrupt:
                ui.console.print()
        else:
            token_real = str(token_file.resolve())
            cmd = [
                "docker",
                "run",
                "--rm",
                "--name",
                DOCKER_DAEMON_CONTAINER,
                "--platform",
                "linux/amd64",
                "-p",
                "127.0.0.1:8765:8765",
                "-v",
                f"{token_real}:/tmp/credentials.json:ro",
                DOCKER_IMAGE,
                "-c",
                "/tmp/credentials.json",
            ]
            if _verbose:
                cmd.append("-v")
            cmd.extend(["daemon", "--port", "8765", "--bind", "0.0.0.0"])
            ui.info("Starting daemon in foreground via Docker (Ctrl-C to stop)")
            try:
                result = sp.run(cmd)
                sys.exit(result.returncode)
            except KeyboardInterrupt:
                ui.console.print()
    elif _start_daemon(token_file, verbose=_verbose):
        ui.success("Daemon started")
    else:
        ui.error("Failed to start daemon")
        sys.exit(1)


@daemon_app.command()
def restart(
    credentials: Annotated[
        Optional[Path], typer.Option("-c", "--credentials", help="Path to credentials.toml")
    ] = None,
) -> None:
    """Restart the bridge daemon."""
    import urllib.request

    from boocloud.bridge import (
        DAEMON_URL,
        _start_daemon,
        _stop_daemon_docker,
        _write_token_json,
        load_credentials,
    )

    try:
        req = urllib.request.Request(f"{DAEMON_URL}/shutdown", method="POST")
        urllib.request.urlopen(req, timeout=5)
    except (OSError, Exception):
        pass
    _stop_daemon_docker()
    time.sleep(1)

    creds = load_credentials(credentials)
    token_file = _write_token_json(creds)
    if _start_daemon(token_file, verbose=_verbose):
        ui.success("Daemon restarted")
    else:
        ui.error("Failed to start daemon")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    try:
        app(argv, standalone_mode=False)
    except click.UsageError as exc:
        ui.error(str(exc))
        sys.exit(2)
