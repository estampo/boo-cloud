"""Tests for boocloud.mcp_server — tool logic, gates, mappings.

These tests target the underlying functions registered as MCP tools, not the
MCP protocol layer itself (FastMCP is exercised by its own upstream tests).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

mcp_server = pytest.importorskip("boocloud.mcp_server")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _tool(name: str):
    """Pull the underlying function out of FastMCP's registry by tool name."""
    return mcp_server.mcp._tool_manager._tools[name].fn


def _stub_print_info(filaments: int = 1) -> object:
    """Build a minimal PrintInfo-like stub with the given filament count."""
    from bambox.info import Filament, PrintInfo

    return PrintInfo(
        time_seconds=300,
        weight_g=10.0,
        layers=50,
        bed_type="Textured PEI Plate",
        printer_model_id="C12",
        filaments=[
            Filament(id=i + 1, type="PLA", color="F2754E", used_m=1.0, used_g=3.0)
            for i in range(filaments)
        ],
    )


def _valid_validation() -> dict:
    return {"valid": True, "errors": [], "warnings": []}


def _ams_trays(slots: list[tuple[int, str, str]]) -> list[dict]:
    """Build a list of parsed AMS tray dicts. slots = [(phys_slot, type, color), ...]."""
    return [
        {
            "phys_slot": slot,
            "ams_id": slot // 4,
            "slot_id": slot % 4,
            "type": ftype,
            "color": color,
            "tray_info_idx": "",
        }
        for slot, ftype, color in slots
    ]


# ---------------------------------------------------------------------------
# list_printers
# ---------------------------------------------------------------------------


class TestListPrinters:
    def test_lists_configured_printers(self) -> None:
        fake = {
            "p1s": {"type": "bambu-cloud", "serial": "00M201234567890", "plate_type": "PEI"},
            "x1c": {"type": "bambu-cloud", "serial": "00X999999999999"},
        }
        with patch("boocloud.mcp_server._creds.list_printers", return_value=fake):
            out = _tool("list_printers")()
        names = {p["name"] for p in out["printers"]}
        assert names == {"p1s", "x1c"}
        p1s = next(p for p in out["printers"] if p["name"] == "p1s")
        assert p1s["plate_type"] == "PEI"
        assert p1s["serial_masked"].endswith("7890")
        assert "00M201234567890" not in p1s["serial_masked"]

    def test_empty_when_no_printers(self) -> None:
        with patch("boocloud.mcp_server._creds.list_printers", return_value={}):
            out = _tool("list_printers")()
        assert out == {"printers": []}


# ---------------------------------------------------------------------------
# _resolve_device_id
# ---------------------------------------------------------------------------


