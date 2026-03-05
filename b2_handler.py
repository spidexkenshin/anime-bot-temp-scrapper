"""
Backblaze B2 handler using b2sdk
All heavy IO runs in executor so it doesn't block the event loop.
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

    # ── Internal: lazy init (runs in executor) ─────────────────

    def _ensure_connected(self):
        if self._api is None:
            info = InMemoryAccountInfo()
            self._api = B2Api(info)
            self._api.authorize_account("production", self.key_id, self.application_key)
            self._bucket = self._api.get_bucket_by_name(self.bucket_name)
            logger.info("✅ Backblaze B2 connected.")

    # ── Upload ─────────────────────────────────────────────────

    def upload_file(self, local_path: str, b2_key: str) -> str:
        """
        Upload a local file to B2.
        Returns the download URL (empty string on failure).
        """
        try:
            self._ensure_connected()
            file_info = self._bucket.upload_local_file(
                local_file=local_path,
                file_name=b2_key,
                content_type="video/mp4",
            )
            url = self._api.get_download_url_for_file_name(self.bucket_name, b2_key)
            logger.info(f"[B2] Uploaded {b2_key} → {url}")
            return url
        except b2exc.B2Error as e:
            logger.error(f"[B2] Upload error ({b2_key}): {e}")
            return ""
        except Exception as e:
            logger.error(f"[B2] Unexpected upload error: {e}")
            return ""

    # ── Delete ─────────────────────────────────────────────────

    def delete_file(self, b2_key: str) -> bool:
        """
        Delete a file from B2 by its key (path).
        Returns True on success.
        """
        try:
            self._ensure_connected()
            # List file versions and delete all
            for file_version, _ in self._bucket.ls(b2_key, latest_only=False):
                self._bucket.delete_file_version(file_version.id_, file_version.file_name)
                logger.info(f"[B2] Deleted {b2_key}")
            return True
        except b2exc.B2Error as e:
            logger.warning(f"[B2] Delete error ({b2_key}): {e}")
            return False
        except Exception as e:
            logger.warning(f"[B2] Unexpected delete error: {e}")
            return False

    # ── Health check ───────────────────────────────────────────

    def is_available(self) -> bool:
        return bool(self.key_id and self.application_key and self.bucket_name)
