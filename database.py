import logging
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.client = None
        self.db = None

    async def connect(self):
        try:
            self.client = AsyncIOMotorClient(Config.MONGODB_URI)
            self.db = self.client[Config.DB_NAME]
            # Verify connection
            await self.client.admin.command("ping")
            logger.info("✅ MongoDB connected successfully!")
        except Exception as e:
            logger.critical(f"❌ MongoDB connection failed: {e}")
            raise

    # ── Admin Management ──────────────────────────────────────

    async def add_admin(self, user_id: int, added_by: int) -> bool:
        try:
            existing = await self.db.admins.find_one({"user_id": user_id})
            if existing:
                return False  # Already admin
            await self.db.admins.insert_one({
                "user_id": user_id,
                "added_by": added_by,
                "added_at": datetime.utcnow()
            })
            return True
        except Exception as e:
            logger.error(f"add_admin error: {e}")
            return False

    async def remove_admin(self, user_id: int) -> bool:
        try:
            result = await self.db.admins.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"remove_admin error: {e}")
            return False

    async def is_admin(self, user_id: int) -> bool:
        try:
            if user_id == Config.OWNER_ID:
                return True
            doc = await self.db.admins.find_one({"user_id": user_id})
            return doc is not None
        except Exception as e:
            logger.error(f"is_admin error: {e}")
            return False

    async def get_admins(self) -> list:
        try:
            cursor = self.db.admins.find({})
            return await cursor.to_list(length=100)
        except Exception as e:
            logger.error(f"get_admins error: {e}")
            return []

    # ── Settings (caption, thumbnail) ─────────────────────────

    async def set_setting(self, key: str, value: str):
        try:
            await self.db.settings.update_one(
                {"key": key},
                {"$set": {"key": key, "value": value, "updated_at": datetime.utcnow()}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"set_setting error: {e}")

    async def get_setting(self, key: str):
        try:
            doc = await self.db.settings.find_one({"key": key})
            return doc["value"] if doc else None
        except Exception as e:
            logger.error(f"get_setting error: {e}")
            return None

    async def delete_setting(self, key: str):
        try:
            await self.db.settings.delete_one({"key": key})
        except Exception as e:
            logger.error(f"delete_setting error: {e}")


# Singleton instance
db = Database()
