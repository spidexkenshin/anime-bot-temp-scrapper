"""
storage.py — No MongoDB needed.
Data is stored in bot_data.json locally.
On startup: tries to restore from B2 backup.
On every write: saves to B2 so data survives Railway redeploys.
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

DATA_FILE = "/app/bot_data.json"
B2_CONFIG_KEY = "config/bot_data.json"

# Default structure
_DEFAULT = {
    "admins": [],        # list of user_id ints
    "caption": None,     # custom caption string or null
    "thumbnail": None,   # telegram file_id string or null
}

_data: dict = {}
_b2 = None  # will be injected after import


def init_storage(b2_instance):
    """Call this at startup, passing the B2Handler instance."""
    global _b2
    _b2 = b2_instance


def _load_local() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[Storage] Could not read local data: {e}")
    return {}


def _save_local():
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_data, f, indent=2)
    except Exception as e:
        logger.error(f"[Storage] Could not save local data: {e}")


def _save_b2():
    """Backup config to B2 so it survives Railway redeploys."""
    if _b2 and _b2.is_available():
        try:
            # Write to temp file first, then upload
            tmp = "/tmp/bot_data_backup.json"
            with open(tmp, "w") as f:
                json.dump(_data, f, indent=2)
            _b2.upload_file(tmp, B2_CONFIG_KEY)
            os.remove(tmp)
        except Exception as e:
            logger.warning(f"[Storage] B2 backup failed (non-critical): {e}")


def _restore_from_b2() -> dict:
    """Try to restore config from B2 on startup."""
    if _b2 and _b2.is_available():
        try:
            tmp = "/tmp/bot_data_restore.json"
            ok = _b2.download_file(B2_CONFIG_KEY, tmp)
            if ok and os.path.exists(tmp):
                with open(tmp, "r") as f:
                    data = json.load(f)
                os.remove(tmp)
                logger.info("[Storage] ✅ Config restored from B2!")
                return data
        except Exception as e:
            logger.info(f"[Storage] No B2 backup found (first run?): {e}")
    return {}


def load():
    """Load data — local file first, B2 backup if local missing."""
    global _data
    local = _load_local()
    if local:
        _data = {**_DEFAULT, **local}
        logger.info("[Storage] ✅ Data loaded from local file.")
    else:
        b2data = _restore_from_b2()
        _data = {**_DEFAULT, **b2data}
        if b2data:
            _save_local()  # cache B2 data locally
        else:
            logger.info("[Storage] Starting with fresh data (no backup found).")
    return _data


def _save():
    _save_local()
    _save_b2()


# ── Admin Management ──────────────────────────────────────────

def add_admin(user_id: int) -> bool:
    if user_id in _data["admins"]:
        return False
    _data["admins"].append(user_id)
    _save()
    return True


def remove_admin(user_id: int) -> bool:
    if user_id not in _data["admins"]:
        return False
    _data["admins"].remove(user_id)
    _save()
    return True


def is_admin(user_id: int, owner_id: int) -> bool:
    return user_id == owner_id or user_id in _data["admins"]


def get_admins() -> list:
    return list(_data.get("admins", []))


# ── Caption ───────────────────────────────────────────────────

def set_caption(text: str):
    _data["caption"] = text
    _save()


def get_caption() -> str | None:
    return _data.get("caption")


def reset_caption():
    _data["caption"] = None
    _save()


# ── Thumbnail ─────────────────────────────────────────────────

def set_thumbnail(file_id: str):
    _data["thumbnail"] = file_id
    _save()


def get_thumbnail() -> str | None:
    return _data.get("thumbnail")


def reset_thumbnail():
    _data["thumbnail"] = None
    _save()
