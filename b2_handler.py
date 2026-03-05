"""
Backblaze B2 handler using b2sdk
Sync methods — run in executor to avoid blocking event loop.
"""

import logging
import os
from b2sdk.v2 import InMemoryAccountInfo, B2Api, exception as b2exc

logger = logging.getLogger(__name__)


class B2Handler:
    def __init__(self, key_id: str, application_key: str, bucket_name: str):
        self.key_id = key_id
        self.application_key = application_key
        self.bucket_name = bucket_name
        self._api = None
        self._bucket = None

    def _ensure_connected(self):
        if self._api is None:
            info = InMemoryAccountInfo()
            self._api = B2Api(info)
            self._api.authorize_account("production", self.key_id, self.application_key)
            self._bucket = self._api.get_bucket_by_name(self.bucket_name)
            logger.info("✅ Backblaze B2 connected.")

    # ── Upload ─────────────────────────────────────────────────

    def upload_file(self, local_path: str, b2_key: str) -> str:
        """Upload a local file to B2. Returns download URL or empty string."""
        try:
            self._ensure_connected()
            self._bucket.upload_local_file(
                local_file=local_path,
                file_name=b2_key,
            )
            url = self._api.get_download_url_for_file_name(self.bucket_name, b2_key)
            logger.info(f"[B2] Uploaded {b2_key}")
            return url
        except b2exc.B2Error as e:
            logger.error(f"[B2] Upload error ({b2_key}): {e}")
            return ""
        except Exception as e:
            logger.error(f"[B2] Unexpected upload error: {e}")
            return ""

    # ── Download ───────────────────────────────────────────────

    def download_file(self, b2_key: str, local_path: str) -> bool:
        """Download a file from B2 to local_path. Returns True on success."""
        try:
            self._ensure_connected()
            downloaded = self._bucket.download_file_by_name(b2_key)
            downloaded.save_to(local_path)
            return True
        except b2exc.FileNotPresent:
            return False
        except b2exc.B2Error as e:
            logger.warning(f"[B2] Download error ({b2_key}): {e}")
            return False
        except Exception as e:
            logger.warning(f"[B2] Unexpected download error: {e}")
            return False

    # ── Delete ─────────────────────────────────────────────────

    def delete_file(self, b2_key: str) -> bool:
        """Delete all versions of a file from B2. Returns True on success."""
        try:
            self._ensure_connected()
            deleted = False
            for file_version, _ in self._bucket.ls(b2_key, latest_only=False):
                self._bucket.delete_file_version(
                    file_version.id_, file_version.file_name
                )
                deleted = True
                logger.info(f"[B2] Deleted {b2_key}")
            return deleted
        except b2exc.B2Error as e:
            logger.warning(f"[B2] Delete error ({b2_key}): {e}")
            return False
        except Exception as e:
            logger.warning(f"[B2] Unexpected delete error: {e}")
            return False

    # ── Health check ───────────────────────────────────────────

    def is_available(self) -> bool:
        return bool(self.key_id and self.application_key and self.bucket_name)
