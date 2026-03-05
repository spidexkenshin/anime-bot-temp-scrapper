import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Telegram ──────────────────────────────────────────────
    API_ID: int = int(os.getenv("API_ID", 0))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # ── Owner & Storage Group ─────────────────────────────────
    OWNER_ID: int = int(os.getenv("OWNER_ID", 0))
    STORAGE_GROUP_ID: int = int(os.getenv("STORAGE_GROUP_ID", 0))

    # ── Backblaze B2 ──────────────────────────────────────────
    B2_KEY_ID: str = os.getenv("B2_KEY_ID", "")
    B2_APPLICATION_KEY: str = os.getenv("B2_APPLICATION_KEY", "")
    B2_BUCKET_NAME: str = os.getenv("B2_BUCKET_NAME", "anime-bot-temp")

    # ── Scraper ───────────────────────────────────────────────
    ANIME_SITE: str = "https://animesalt.top"
    SCRAPER_HEADERS: dict = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    # ── Quality order ─────────────────────────────────────────
    QUALITIES_ORDER: list = ["360p", "480p", "720p", "1080p"]

    # ── Misc ──────────────────────────────────────────────────
    DOWNLOAD_PATH: str = "/tmp/anime_dl"
    CHUNK_SIZE: int = 1024 * 512  # 512 KB

    # ── Default Caption ───────────────────────────────────────
    DEFAULT_CAPTION: str = (
        "<b><blockquote> ✨ {anime} ✨</blockquote>\n"
        "‣ Episode : {ep}\n"
        "‣ Season : {season}\n"
        "‣ Quality : {quality}\n"
        "‣ Audio : {audio} | Official🎙️\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>🚀 For More Join: [@KENSHIN_ANIME &amp; MANWHA_VERSE]</blockquote>\n"
        "━━━━━━━━━━━━━━━━━━━━━━</b>"
    )