class TestResolveDeviceId:
    def test_explicit_device_passthrough(self) -> None:
        did, name = mcp_server._resolve_device_id(printer=None, device="ABC123")
        assert did == "ABC123"
        assert name is None

    def test_both_args_rejected(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            mcp_server._resolve_device_id(printer="p1s", device="ABC123")

    def test_unknown_printer(self) -> None:
        with patch("boocloud.mcp_server._creds.list_printers", return_value={}):
            with pytest.raises(ValueError, match="Unknown printer"):
                mcp_server._resolve_device_id(printer="ghost", device=None)

    def test_falls_back_to_first_configured(self) -> None:
        fake = {"p1s": {"serial": "00M201234567890"}}
        with patch("boocloud.mcp_server._creds.list_printers", return_value=fake):
            did, name = mcp_server._resolve_device_id(printer=None, device=None)
        assert did == "00M201234567890"
        assert name == "p1s"

    def test_no_printer_configured(self) -> None:
        with patch("boocloud.mcp_server._creds.list_printers", return_value={}):
            with pytest.raises(ValueError, match="No printer specified"):
                mcp_server._resolve_device_id(printer=None, device=None)


# ---------------------------------------------------------------------------
# _validate_ams_slots
# ---------------------------------------------------------------------------


class TestValidateAmsSlots:
    def test_length_mismatch(self) -> None:
        trays = _ams_trays([(0, "PLA", "F2754E")])
        err = mcp_server._validate_ams_slots([1, 2], filament_count=1, trays=trays)
        assert err is not None
        assert "2 entries" in err

    def test_slot_not_loaded(self) -> None:
        trays = _ams_trays([(0, "PLA", "F2754E")])  # phys_slot 0 = slot 1
        err = mcp_server._validate_ams_slots([3], filament_count=1, trays=trays)
        assert err is not None
        assert "Loaded slots" in err

    def test_zero_indexed_rejected(self) -> None:
        trays = _ams_trays([(0, "PLA", "F2754E")])
        err = mcp_server._validate_ams_slots([0], filament_count=1, trays=trays)
        assert err is not None
        assert "1-indexed" in err

    def test_valid_mapping(self) -> None:
        trays = _ams_trays([(0, "PLA", "F2754E"), (1, "PETG", "2850E0")])
        assert mcp_server._validate_ams_slots([1, 2], 2, trays) is None


# ---------------------------------------------------------------------------
# start_print — the safety gates
# ---------------------------------------------------------------------------


class TestStartPrintMissingFile:
    def test_returns_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.gcode.3mf"
        result = _tool("start_print")(file_path=str(missing))
        assert result["result"] == "error"
        assert "not found" in result["message"]


class TestStartPrintValidationGate:
    def test_refuses_on_validation_errors(self, tmp_path: Path) -> None:
        threemf = tmp_path / "bad.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        bad_validation = {
            "valid": False,
            "errors": [{"code": "E003", "message": "md5 mismatch", "detail": ""}],
            "warnings": [],
        }
        with patch("boocloud.mcp_server._bambox_validate") as m:
            m.return_value.to_dict.return_value = bad_validation
            result = _tool("start_print")(file_path=str(threemf), device="ABC")
        assert result["result"] == "invalid"
        assert result["errors"][0]["code"] == "E003"


class TestStartPrintAmsGate:
    def test_ams_loaded_without_slots_refuses(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        trays = _ams_trays([(0, "PLA", "F2754E"), (1, "PETG-CF", "2850E0")])
        with (
            patch("boocloud.mcp_server._bambox_validate") as v,
            patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(1)),
            patch("boocloud.mcp_server._query_ams_trays", return_value=trays),
        ):
            v.return_value.to_dict.return_value = _valid_validation()
            result = _tool("start_print")(file_path=str(threemf), device="ABC")
        assert result["result"] == "needs_ams_slots"
        assert len(result["filaments"]) == 1
        assert len(result["loaded_trays"]) == 2
        assert result["loaded_trays"][0]["slot"] == 1

    def test_ams_not_loaded_proceeds_without_slots(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        with (
            patch("boocloud.mcp_server._bambox_validate") as v,
            patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(1)),
            patch("boocloud.mcp_server._query_ams_trays", return_value=[]),
        ):
            v.return_value.to_dict.return_value = _valid_validation()
            result = _tool("start_print")(file_path=str(threemf), device="ABC")
        # With AMS empty and confirm=False, we get a plan, not needs_ams_slots
        assert result["result"] == "plan"

    def test_skip_ams_bypasses_query(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        with (
            patch("boocloud.mcp_server._bambox_validate") as v,
            patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(1)),
            patch("boocloud.mcp_server._query_ams_trays") as q,
        ):
            v.return_value.to_dict.return_value = _valid_validation()
            result = _tool("start_print")(file_path=str(threemf), device="ABC", skip_ams=True)
        q.assert_not_called()
        assert result["result"] == "plan"

    def test_wrong_number_of_slots_rejected(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        trays = _ams_trays([(0, "PLA", "F2754E"), (1, "PETG", "2850E0")])
        with (
            patch("boocloud.mcp_server._bambox_validate") as v,
            patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(2)),
            patch("boocloud.mcp_server._query_ams_trays", return_value=trays),
        ):
            v.return_value.to_dict.return_value = _valid_validation()
            result = _tool("start_print")(file_path=str(threemf), device="ABC", ams_slots=[1])
        assert result["result"] == "error"
        assert "1 entries" in result["message"]


