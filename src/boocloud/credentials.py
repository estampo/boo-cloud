"""Load and manage printer credentials from credentials.toml."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


def _cache_dir() -> Path:
    """Return a writable cache directory.

    Resolution order:
    1. ``XDG_CACHE_HOME/boo-cloud`` (if set)
    2. ``~/.cache/boo-cloud`` (Unix) / ``AppData/Local/boo-cloud/cache`` (Windows)
    3. ``tempfile.gettempdir()/boo-cloud`` (fallback when home is not writable)
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        d = Path(xdg) / "boo-cloud"
    elif sys.platform == "win32":
        d = Path.home() / "AppData" / "Local" / "boo-cloud" / "cache"
    else:
        d = Path.home() / ".cache" / "boo-cloud"

    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        return d
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "boo-cloud"
        fallback.mkdir(parents=True, exist_ok=True, mode=0o700)
        log.warning("Cannot create cache dir %s — using %s", d, fallback)
        return fallback


def mask_serial(serial: str) -> str:
    """Mask a printer serial, keeping only the last 4 characters visible."""
    if len(serial) <= 4:
        return serial
    return "*" * (len(serial) - 4) + serial[-4:]


def _credentials_source() -> str:
    """Describe where credentials are loaded from (for error messages)."""
    if "BOO_CLOUD_CREDENTIALS_TOML" in os.environ:
        return "BOO_CLOUD_CREDENTIALS_TOML env var"
    if "BAMBOX_CREDENTIALS_TOML" in os.environ:
        return "BAMBOX_CREDENTIALS_TOML env var"
    return str(_credentials_path())


def _credentials_path() -> Path:
    """Return the path to the credentials file.

    Resolution order:
    1. ``BOO_CLOUD_CREDENTIALS`` env var
    2. ``BAMBOX_CREDENTIALS`` env var (migration fallback)
    3. ``ESTAMPO_CREDENTIALS`` env var (legacy fallback)
    4. ``~/.config/boo-cloud/credentials.toml`` (if exists)
    5. ``~/.config/bambox/credentials.toml`` (migration fallback)
    6. ``~/.config/estampo/credentials.toml`` (legacy fallback)
    7. ``~/.config/boo-cloud/credentials.toml`` (default for new installs)
    """
    env = os.environ.get("BOO_CLOUD_CREDENTIALS")
    if env:
        return Path(env)
    env = os.environ.get("BAMBOX_CREDENTIALS")
    if env:
        return Path(env)
    env = os.environ.get("ESTAMPO_CREDENTIALS")
    if env:
        return Path(env)

    if sys.platform == "win32":
        boo_path = Path.home() / "AppData/Roaming/boo-cloud/credentials.toml"
        bambox_path = Path.home() / "AppData/Roaming/bambox/credentials.toml"
        estampo_path = Path.home() / "AppData/Roaming/estampo/credentials.toml"
    else:
        boo_path = Path.home() / ".config/boo-cloud/credentials.toml"
        bambox_path = Path.home() / ".config/bambox/credentials.toml"
        estampo_path = Path.home() / ".config/estampo/credentials.toml"

    candidates = [boo_path, bambox_path, estampo_path]

    if sys.platform == "darwin":
        lib_dir = Path.home() / "Library" / "Application Support"
        candidates.append(lib_dir / "boo-cloud" / "credentials.toml")
        candidates.append(lib_dir / "bambox" / "credentials.toml")
        candidates.append(lib_dir / "estampo" / "credentials.toml")

    for path in candidates:
        if path.exists():
            return path
    return boo_path


def _load_raw() -> dict:
    """Load the raw credentials TOML, or return empty dict if not found."""
    import tomllib

    env_toml = os.environ.get("BOO_CLOUD_CREDENTIALS_TOML")
    if env_toml is None:
        env_toml = os.environ.get("BAMBOX_CREDENTIALS_TOML")
    if env_toml is not None:
        return tomllib.loads(env_toml)

    path = _credentials_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _escape_toml_value(val: str) -> str:
    return val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _quote_toml_key(key: str) -> str:
    if key.isidentifier() and key.isascii():
        return key
    return '"' + _escape_toml_value(key) + '"'


