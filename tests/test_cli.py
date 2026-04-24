"""Tests for boocloud CLI (cli.py) — cloud commands only."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from boocloud.cli import (
    _format_progress_bar,
    _format_status,
    main,
)

# ---------------------------------------------------------------------------
# _cmd_print via main()
# ---------------------------------------------------------------------------


class TestCmdPrint:
    def test_print_missing_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        missing = tmp_path / "nope.gcode.3mf"
        with patch("boocloud.bridge.load_credentials", return_value={"token": "t"}):
            with pytest.raises(SystemExit, match="1"):
                main(["print", str(missing), "-d", "SERIAL"])
        assert "not found" in capsys.readouterr().err

    def test_print_bad_credentials(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")

        with patch("boocloud.bridge.load_credentials", side_effect=FileNotFoundError("no creds")):
            with pytest.raises(SystemExit, match="1"):
                main(["print", str(threemf), "-d", "SERIAL"])
        assert "no creds" in capsys.readouterr().err

    def test_print_no_device_no_serial(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok", "email": "e"}
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text('[cloud]\ntoken = "tok"\n')

        with patch("boocloud.bridge.load_credentials", return_value=creds):
            with pytest.raises(SystemExit, match="1"):
                main(["print", str(threemf), "-c", str(creds_file)])
        assert "no printer configured" in capsys.readouterr().err

    def test_print_device_from_credentials_file(self, tmp_path: Path) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text('[cloud]\ntoken = "tok"\n[printers.myprinter]\nserial = "ABC123"\n')
        creds = {"token": "tok", "email": "e"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cloud_print", return_value={"result": "success"}) as mock_cp,
        ):
            main(["print", str(threemf), "-c", str(creds_file)])
            assert mock_cp.call_args[0][1] == "ABC123"

    def test_print_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cloud_print", return_value={"result": "sent"}),
        ):
            main(["print", str(threemf), "-d", "SERIAL123"])
        assert "successfully" in capsys.readouterr().out

    def test_print_unknown_result(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch(
                "boocloud.bridge.cloud_print",
                return_value={"result": "pending", "info": "x"},
            ),
        ):
            main(["print", str(threemf), "-d", "SERIAL123"])
        out = capsys.readouterr().out
        assert "Bridge response" in out

    def test_print_exception(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cloud_print", side_effect=RuntimeError("bridge failed")),
        ):
            with pytest.raises(SystemExit, match="1"):
                main(["print", str(threemf), "-d", "SERIAL123"])
        assert "bridge failed" in capsys.readouterr().err

    def test_print_with_ams_tray(self, tmp_path: Path) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cloud_print", return_value={"result": "success"}) as mock_cp,
        ):
            main(
                [
                    "print",
                    str(threemf),
                    "-d",
                    "SER",
                    "--ams-tray",
                    "2:PETG-CF:2850E0",
                ]
            )
            call_kw = mock_cp.call_args[1]
            trays = call_kw["ams_trays"]
            assert len(trays) == 1
            assert trays[0]["phys_slot"] == 2
            assert trays[0]["type"] == "PETG-CF"
            assert trays[0]["color"] == "2850E0"

    def test_print_bad_ams_tray_format(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with patch("boocloud.bridge.load_credentials", return_value=creds):
            with pytest.raises(SystemExit, match="1"):
                main(["print", str(threemf), "-d", "SER", "--ams-tray", "bad"])
        assert "SLOT:TYPE:COLOR" in capsys.readouterr().err

    def test_print_with_project_name(self, tmp_path: Path) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cloud_print", return_value={"result": "success"}) as mock_cp,
        ):
            main(["print", str(threemf), "-d", "SER", "--project", "MyProject"])
            assert mock_cp.call_args[1]["project_name"] == "MyProject"

    def test_print_no_ams_mapping_flag(self, tmp_path: Path) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cloud_print", return_value={"result": "success"}) as mock_cp,
        ):
            main(["print", str(threemf), "-d", "SER", "--no-ams-mapping"])
            assert mock_cp.call_args[1]["skip_ams_mapping"] is True

    def test_print_timeout_flag(self, tmp_path: Path) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"fake")
        creds = {"token": "tok"}

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cloud_print", return_value={"result": "success"}) as mock_cp,
        ):
            main(["print", str(threemf), "-d", "SER", "--timeout", "300"])
            assert mock_cp.call_args[1]["timeout"] == 300


def _make_threemf_with_bed_type(path: Path, bed_type: str) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "Metadata/project_settings.config",
            json.dumps({"curr_bed_type": bed_type}),
        )


class TestPrintBedTypeMismatch:
    """Tests for bed_type mismatch detection."""

    def test_mismatch_emits_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        _make_threemf_with_bed_type(threemf, "Cool Plate")
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text(
            '[cloud]\ntoken = "tok"\n'
            '[printers.myp]\nserial = "ABC"\nplate_type = "Textured PEI Plate"\n'
        )
        with (
            patch("boocloud.bridge.load_credentials", return_value={"token": "t"}),
            patch("boocloud.bridge.cloud_print", return_value={"result": "sent"}),
        ):
            main(["print", str(threemf), "-p", "myp", "-c", str(creds_file), "--no-ams-mapping"])
        err = capsys.readouterr().err
        assert "Cool Plate" in err
        assert "Textured PEI Plate" in err
        assert "nozzle crash" in err

    def test_match_emits_no_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        _make_threemf_with_bed_type(threemf, "Textured PEI Plate")
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text(
            '[cloud]\ntoken = "tok"\n'
            '[printers.myp]\nserial = "ABC"\nplate_type = "Textured PEI Plate"\n'
        )
        with (
            patch("boocloud.bridge.load_credentials", return_value={"token": "t"}),
            patch("boocloud.bridge.cloud_print", return_value={"result": "sent"}),
        ):
            main(["print", str(threemf), "-p", "myp", "-c", str(creds_file), "--no-ams-mapping"])
        assert "nozzle crash" not in capsys.readouterr().err

    def test_match_case_insensitive(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        _make_threemf_with_bed_type(threemf, "textured pei plate")
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text(
            '[cloud]\ntoken = "tok"\n'
            '[printers.myp]\nserial = "ABC"\nplate_type = "Textured PEI Plate"\n'
        )
        with (
            patch("boocloud.bridge.load_credentials", return_value={"token": "t"}),
            patch("boocloud.bridge.cloud_print", return_value={"result": "sent"}),
        ):
            main(["print", str(threemf), "-p", "myp", "-c", str(creds_file), "--no-ams-mapping"])
        assert "nozzle crash" not in capsys.readouterr().err

    def test_no_plate_configured_skips_silently(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        _make_threemf_with_bed_type(threemf, "Cool Plate")
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text('[cloud]\ntoken = "tok"\n[printers.myp]\nserial = "ABC"\n')
        with (
            patch("boocloud.bridge.load_credentials", return_value={"token": "t"}),
            patch("boocloud.bridge.cloud_print", return_value={"result": "sent"}),
        ):
            main(["print", str(threemf), "-p", "myp", "-c", str(creds_file), "--no-ams-mapping"])
        assert "nozzle crash" not in capsys.readouterr().err

    def test_unreadable_archive_does_not_warn(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        threemf = tmp_path / "test.gcode.3mf"
        threemf.write_bytes(b"not a zip")
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text(
            '[cloud]\ntoken = "tok"\n'
            '[printers.myp]\nserial = "ABC"\nplate_type = "Textured PEI Plate"\n'
        )
        with (
            patch("boocloud.bridge.load_credentials", return_value={"token": "t"}),
            patch("boocloud.bridge.cloud_print", return_value={"result": "sent"}),
        ):
            main(["print", str(threemf), "-p", "myp", "-c", str(creds_file), "--no-ams-mapping"])
        assert "nozzle crash" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cancel via main()
# ---------------------------------------------------------------------------


class TestCmdCancel:
    def test_cancel_bad_credentials(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("boocloud.bridge.load_credentials", side_effect=FileNotFoundError("no creds")):
            with pytest.raises(SystemExit, match="1"):
                main(["cancel", "-d", "SERIAL"])
        assert "no creds" in capsys.readouterr().err

    def test_cancel_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        creds = {"token": "tok"}
        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cancel_print", return_value={"result": "success"}) as mock_cp,
            patch("boocloud.cli.ui.prompt_yn", return_value=True),
        ):
            main(["cancel", "-d", "SERIAL123"])
            assert mock_cp.call_args[0][0] == "SERIAL123"
        assert "cancelled" in capsys.readouterr().out.lower()

    def test_cancel_aborted_by_user(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        creds = {"token": "tok"}
        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cancel_print") as mock_cp,
            patch("boocloud.cli.ui.prompt_yn", return_value=False),
        ):
            main(["cancel", "-d", "SERIAL123"])
            mock_cp.assert_not_called()

    def test_cancel_by_printer_name(self, tmp_path: Path) -> None:
        creds_file = tmp_path / "credentials.toml"
        creds_file.write_text('[cloud]\ntoken = "tok"\n[printers.myprinter]\nserial = "ABC123"\n')
        creds = {"token": "tok"}
        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge.cancel_print", return_value={"result": "ok"}) as mock_cp,
            patch("boocloud.cli.ui.prompt_yn", return_value=True),
        ):
            main(["cancel", "-p", "myprinter", "-c", str(creds_file)])
            assert mock_cp.call_args[0][0] == "ABC123"


# ---------------------------------------------------------------------------
# _cmd_status via main()
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def _make_status(self, **overrides: object) -> dict:
        base: dict = {
            "gcode_state": "IDLE",
            "nozzle_temper": 25,
            "bed_temper": 22,
        }
        base.update(overrides)
        return base

    def test_status_basic(self, capsys: pytest.CaptureFixture[str]) -> None:
        creds = {"token": "tok"}
        status = self._make_status()
        token = MagicMock()

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge._write_token_json", return_value=token),
            patch("boocloud.bridge.query_status", return_value=status),
            patch("boocloud.bridge.parse_ams_trays", return_value=[]),
        ):
            main(["status", "DEVICE123"])
        out = capsys.readouterr().out
        assert "IDLE" in out
        assert "25" in out

    def test_status_with_progress(self, capsys: pytest.CaptureFixture[str]) -> None:
        creds = {"token": "tok"}
        status = self._make_status(mc_percent=42, mc_remaining_time=15, subtask_name="benchy.3mf")
        token = MagicMock()

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge._write_token_json", return_value=token),
            patch("boocloud.bridge.query_status", return_value=status),
            patch("boocloud.bridge.parse_ams_trays", return_value=[]),
        ):
            main(["status", "DEVICE123"])
        out = capsys.readouterr().out
        assert "42%" in out
        assert "15" in out
        assert "benchy.3mf" in out

    def test_status_with_ams_trays(self, capsys: pytest.CaptureFixture[str]) -> None:
        creds = {"token": "tok"}
        status = self._make_status()
        trays = [
            {
                "phys_slot": 0,
                "type": "PLA",
                "color": "FFFFFF",
                "tray_info_idx": "GFL00",
            },
        ]
        token = MagicMock()

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge._write_token_json", return_value=token),
            patch("boocloud.bridge.query_status", return_value=status),
            patch("boocloud.bridge.parse_ams_trays", return_value=trays),
        ):
            main(["status", "DEVICE123"])
        out = capsys.readouterr().out
        assert "AMS" in out
        assert "PLA" in out

    def test_status_token_cleanup_on_error(self) -> None:
        creds = {"token": "tok"}
        token = MagicMock()

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge._write_token_json", return_value=token),
            patch("boocloud.bridge.query_status", side_effect=RuntimeError("fail")),
        ):
            with pytest.raises(RuntimeError):
                main(["status", "DEVICE123"])
        token.unlink.assert_called_once()


# ---------------------------------------------------------------------------
# _format_progress_bar
# ---------------------------------------------------------------------------


class TestFormatProgressBar:
    def test_zero_percent(self) -> None:
        bar = _format_progress_bar(0, width=10)
        assert bar == "[░░░░░░░░░░] 0%"

    def test_hundred_percent(self) -> None:
        bar = _format_progress_bar(100, width=10)
        assert bar == "[██████████] 100%"

    def test_fifty_percent(self) -> None:
        bar = _format_progress_bar(50, width=10)
        assert bar == "[█████░░░░░] 50%"

    def test_clamps_above_100(self) -> None:
        bar = _format_progress_bar(120, width=10)
        assert bar == "[██████████] 100%"

    def test_clamps_below_0(self) -> None:
        bar = _format_progress_bar(-5, width=10)
        assert bar == "[░░░░░░░░░░] 0%"

    def test_default_width(self) -> None:
        bar = _format_progress_bar(50)
        assert bar.startswith("[")
        assert "50%" in bar


# ---------------------------------------------------------------------------
# _format_status
# ---------------------------------------------------------------------------


class TestFormatStatus:
    def test_idle_no_color(self) -> None:
        status = {"gcode_state": "IDLE", "nozzle_temper": 25, "bed_temper": 22}
        text = _format_status(status, use_color=False)
        assert "State:" in text and "IDLE" in text
        assert "25°C" in text
        assert "22°C" in text

    def test_temps_rounded(self) -> None:
        status = {"gcode_state": "IDLE", "nozzle_temper": 18.71875, "bed_temper": 16.375}
        text = _format_status(status, use_color=False)
        assert "19°C" in text
        assert "16°C" in text

    def test_target_temps_shown(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 180,
            "nozzle_target_temper": 220,
            "bed_temper": 40,
            "bed_target_temper": 60,
        }
        text = _format_status(status, use_color=False)
        assert "180°C → 220°C" in text
        assert "40°C → 60°C" in text

    def test_running_with_color(self) -> None:
        status = {"gcode_state": "RUNNING", "nozzle_temper": 220, "bed_temper": 60}
        text = _format_status(status, use_color=True)
        assert "[green]" in text
        assert "RUNNING" in text

    def test_failed_color(self) -> None:
        status = {"gcode_state": "FAILED", "nozzle_temper": 0, "bed_temper": 0}
        text = _format_status(status, use_color=True)
        assert "[red bold]" in text

    def test_pause_color(self) -> None:
        status = {"gcode_state": "PAUSE", "nozzle_temper": 0, "bed_temper": 0}
        text = _format_status(status, use_color=True)
        assert "[yellow]" in text

    def test_finish_color(self) -> None:
        status = {"gcode_state": "FINISH", "nozzle_temper": 0, "bed_temper": 0}
        text = _format_status(status, use_color=True)
        assert "[blue]" in text

    def test_unknown_state_no_color_escape(self) -> None:
        status = {"gcode_state": "WEIRD", "nozzle_temper": 0, "bed_temper": 0}
        text = _format_status(status, use_color=True)
        assert "WEIRD" in text

    def test_progress_bar_rendered(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "mc_percent": 42,
            "mc_remaining_time": 83,
        }
        text = _format_status(status, use_color=False)
        assert "42%" in text
        assert "1h 23m" in text
        assert "█" in text

    def test_progress_no_eta(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "mc_percent": 10,
            "mc_remaining_time": None,
        }
        text = _format_status(status, use_color=False)
        assert "10%" in text
        assert "ETA ?" in text

    def test_subtask_name(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "subtask_name": "benchy.3mf",
        }
        text = _format_status(status, use_color=False)
        assert "benchy.3mf" in text

    def test_ams_trays_1_indexed(self) -> None:
        status = {"gcode_state": "IDLE", "nozzle_temper": 25, "bed_temper": 22}
        trays = [{"phys_slot": 0, "type": "PLA", "color": "FFFFFF", "tray_info_idx": "GFL00"}]
        text = _format_status(status, ams_trays=trays, use_color=False)
        assert "AMS:" in text
        assert "slot 1" in text
        assert "PLA" in text
        assert "#FFFFFF" in text

    def test_ams_active_tray_indicator(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "ams": {"tray_now": 0},
        }
        trays = [
            {"phys_slot": 0, "type": "PLA", "color": "FFFFFF", "tray_info_idx": "GFL00"},
            {"phys_slot": 1, "type": "PETG", "color": "2850E0", "tray_info_idx": "GFG98"},
        ]
        text = _format_status(status, ams_trays=trays, use_color=False)
        assert "<-- printing" in text
        lines = text.split("\n")
        pla_line = [ln for ln in lines if "PLA" in ln][0]
        petg_line = [ln for ln in lines if "PETG" in ln][0]
        assert "<-- printing" in pla_line
        assert "<-- printing" not in petg_line

    def test_ams_color_swatch(self) -> None:
        status = {"gcode_state": "IDLE", "nozzle_temper": 25, "bed_temper": 22}
        trays = [{"phys_slot": 0, "type": "PLA", "color": "2850E0", "tray_info_idx": "GFL00"}]
        text = _format_status(status, ams_trays=trays, use_color=True)
        assert "on rgb(" in text

    def test_print_stage_shown(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "mc_print_stage": 2,
            "layer_num": 0,
        }
        text = _format_status(status, use_color=False)
        assert "Stage:" in text
        assert "heatbed preheating" in text

    def test_print_stage_printing_when_layer_positive(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "mc_print_stage": 2,
            "layer_num": 5,
        }
        text = _format_status(status, use_color=False)
        assert "Stage:" in text
        assert "printing" in text

    def test_no_stage_when_idle(self) -> None:
        status = {"gcode_state": "IDLE", "nozzle_temper": 25, "bed_temper": 22}
        text = _format_status(status, use_color=False)
        assert "Stage:" not in text

    def test_eta_minutes_only(self) -> None:
        status = {
            "gcode_state": "RUNNING",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "mc_percent": 90,
            "mc_remaining_time": 5,
        }
        text = _format_status(status, use_color=False)
        assert "5m" in text
        assert "0h" not in text


class TestStatusWatchArgs:
    """Test that --watch and --interval flags are parsed correctly."""

    def test_watch_flag_parsed(self) -> None:
        creds = {"token": "tok"}
        status = {"gcode_state": "IDLE", "nozzle_temper": 25, "bed_temper": 22}
        token = MagicMock()

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge._write_token_json", return_value=token),
            patch("boocloud.bridge.query_status", return_value=status),
            patch("boocloud.bridge.parse_ams_trays", return_value=[]),
            patch("time.sleep", side_effect=KeyboardInterrupt),
        ):
            main(["status", "DEVICE123", "--watch"])

    def test_interval_flag_parsed(self) -> None:
        creds = {"token": "tok"}
        status = {"gcode_state": "IDLE", "nozzle_temper": 25, "bed_temper": 22}
        token = MagicMock()

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge._write_token_json", return_value=token),
            patch("boocloud.bridge._ensure_daemon", return_value=False),
            patch("boocloud.bridge.query_status", return_value=status),
            patch("boocloud.bridge.parse_ams_trays", return_value=[]),
            patch("time.sleep", side_effect=KeyboardInterrupt) as mock_sleep,
        ):
            main(["status", "DEVICE123", "--watch", "--interval", "5"])
        mock_sleep.assert_called_once_with(5)

    def test_watch_short_flag(self) -> None:
        creds = {"token": "tok"}
        status = {"gcode_state": "IDLE", "nozzle_temper": 25, "bed_temper": 22}
        token = MagicMock()

        with (
            patch("boocloud.bridge.load_credentials", return_value=creds),
            patch("boocloud.bridge._write_token_json", return_value=token),
            patch("boocloud.bridge._ensure_daemon", return_value=False),
            patch("boocloud.bridge.query_status", return_value=status),
            patch("boocloud.bridge.parse_ams_trays", return_value=[]),
            patch("time.sleep", side_effect=KeyboardInterrupt),
        ):
            main(["status", "DEVICE123", "-w"])