class TestStartPrintConfirmGate:
    def test_default_is_plan_only(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        trays = _ams_trays([(0, "PLA", "F2754E")])
        with (
            patch("boocloud.mcp_server._bambox_validate") as v,
            patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(1)),
            patch("boocloud.mcp_server._query_ams_trays", return_value=trays),
            patch("boocloud.mcp_server.cloud_print") as cp,
        ):
            v.return_value.to_dict.return_value = _valid_validation()
            result = _tool("start_print")(file_path=str(threemf), device="ABC", ams_slots=[1])
        assert result["result"] == "plan"
        assert result["plan"]["ams_slots"] == [1]
        cp.assert_not_called()

    def test_confirm_submits_with_zero_indexed_mapping(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        trays = _ams_trays([(0, "PLA", "F2754E"), (3, "PETG", "2850E0")])
        with (
            patch("boocloud.mcp_server._bambox_validate") as v,
            patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(2)),
            patch("boocloud.mcp_server._query_ams_trays", return_value=trays),
            patch("boocloud.mcp_server.load_credentials", return_value={"token": "t"}),
            patch("boocloud.mcp_server.cloud_print", return_value={"result": "success"}) as cp,
        ):
            v.return_value.to_dict.return_value = _valid_validation()
            result = _tool("start_print")(
                file_path=str(threemf),
                device="ABC",
                ams_slots=[1, 4],  # 1-indexed user input
                confirm=True,
            )
        assert result["result"] == "success"
        # Bridge gets 0-indexed override
        _, kwargs = cp.call_args
        assert kwargs["ams_mapping_override"] == [0, 3]

    def test_skip_ams_confirm_submits_without_mapping(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")

        with (
            patch("boocloud.mcp_server._bambox_validate") as v,
            patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(1)),
            patch("boocloud.mcp_server._query_ams_trays") as q,
            patch("boocloud.mcp_server.load_credentials", return_value={"token": "t"}),
            patch("boocloud.mcp_server.cloud_print", return_value={"result": "success"}) as cp,
        ):
            v.return_value.to_dict.return_value = _valid_validation()
            result = _tool("start_print")(
                file_path=str(threemf),
                device="ABC",
                skip_ams=True,
                confirm=True,
            )
        q.assert_not_called()
        _, kwargs = cp.call_args
        assert kwargs["ams_mapping_override"] is None
        assert kwargs["skip_ams_mapping"] is True
        assert result["result"] == "success"


# ---------------------------------------------------------------------------
# get_print_info / validate_3mf
# ---------------------------------------------------------------------------


class TestGetPrintInfo:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _tool("get_print_info")(file_path=str(tmp_path / "nope.gcode.3mf"))

    def test_delegates_to_bambox(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")
        with patch("boocloud.mcp_server.extract_print_info", return_value=_stub_print_info(2)) as m:
            out = _tool("get_print_info")(file_path=str(threemf))
        m.assert_called_once()
        assert out["printer_model_id"] == "C12"
        assert len(out["filaments"]) == 2


class TestValidate3mf:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _tool("validate_3mf")(file_path=str(tmp_path / "nope.gcode.3mf"))

    def test_delegates_to_bambox(self, tmp_path: Path) -> None:
        threemf = tmp_path / "x.gcode.3mf"
        threemf.write_bytes(b"placeholder")
        with patch("boocloud.mcp_server._bambox_validate") as v:
            v.return_value.to_dict.return_value = _valid_validation()
            out = _tool("validate_3mf")(file_path=str(threemf))
        assert out == _valid_validation()
