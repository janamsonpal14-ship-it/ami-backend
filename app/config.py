from __future__ import annotations

import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(
    os.environ.get("AMICO_DATA_DIR")
    or os.environ.get("VELORA_DATA_DIR")
    or os.environ.get("ADVOCATEOS_DATA_DIR")
    or (BASE_DIR / "data")
)
UPLOADS_DIR = DATA_DIR / "uploads"
LETTERHEADS_DIR = UPLOADS_DIR / "letterheads"
BHC_CALENDARS_DIR = UPLOADS_DIR / "bhc_calendars"
BARE_ACTS_DIR = UPLOADS_DIR / "bare_acts"
EXPORTS_DIR = DATA_DIR / "exports"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
CACHE_DIR = DATA_DIR / "cache"


def _resolve_db_path() -> Path:
    explicit = (
        os.environ.get("AMICO_DB_PATH")
        or os.environ.get("VELORA_DB_PATH")
        or os.environ.get("ADVOCATEOS_DB_PATH")
    )
    if explicit:
        return Path(explicit)
    amico_db = DATA_DIR / "amico.db"
    velora_db = DATA_DIR / "velora.db"
    legacy_db = DATA_DIR / "advocateos.db"
    if not amico_db.exists():
        # Seamlessly migrate from velora.db or advocateos.db to amico.db
        source_db = velora_db if velora_db.exists() else (legacy_db if legacy_db.exists() else None)
        if source_db:
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                for ext in ("", "-wal", "-shm"):
                    src = Path(f"{source_db}{ext}")
                    dst = Path(f"{amico_db}{ext}")
                    if src.exists() and not dst.exists():
                        src.rename(dst)
            except OSError:
                return source_db
    return amico_db


DB_PATH = _resolve_db_path()
JWT_SECRET_PATH = DATA_DIR / ".jwt_secret"

IST = ZoneInfo("Asia/Kolkata")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 12  # 12 hours per office session


def ensure_directories() -> None:
    """Create local storage directories if they do not exist."""
    for folder in (
        DATA_DIR,
        UPLOADS_DIR,
        LETTERHEADS_DIR,
        BHC_CALENDARS_DIR,
        BARE_ACTS_DIR,
        EXPORTS_DIR,
        SNAPSHOTS_DIR,
        CACHE_DIR,
    ):
        folder.mkdir(parents=True, exist_ok=True)


def get_jwt_secret() -> str:
    """Load JWT secret from environment or persistent local secret file."""
    env_secret = (
        os.environ.get("AMICO_JWT_SECRET")
        or os.environ.get("VELORA_JWT_SECRET")
        or os.environ.get("ADVOCATEOS_JWT_SECRET")
    )
    if env_secret:
        return env_secret
    ensure_directories()
    if JWT_SECRET_PATH.exists():
        secret = JWT_SECRET_PATH.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    secret = secrets.token_urlsafe(48)
    JWT_SECRET_PATH.write_text(secret, encoding="utf-8")
    try:
        os.chmod(JWT_SECRET_PATH, 0o600)
    except OSError:
        pass
    return secret