def _write_credentials(data: dict) -> None:
    """Write credentials dict to TOML file with 0o600 permissions."""
    if "BOO_CLOUD_CREDENTIALS_TOML" in os.environ or "BAMBOX_CREDENTIALS_TOML" in os.environ:
        env_var = (
            "BOO_CLOUD_CREDENTIALS_TOML"
            if "BOO_CLOUD_CREDENTIALS_TOML" in os.environ
            else "BAMBOX_CREDENTIALS_TOML"
        )
        raise RuntimeError(
            f"Cannot save credentials: {env_var} is set (read-only mode). "
            f"Unset it or use BOO_CLOUD_CREDENTIALS to point to a writable file."
        )
    path = _credentials_path()
    if sys.platform != "win32":
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            _write_credentials_toml(f, data)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            _write_credentials_toml(f, data)


def _write_credentials_toml(f, data: dict) -> None:  # noqa: ANN001
    cloud = data.get("cloud", {})
    if cloud:
        f.write("[cloud]\n")
        for key, val in cloud.items():
            f.write(f'{key} = "{_escape_toml_value(str(val))}"\n')
        f.write("\n")

    for printer_name, creds in data.get("printers", {}).items():
        f.write(f"[printers.{_quote_toml_key(printer_name)}]\n")
        for key, val in creds.items():
            f.write(f'{key} = "{_escape_toml_value(str(val))}"\n')
        f.write("\n")


def load_cloud_credentials() -> dict[str, str] | None:
    """Load cloud credentials from the [cloud] section."""
    raw = _load_raw()
    cloud = raw.get("cloud")
    if not cloud or not cloud.get("token"):
        return None
    return cloud


def save_cloud_credentials(
    token: str, refresh_token: str, email: str, uid: str, **extra: str
) -> None:
    """Save cloud credentials to the [cloud] section of credentials.toml."""
    raw = _load_raw()
    raw["cloud"] = {
        "token": token,
        "refresh_token": refresh_token,
        "email": email,
        "uid": uid,
        **extra,
    }
    _write_credentials(raw)


def list_printers() -> dict[str, dict[str, str]]:
    """Return all configured printers from credentials.toml."""
    raw = _load_raw()
    return raw.get("printers", {})


def save_printer(name: str, entry: dict[str, str]) -> None:
    """Save a printer entry to credentials.toml."""
    raw = _load_raw()
    if "printers" not in raw:
        raw["printers"] = {}
    raw["printers"][name] = entry
    _write_credentials(raw)


def load_printer_credentials(name: str) -> dict[str, str]:
    """Load credentials for a named printer."""
    raw = _load_raw()
    if not raw:
        raise RuntimeError(
            f"Credentials not found: {_credentials_source()}\n"
            "Run 'boocloud login' or set BOO_CLOUD_CREDENTIALS_TOML to create credentials."
        )
    printers = raw.get("printers", {})
    if name not in printers:
        available = list(printers.keys())
        raise RuntimeError(
            f"Printer '{name}' not found in {_credentials_source()}. Available: {available}"
        )
    creds = dict(printers[name])

    env_serial = os.environ.get("BAMBU_SERIAL")
    if env_serial:
        creds["serial"] = env_serial

    return creds


def write_token_json(cloud: dict[str, str], directory: Path | None = None) -> Path:
    """Write a temp JSON token file for the bridge binary."""
    bridge_data = {
        "token": cloud["token"],
        "refreshToken": cloud.get("refresh_token", ""),
        "email": cloud.get("email", ""),
        "uid": cloud.get("uid", ""),
    }
    d = directory or _cache_dir()
    if directory:
        d.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="bambu_token_", dir=str(d))
    ok = False
    try:
        if sys.platform != "win32":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(bridge_data, f)
        ok = True
    finally:
        if not ok:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass
    return Path(path)


@contextmanager
def cloud_token_json():
    """Context manager that yields a temp JSON file path for the bridge binary."""
    cloud = load_cloud_credentials()
    if not cloud:
        raise RuntimeError("No cloud credentials found.\nRun 'boocloud login' to log in.")

    tmp_path = write_token_json(cloud)
    try:
        yield tmp_path
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
