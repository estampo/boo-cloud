"""Tests for bridge.py — local binary fallback and Docker chain."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

import pytest

from boocloud.bridge import (
    DOCKER_DAEMON_CONTAINER,
    DOCKER_IMAGE,
    EXPECTED_API_VERSION,
    _build_ams_mapping,
    _check_daemon_version,
    _cloud_print_impl,
    _ensure_daemon,
    _find_local_bridge,
    _kill_local_daemon,
    _patch_config_3mf_colors,
    _run_bridge_local,
    _shutdown_daemon,
    _start_daemon_docker,
    _stop_daemon_docker,
    _strip_gcode_from_3mf,
    _write_token_json,
    load_credentials,
    parse_ams_trays,
    query_status,
)


class TestFindLocalBridge:
    def test_finds_binary_on_path(self, tmp_path):
        with (
            patch("boocloud.bridge._IS_MACOS", False),
            patch("boocloud.bridge.shutil.which", return_value="/usr/local/bin/boocloud-bridge"),
        ):
            assert _find_local_bridge() == "/usr/local/bin/boocloud-bridge"

    def test_finds_binary_in_local_bin(self, tmp_path):
        with (
            patch("boocloud.bridge._IS_MACOS", False),
            patch("boocloud.bridge.shutil.which", return_value=None),
            patch("boocloud.bridge.Path.home", return_value=tmp_path),
        ):
            local_bin = tmp_path / ".local" / "bin"
            local_bin.mkdir(parents=True)
            bridge = local_bin / "boocloud-bridge"
            bridge.touch()
            bridge.chmod(0o755)
            assert _find_local_bridge() == str(bridge)

    def test_returns_none_when_not_found(self, tmp_path):
        empty = tmp_path / "empty_home"
        empty.mkdir()
        with (
            patch("boocloud.bridge._IS_MACOS", False),
            patch("boocloud.bridge.shutil.which", return_value=None),
            patch("boocloud.bridge.Path.home", return_value=empty),
        ):
            assert _find_local_bridge() is None

    def test_returns_none_on_macos(self):
        with (
            patch("boocloud.bridge._IS_MACOS", True),
            patch("boocloud.bridge.shutil.which", return_value="/usr/local/bin/boocloud-bridge"),
        ):
            assert _find_local_bridge() is None


class TestRunBridgeLocal:
    def test_passes_args_directly(self):
        with patch("boocloud.bridge.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
            _run_bridge_local(
                "/usr/local/bin/boocloud-bridge",
                ["-c", "/tmp/token.json", "status", "DEV1"],
            )
            cmd = mock_run.call_args[0][0]
            assert cmd == [
                "/usr/local/bin/boocloud-bridge",
                "-c",
                "/tmp/token.json",
                "status",
                "DEV1",
            ]

    def test_verbose_flag_before_args(self):
        with patch("boocloud.bridge.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
            _run_bridge_local(
                "/bin/boocloud-bridge",
                ["-c", "/tmp/token.json", "print", "/f.3mf", "DEV1"],
                verbose=True,
            )
            cmd = mock_run.call_args[0][0]
            assert cmd == [
                "/bin/boocloud-bridge",
                "-v",
                "-c",
                "/tmp/token.json",
                "print",
                "/f.3mf",
                "DEV1",
            ]


class TestRunBridgeFallback:
    def test_uses_local_binary_when_available(self):
        with (
            patch(
                "boocloud.bridge._find_local_bridge",
                return_value="/usr/local/bin/boocloud-bridge",
            ),
            patch("boocloud.bridge._run_bridge_local") as mock_local,
        ):
            from boocloud.bridge import _run_bridge

            _run_bridge(["status", "DEVICE123", "/tmp/token.json"])
            mock_local.assert_called_once()
            assert mock_local.call_args[0][0] == "/usr/local/bin/boocloud-bridge"

    def test_falls_back_to_docker(self):
        with (
            patch("boocloud.bridge._find_local_bridge", return_value=None),
            patch("boocloud.bridge._run_bridge_docker") as mock_docker,
        ):
            from boocloud.bridge import _run_bridge

            _run_bridge(["status", "DEVICE123", "/tmp/token.json"])
            mock_docker.assert_called_once()


def _make_test_3mf(path, filaments, project_settings=None):
    slice_info = "<config>\n  <plate>\n"
    for fid, ftype, color in filaments:
        slice_info += f'    <filament id="{fid}" type="{ftype}" color="#{color}" />\n'
    slice_info += "  </plate>\n</config>"

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/slice_info.config", slice_info)
        if project_settings is not None:
            z.writestr(
                "Metadata/project_settings.config",
                json.dumps(project_settings),
            )


class TestBuildAmsMapping:
    def test_unmatched_filament_raises_error(self, tmp_path):
        threemf = tmp_path / "test.3mf"
        _make_test_3mf(threemf, [(1, "PLA", "FF0000")])

        ams_trays = [
            {
                "phys_slot": 0,
                "ams_id": 0,
                "slot_id": 0,
                "type": "PETG",
                "color": "00FF00",
                "tray_info_idx": "",
            },
        ]

        with pytest.raises(RuntimeError, match="Filament slot 1.*no matching AMS tray"):
            _build_ams_mapping(threemf, ams_trays)

    def test_matched_filament_maps_correctly(self, tmp_path):
        threemf = tmp_path / "test.3mf"
        _make_test_3mf(threemf, [(1, "PLA", "FF0000")])

        ams_trays = [
            {
                "phys_slot": 2,
                "ams_id": 0,
                "slot_id": 2,
                "type": "PLA",
                "color": "FF0000",
                "tray_info_idx": "",
            },
        ]

        result = _build_ams_mapping(threemf, ams_trays)
        assert result["amsMapping"] == [2]
        assert result["amsMapping2"] == [{"ams_id": 0, "slot_id": 2}]

    def test_mixed_matched_and_unmatched_raises(self, tmp_path):
        threemf = tmp_path / "test.3mf"
        _make_test_3mf(
            threemf,
            [(1, "PLA", "FF0000"), (2, "ABS", "0000FF")],
            project_settings={"filament_colour": ["#FF0000FF", "#0000FFFF"]},
        )

        ams_trays = [
            {
                "phys_slot": 1,
                "ams_id": 0,
                "slot_id": 1,
                "type": "PLA",
                "color": "FF0000",
                "tray_info_idx": "",
            },
        ]

        with pytest.raises(RuntimeError, match="Filament slot 2.*no matching AMS tray"):
            _build_ams_mapping(threemf, ams_trays)


def _make_namespaced_3mf(path, filaments, namespace, project_settings=None):
    slice_info = f'<config xmlns="{namespace}">\n  <plate>\n'
    for fid, ftype, color in filaments:
        slice_info += f'    <filament id="{fid}" type="{ftype}" color="#{color}" />\n'
    slice_info += "  </plate>\n</config>"

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/slice_info.config", slice_info)
        if project_settings is not None:
            z.writestr(
                "Metadata/project_settings.config",
                json.dumps(project_settings),
            )


class TestBuildAmsMappingNamespaced:
    def test_namespaced_xml_maps_correctly(self, tmp_path):
        threemf = tmp_path / "ns.3mf"
        _make_namespaced_3mf(
            threemf,
            [(1, "PLA", "FF0000")],
            namespace="http://example.com/bambu",
        )

        ams_trays = [
            {
                "phys_slot": 2,
                "ams_id": 0,
                "slot_id": 2,
                "type": "PLA",
                "color": "FF0000",
                "tray_info_idx": "",
            },
        ]

        result = _build_ams_mapping(threemf, ams_trays)
        assert result["amsMapping"] == [2]
        assert result["amsMapping2"] == [{"ams_id": 0, "slot_id": 2}]

    def test_namespaced_xml_unmatched_raises(self, tmp_path):
        threemf = tmp_path / "ns.3mf"
        _make_namespaced_3mf(
            threemf,
            [(1, "PLA", "FF0000")],
            namespace="http://example.com/bambu",
        )

        ams_trays = [
            {
                "phys_slot": 0,
                "ams_id": 0,
                "slot_id": 0,
                "type": "PETG",
                "color": "00FF00",
                "tray_info_idx": "",
            },
        ]

        with pytest.raises(RuntimeError, match="Filament slot 1.*no matching AMS tray"):
            _build_ams_mapping(threemf, ams_trays)


class TestPatchConfigColors:
    def _make_config_bytes(self, filaments, namespace=None):
        if namespace:
            xml = f'<config xmlns="{namespace}">\n  <plate>\n'
        else:
            xml = "<config>\n  <plate>\n"
        for fid, ftype, color in filaments:
            xml += f'    <filament id="{fid}" type="{ftype}" color="#{color}" />\n'
        xml += "  </plate>\n</config>"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Metadata/slice_info.config", xml)
        return buf.getvalue()

    def test_patches_colors_no_namespace(self, tmp_path):
        config = self._make_config_bytes([(1, "PLA", "FF0000")])
        ams_trays = [
            {"phys_slot": 0, "ams_id": 0, "slot_id": 0, "type": "PLA", "color": "00FF00"},
        ]
        mapping = [0]
        source = tmp_path / "dummy.3mf"
        source.touch()

        patched = _patch_config_3mf_colors(config, source, ams_trays, mapping)
        with zipfile.ZipFile(io.BytesIO(patched), "r") as z:
            root = ET.fromstring(z.read("Metadata/slice_info.config"))
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag[: root.tag.index("}") + 1]
            fil = root.find(f"{ns}plate").find(f"{ns}filament")
            assert fil.get("color") == "#00FF00"

    def test_patches_colors_with_namespace(self, tmp_path):
        config = self._make_config_bytes(
            [(1, "PLA", "FF0000")], namespace="http://example.com/bambu"
        )
        ams_trays = [
            {"phys_slot": 0, "ams_id": 0, "slot_id": 0, "type": "PLA", "color": "00FF00"},
        ]
        mapping = [0]
        source = tmp_path / "dummy.3mf"
        source.touch()

        patched = _patch_config_3mf_colors(config, source, ams_trays, mapping)
        with zipfile.ZipFile(io.BytesIO(patched), "r") as z:
            root = ET.fromstring(z.read("Metadata/slice_info.config"))
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag[: root.tag.index("}") + 1]
            fil = root.find(f"{ns}plate").find(f"{ns}filament")
            assert fil.get("color") == "#00FF00"


class TestLoadCredentials:
    def test_loads_valid_toml(self, tmp_path):
        cred = tmp_path / "credentials.toml"
        cred.write_text('[cloud]\ntoken = "tok"\nrefresh_token = "rt"\nemail = "a@b"\nuid = "u1"\n')
        result = load_credentials(cred)
        assert result["token"] == "tok"
        assert result["refresh_token"] == "rt"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Credentials file not found"):
            load_credentials(tmp_path / "nope.toml")

    def test_missing_cloud_section_raises(self, tmp_path):
        cred = tmp_path / "credentials.toml"
        cred.write_text("[other]\nfoo = 1\n")
        with pytest.raises(ValueError, match="No \\[cloud\\] credentials"):
            load_credentials(cred)

    def test_missing_token_raises(self, tmp_path):
        cred = tmp_path / "credentials.toml"
        cred.write_text('[cloud]\nemail = "a@b"\n')
        with pytest.raises(ValueError, match="No \\[cloud\\] credentials"):
            load_credentials(cred)

    def test_default_path_when_none(self, tmp_path):
        cloud = {"token": "t", "email": "a@b"}
        with patch("boocloud.credentials.load_cloud_credentials", return_value=cloud):
            result = load_credentials(None)
            assert result["token"] == "t"


class TestWriteTokenJson:
    def test_writes_json_with_correct_keys(self, tmp_path):
        cloud = {"token": "tok", "refresh_token": "rt", "email": "a@b", "uid": "u1"}
        path = _write_token_json(cloud, directory=tmp_path)
        try:
            data = json.loads(path.read_text())
            assert data["token"] == "tok"
            assert data["refreshToken"] == "rt"
            assert data["email"] == "a@b"
            assert data["uid"] == "u1"
        finally:
            path.unlink()

    def test_defaults_for_missing_keys(self, tmp_path):
        cloud = {"token": "tok"}
        path = _write_token_json(cloud, directory=tmp_path)
        try:
            data = json.loads(path.read_text())
            assert data["refreshToken"] == ""
            assert data["email"] == ""
            assert data["uid"] == ""
        finally:
            path.unlink()

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="chmod 0o600 not enforced on Windows")
    def test_file_permissions(self, tmp_path):
        cloud = {"token": "tok"}
        path = _write_token_json(cloud, directory=tmp_path)
        try:
            assert oct(path.stat().st_mode & 0o777) == oct(0o600)
        finally:
            path.unlink()


class TestParseAmsTrays:
    def test_parses_single_ams_unit(self):
        status = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_type": "PLA",
                                "tray_color": "FF0000FF",
                                "tray_info_idx": "GFL00",
                            },
                            {
                                "id": "1",
                                "tray_type": "PETG",
                                "tray_color": "00FF00FF",
                                "tray_info_idx": "GFG00",
                            },
                        ],
                    }
                ]
            }
        }
        trays = parse_ams_trays(status)
        assert len(trays) == 2
        assert trays[0] == {
            "phys_slot": 0,
            "ams_id": 0,
            "slot_id": 0,
            "type": "PLA",
            "color": "FF0000",
            "tray_info_idx": "GFL00",
        }
        assert trays[1]["phys_slot"] == 1
        assert trays[1]["color"] == "00FF00"

    def test_skips_empty_trays(self):
        status = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {"id": "0", "tray_type": "", "tray_color": ""},
                            {"id": "1", "tray_type": "PLA", "tray_color": "FFFFFF"},
                        ],
                    }
                ]
            }
        }
        trays = parse_ams_trays(status)
        assert len(trays) == 1
        assert trays[0]["type"] == "PLA"

    def test_multi_ams_units(self):
        status = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [{"id": "2", "tray_type": "PLA", "tray_color": "FF0000"}],
                    },
                    {
                        "id": "1",
                        "tray": [{"id": "0", "tray_type": "ABS", "tray_color": "0000FF"}],
                    },
                ]
            }
        }
        trays = parse_ams_trays(status)
        assert len(trays) == 2
        assert trays[0]["phys_slot"] == 2  # ams_id=0, slot_id=2 -> 0*4+2
        assert trays[1]["phys_slot"] == 4  # ams_id=1, slot_id=0 -> 1*4+0

    def test_empty_status(self):
        assert parse_ams_trays({}) == []
        assert parse_ams_trays({"ams": {}}) == []
        assert parse_ams_trays({"ams": {"ams": []}}) == []


class TestStripGcodeFrom3mf:
    def test_strips_gcode_keeps_metadata(self, tmp_path):
        threemf = tmp_path / "test.3mf"
        with zipfile.ZipFile(threemf, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("_rels/.rels", "<Relationships/>")
            z.writestr("Metadata/slice_info.config", "<config/>")
            z.writestr("Metadata/project_settings.config", "{}")
            z.writestr("Metadata/plate_1.json", '{"plate": 1}')
            z.writestr("Metadata/plate_1.gcode", "G28\nG1 X10")
            z.writestr("Metadata/plate_1.png", b"fake-png")
            z.writestr("Metadata/.md5", "checksums")

        result = _strip_gcode_from_3mf(threemf)
        with zipfile.ZipFile(io.BytesIO(result), "r") as z:
            names = z.namelist()
            assert "[Content_Types].xml" in names
            assert "_rels/.rels" in names
            assert "Metadata/slice_info.config" in names
            assert "Metadata/project_settings.config" in names
            assert "Metadata/plate_1.json" in names
            assert "Metadata/plate_1.gcode" not in names
            assert "Metadata/plate_1.png" not in names
            assert "Metadata/.md5" not in names

    def test_preserves_model_settings_rels(self, tmp_path):
        threemf = tmp_path / "test.3mf"
        with zipfile.ZipFile(threemf, "w") as z:
            z.writestr("Metadata/_rels/model_settings.config.rels", "<rels/>")
            z.writestr("Metadata/model_settings.config", "<model/>")
        result = _strip_gcode_from_3mf(threemf)
        with zipfile.ZipFile(io.BytesIO(result), "r") as z:
            assert "Metadata/_rels/model_settings.config.rels" in z.namelist()
            assert "Metadata/model_settings.config" in z.namelist()


class TestQueryStatus:
    def test_parses_print_key(self, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}")
        status_json = json.dumps({"print": {"mc_percent": 50, "gcode_state": "RUNNING"}})
        with (
            patch("boocloud.bridge._ensure_daemon", return_value=False),
            patch("boocloud.bridge._run_bridge") as mock_bridge,
        ):
            mock_bridge.return_value = subprocess.CompletedProcess([], 0, status_json, "")
            result = query_status("DEV1", token)
            assert result["mc_percent"] == 50

    def test_returns_raw_when_no_print_key(self, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}")
        status_json = json.dumps({"gcode_state": "IDLE"})
        with (
            patch("boocloud.bridge._ensure_daemon", return_value=False),
            patch("boocloud.bridge._run_bridge") as mock_bridge,
        ):
            mock_bridge.return_value = subprocess.CompletedProcess([], 0, status_json, "")
            result = query_status("DEV1", token)
            assert result["gcode_state"] == "IDLE"

    def test_non_json_raises(self, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}")
        with (
            patch("boocloud.bridge._ensure_daemon", return_value=False),
            patch("boocloud.bridge._run_bridge") as mock_bridge,
        ):
            mock_bridge.return_value = subprocess.CompletedProcess([], 1, "error text", "fail")
            with pytest.raises(RuntimeError, match="Bridge returned non-JSON"):
                query_status("DEV1", token)

    def test_uses_daemon_when_available(self, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}")
        status = {"mc_percent": 42, "gcode_state": "RUNNING"}
        with (
            patch("boocloud.bridge._ensure_daemon", return_value=True),
            patch("boocloud.bridge.query_status_daemon", return_value=status) as daemon,
            patch("boocloud.bridge._run_bridge") as subprocess_bridge,
        ):
            result = query_status("DEV1", token)
        assert result["mc_percent"] == 42
        daemon.assert_called_once_with("DEV1")
        subprocess_bridge.assert_not_called()

    def test_daemon_failure_falls_back_to_subprocess(self, tmp_path):
        """If the per-call ping passes but the daemon query then fails,
        fall back to the subprocess path (don't retry — _ensure_daemon
        validates health on every call already)."""
        token = tmp_path / "token.json"
        token.write_text("{}")
        status_json = json.dumps({"print": {"gcode_state": "IDLE"}})
        with (
            patch("boocloud.bridge._ensure_daemon", return_value=True),
            patch("boocloud.bridge.query_status_daemon", side_effect=RuntimeError("boom")),
            patch("boocloud.bridge._run_bridge") as subprocess_bridge,
        ):
            subprocess_bridge.return_value = subprocess.CompletedProcess([], 0, status_json, "")
            result = query_status("DEV1", token)
        assert result["gcode_state"] == "IDLE"
        subprocess_bridge.assert_called_once()


class TestEnsureDaemonHealth:
    """``_ensure_daemon`` pings on every call and restarts a wedged daemon."""

    def test_ping_ok_returns_immediately(self, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}")
        with (
            patch("boocloud.bridge._daemon_ping", return_value=True),
            patch("boocloud.bridge._check_daemon_version"),
            patch("boocloud.bridge._shutdown_daemon") as shutdown,
            patch("boocloud.bridge._start_daemon") as start,
        ):
            assert _ensure_daemon(token) is True
        shutdown.assert_not_called()
        start.assert_not_called()

    def test_ping_fails_shuts_down_and_starts_fresh(self, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}")
        with (
            patch("boocloud.bridge._daemon_ping", return_value=False),
            patch("boocloud.bridge._check_daemon_version"),
            patch("boocloud.bridge._shutdown_daemon", return_value=True) as shutdown,
            patch("boocloud.bridge._start_daemon", return_value=True) as start,
        ):
            assert _ensure_daemon(token) is True
        shutdown.assert_called_once()
        start.assert_called_once()


class TestShutdownDaemon:
    """``_shutdown_daemon`` tries cooperative shutdown then force-kills."""

    def test_cooperative_shutdown_succeeds(self):
        with (
            patch("urllib.request.urlopen"),
            patch("boocloud.bridge._daemon_ping", return_value=False),
            patch("boocloud.bridge._kill_local_daemon") as kill,
        ):
            assert _shutdown_daemon() is True
        kill.assert_not_called()

    def test_force_kill_when_cooperative_shutdown_fails(self):
        # /shutdown succeeds at HTTP level but daemon stays up — wedged HTTP handler.
        with (
            patch("urllib.request.urlopen"),
            patch(
                "boocloud.bridge._daemon_ping",
                # 4 polls after /shutdown all True, then 1st post-kill ping False
                side_effect=[True, True, True, True, False],
            ),
            patch("boocloud.bridge._kill_local_daemon", return_value=True) as kill,
        ):
            assert _shutdown_daemon() is True
        kill.assert_called_once()

    def test_returns_false_when_daemon_refuses_to_die(self):
        with (
            patch("urllib.request.urlopen"),
            patch("boocloud.bridge._daemon_ping", return_value=True),
            patch("boocloud.bridge._kill_local_daemon", return_value=False),
        ):
            assert _shutdown_daemon() is False


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="native daemon kill path is Unix-only; Windows uses Docker (see README)",
)
class TestKillLocalDaemon:
    """``_kill_local_daemon`` finds the PID via PID-file then pgrep, then kills."""

    def test_uses_pid_file_when_present(self, tmp_path, monkeypatch):
        # Spawn a long-lived dummy process to act as the "daemon"
        proc = subprocess.Popen(["sleep", "60"])
        try:
            pid_file = tmp_path / "bridge.pid"
            pid_file.write_text(f"{proc.pid}\n")
            monkeypatch.setattr("boocloud.bridge._pid_file_path", lambda: pid_file)
            # No pgrep needed — and we don't want it picking up the test runner
            with patch(
                "boocloud.bridge.subprocess.run",
                side_effect=FileNotFoundError,
            ):
                assert _kill_local_daemon() is True
            proc.wait(timeout=5)
            assert not pid_file.exists()  # cleaned up after kill
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_stale_pid_file_is_cleared(self, tmp_path, monkeypatch):
        # 99999 is almost certainly not a live PID
        pid_file = tmp_path / "bridge.pid"
        pid_file.write_text("99999\n")
        monkeypatch.setattr("boocloud.bridge._pid_file_path", lambda: pid_file)
        with patch(
            "boocloud.bridge.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            # No pgrep, no live PID — nothing to kill.
            assert _kill_local_daemon() is False
        # Stale file was removed during the sanity check.
        assert not pid_file.exists()

    def test_falls_back_to_pgrep_when_no_pid_file(self, tmp_path, monkeypatch):
        proc = subprocess.Popen(["sleep", "60"])
        try:
            pid_file = tmp_path / "bridge.pid"
            # File deliberately doesn't exist
            monkeypatch.setattr("boocloud.bridge._pid_file_path", lambda: pid_file)

            def fake_run(cmd, **kwargs):
                if cmd[0] == "pgrep":
                    return subprocess.CompletedProcess(cmd, 0, f"{proc.pid}\n", "")
                raise FileNotFoundError

            with patch("boocloud.bridge.subprocess.run", side_effect=fake_run):
                assert _kill_local_daemon() is True
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_no_pid_file_no_pgrep_returns_false(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "bridge.pid"
        monkeypatch.setattr("boocloud.bridge._pid_file_path", lambda: pid_file)
        with patch(
            "boocloud.bridge.subprocess.run",
            side_effect=FileNotFoundError,  # pgrep not installed
        ):
            assert _kill_local_daemon() is False

    def test_does_not_target_own_pid(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "bridge.pid"
        # No PID file written
        monkeypatch.setattr("boocloud.bridge._pid_file_path", lambda: pid_file)

        def fake_run(cmd, **kwargs):
            if cmd[0] == "pgrep":
                return subprocess.CompletedProcess(cmd, 0, f"{os.getpid()}\n", "")
            raise FileNotFoundError

        with patch("boocloud.bridge.subprocess.run", side_effect=fake_run):
            # Only matched PID is our own → filtered out → nothing to kill.
            assert _kill_local_daemon() is False


class TestPidFilePath:
    def test_uses_xdg_runtime_dir_when_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        from boocloud.bridge import _pid_file_path

        assert _pid_file_path() == tmp_path / "boocloud-bridge.pid"

    def test_falls_back_to_temp_when_xdg_unset(self, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        from boocloud.bridge import _pid_file_path

        p = _pid_file_path()
        getuid = getattr(os, "getuid", None)
        expected = f"boocloud-bridge-{getuid()}.pid" if getuid else "boocloud-bridge.pid"
        assert p.name == expected
        # Should be in a known temp dir, not / or cwd
        assert str(p).startswith(tempfile.gettempdir())

    def test_falls_back_to_temp_without_getuid(self, monkeypatch):
        """Windows has no ``os.getuid`` — path drops the per-user suffix."""
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.delattr(os, "getuid", raising=False)
        from boocloud.bridge import _pid_file_path

        p = _pid_file_path()
        assert p.name == "boocloud-bridge.pid"
        assert str(p).startswith(tempfile.gettempdir())


class TestCloudPrintImpl:
    def _setup_3mf(self, tmp_path):
        threemf = tmp_path / "test.gcode.3mf"
        _make_test_3mf(threemf, [(1, "PLA", "FF0000")])
        token = tmp_path / "token.json"
        token.write_text("{}")
        return threemf, token

    def test_skip_ams_builds_basic_args(self, tmp_path):
        threemf, token = self._setup_3mf(tmp_path)
        response = {"result": "success"}
        with patch("boocloud.bridge._run_bridge") as mock_bridge:
            mock_bridge.return_value = subprocess.CompletedProcess([], 0, json.dumps(response), "")
            result = _cloud_print_impl(
                threemf,
                "DEV1",
                token,
                project_name="test",
                timeout=60,
                verbose=False,
                skip_ams_mapping=True,
                ams_trays=[],
            )
            assert result == response
            call_args = mock_bridge.call_args[0][0]
            assert call_args[0] == "-c"
            assert call_args[2] == "print"
            assert "--project" in call_args
            assert call_args[call_args.index("--project") + 1] == "test"
            assert "--timeout" in call_args
            assert "--config-3mf" in call_args

    def test_with_ams_trays_builds_mapping_args(self, tmp_path):
        threemf, token = self._setup_3mf(tmp_path)
        ams_trays = [
            {
                "phys_slot": 2,
                "ams_id": 0,
                "slot_id": 2,
                "type": "PLA",
                "color": "FF0000",
                "tray_info_idx": "",
            },
        ]
        response = {"result": "success"}
        with patch("boocloud.bridge._run_bridge") as mock_bridge:
            mock_bridge.return_value = subprocess.CompletedProcess([], 0, json.dumps(response), "")
            _cloud_print_impl(
                threemf,
                "DEV1",
                token,
                project_name="boocloud",
                timeout=120,
                verbose=False,
                skip_ams_mapping=False,
                ams_trays=ams_trays,
            )
            call_args = mock_bridge.call_args[0][0]
            assert "--ams-mapping" in call_args
            assert "--ams-mapping2" in call_args

    def test_non_json_response_raises(self, tmp_path):
        threemf, token = self._setup_3mf(tmp_path)
        with patch("boocloud.bridge._run_bridge") as mock_bridge:
            mock_bridge.return_value = subprocess.CompletedProcess([], 1, "garbage", "err")
            with pytest.raises(RuntimeError, match="Bridge returned non-JSON"):
                _cloud_print_impl(
                    threemf,
                    "DEV1",
                    token,
                    project_name="boocloud",
                    timeout=60,
                    verbose=False,
                    skip_ams_mapping=True,
                    ams_trays=[],
                )

    def test_cleans_up_config_3mf(self, tmp_path):
        threemf, token = self._setup_3mf(tmp_path)
        response = {"result": "success"}
        with patch("boocloud.bridge._run_bridge") as mock_bridge:
            mock_bridge.return_value = subprocess.CompletedProcess([], 0, json.dumps(response), "")
            _cloud_print_impl(
                threemf,
                "DEV1",
                token,
                project_name="boocloud",
                timeout=60,
                verbose=False,
                skip_ams_mapping=True,
                ams_trays=[],
            )
        config_path = tmp_path / "test.gcode_config.3mf"
        assert not config_path.exists()


class TestCheckDaemonVersion:
    def _mock_health(self, data: dict):
        import urllib.request

        body = json.dumps(data).encode()

        class FakeResp:
            status = 200

            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return patch.object(urllib.request, "urlopen", return_value=FakeResp())

    def test_compatible_version_passes(self):
        health = {
            "status": "ok",
            "bridge_version": "0.1.0",
            "api_version": EXPECTED_API_VERSION,
            "plugin_version": "02.05.00.00",
        }
        with self._mock_health(health):
            _check_daemon_version()

    def test_incompatible_version_raises(self):
        health = {
            "status": "ok",
            "bridge_version": "0.9.0",
            "api_version": 999,
            "plugin_version": "02.05.00.00",
        }
        with self._mock_health(health):
            with pytest.raises(RuntimeError, match="Bridge API version mismatch"):
                _check_daemon_version()

    def test_missing_api_version_warns(self, caplog):
        health = {"status": "ok"}
        with self._mock_health(health):
            import logging

            with caplog.at_level(logging.WARNING, logger="boocloud.bridge"):
                _check_daemon_version()
            assert "does not report api_version" in caplog.text

    def test_unreachable_daemon_warns(self, caplog):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            import logging

            with caplog.at_level(logging.WARNING, logger="boocloud.bridge"):
                _check_daemon_version()
            assert "Could not query bridge version" in caplog.text


class TestStartDaemonDocker:
    def test_returns_false_when_docker_not_installed(self, tmp_path):
        token = tmp_path / "creds.json"
        token.write_text("{}")
        with patch("boocloud.bridge.subprocess.run", side_effect=FileNotFoundError):
            assert _start_daemon_docker(token) is False

    def test_returns_false_when_docker_info_times_out(self, tmp_path):
        token = tmp_path / "creds.json"
        token.write_text("{}")
        with patch(
            "boocloud.bridge.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["docker", "info"], 10),
        ):
            assert _start_daemon_docker(token) is False

    def test_returns_false_when_docker_not_running(self, tmp_path):
        token = tmp_path / "creds.json"
        token.write_text("{}")
        failed = subprocess.CompletedProcess([], returncode=1, stderr="error")
        with patch("boocloud.bridge.subprocess.run", return_value=failed):
            assert _start_daemon_docker(token) is False

    def test_launches_container_on_success(self, tmp_path):
        token = tmp_path / "creds.json"
        token.write_text("{}")
        ok = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return ok

        with patch("boocloud.bridge.subprocess.run", side_effect=fake_run):
            assert _start_daemon_docker(token) is True
        docker_run_cmd = calls[2]
        assert "docker" in docker_run_cmd[0]
        assert "-d" in docker_run_cmd
        assert DOCKER_DAEMON_CONTAINER in docker_run_cmd
        assert DOCKER_IMAGE in docker_run_cmd

    def test_publishes_port_only_on_loopback(self, tmp_path):
        token = tmp_path / "creds.json"
        token.write_text("{}")
        ok = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return ok

        with patch("boocloud.bridge.subprocess.run", side_effect=fake_run):
            _start_daemon_docker(token)
        docker_run_cmd = calls[2]
        port_idx = docker_run_cmd.index("-p")
        assert docker_run_cmd[port_idx + 1] == "127.0.0.1:8765:8765"

    def test_returns_false_on_docker_run_failure(self, tmp_path):
        token = tmp_path / "creds.json"
        token.write_text("{}")
        ok = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        fail = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="fail")
        results = iter([ok, ok, fail])

        with patch("boocloud.bridge.subprocess.run", side_effect=lambda *a, **kw: next(results)):
            assert _start_daemon_docker(token) is False


class TestStopDaemonDocker:
    def test_calls_docker_rm(self):
        calls: list[list[str]] = []
        ok = subprocess.CompletedProcess([], returncode=0)

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return ok

        with patch("boocloud.bridge.subprocess.run", side_effect=fake_run):
            _stop_daemon_docker()
        assert calls[0] == ["docker", "rm", "-f", DOCKER_DAEMON_CONTAINER]
