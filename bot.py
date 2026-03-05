"""
KenshinAnimeBot — animesalt.top scraper bot
Personal project only. All files in root folder.
"""

import asyncio
import logging
import os
import re

import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from b2_handler import B2Handler
from config import Config
from database import db
from queue_system import QueueManager
from scraper import get_episodes, get_seasons, get_video_links, search_anime

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Init clients ──────────────────────────────────────────────
app = Client(
    "KenshinAnimeBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

b2 = B2Handler(Config.B2_KEY_ID, Config.B2_APPLICATION_KEY, Config.B2_BUCKET_NAME)
queue_mgr = QueueManager()

# ── In-memory session state ───────────────────────────────────
# { user_id: { search_results, selected_anime, seasons, download_plan, ... } }
sessions: dict = {}

os.makedirs(Config.DOWNLOAD_PATH, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

async def is_admin(user_id: int) -> bool:
    if user_id == Config.OWNER_ID:
        return True
    return await db.is_admin(user_id)


async def safe_edit(msg: Message, text: str, markup=None, parse_mode=ParseMode.MARKDOWN):
    """Edit message, silently ignore MessageNotModified."""
    try:
        kwargs = {"text": text, "parse_mode": parse_mode}
        if markup:
            kwargs["reply_markup"] = markup
        await msg.edit(**kwargs)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        try:
            await msg.edit(**kwargs)
        except Exception:
            pass
    except Exception as ex:
        logger.warning(f"safe_edit: {ex}")


def build_progress_bar(current: int, total: int) -> str:
    if total == 0:
        return "`[░░░░░░░░░░░░░░░░░░░░]` 0%"
    pct = (current * 100) / total
    filled = int(pct / 5)
    bar = "█" * filled + "░" * (20 - filled)
    mb_done = current / (1024 * 1024)
    mb_total = total / (1024 * 1024)
    return f"`[{bar}]` **{pct:.1f}%**\n**{mb_done:.1f} MB** / **{mb_total:.1f} MB**"


def admin_check(func):
    """Decorator — blocks non-admins."""
    async def wrapper(client, update, *args, **kwargs):
        uid = (update.from_user.id if hasattr(update, "from_user") and update.from_user else 0)
        if not await is_admin(uid):
            if isinstance(update, Message):
                await update.reply_text("❌ You are not authorized.")
            elif isinstance(update, CallbackQuery):
                await update.answer("❌ Not authorized!", show_alert=True)
            return
        return await func(client, update, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


# ═══════════════════════════════════════════════════════════════
#  START / HELP
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, msg: Message):
    await msg.reply_text(
        "🎌 **KenshinAnimeBot**\n\n"
        "Powered by `@KENSHIN_ANIME` & `MANWHA_VERSE`\n\n"
        "Personal anime scraper bot.\n"
        "Type /help to see all commands.",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("help"))
@admin_check
async def help_cmd(client, msg: Message):
    text = (
        "🎌 **KenshinAnimeBot — Commands**\n\n"
        "**🔍 Anime**\n"
        "• `/anime <name>` — Search & download anime\n"
        "• `/status` — Queue status\n"
        "• `/clearqueue` — Clear the download queue _(owner)_\n\n"
        "**✏️ Settings**\n"
        "• `/setcaption` — Set custom caption (reply to text or pass inline)\n"
        "• `/resetcaption` — Restore default caption\n"
        "• `/setthumb` — Reply to a photo to set thumbnail\n"
        "• `/resetthumb` — Remove custom thumbnail\n"
        "• `/showcaption` — View current caption template\n\n"
        "**👥 Admins** _(owner only)_\n"
        "• `/addadmin <user_id>` — Add admin\n"
        "• `/deladmin <user_id>` — Remove admin\n"
        "• `/admins` — List admins\n\n"
        "**📌 Caption Variables:**\n"
        "`{anime}` `{ep}` `{season}` `{quality}` `{audio}`"
    )
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ═══════════════════════════════════════════════════════════════
#  ADMIN MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("addadmin"))
async def add_admin_cmd(client, msg: Message):
    if msg.from_user.id != Config.OWNER_ID:
        await msg.reply_text("❌ Only the owner can add admins.")
        return
    if len(msg.command) < 2:
        await msg.reply_text("❌ Usage: `/addadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(msg.command[1])
        added = await db.add_admin(uid, msg.from_user.id)
        if added:
            await msg.reply_text(f"✅ User `{uid}` is now an admin.", parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(f"ℹ️ User `{uid}` is already an admin.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await msg.reply_text("❌ Invalid user ID (must be a number).")


@app.on_message(filters.command("deladmin"))
async def del_admin_cmd(client, msg: Message):
    if msg.from_user.id != Config.OWNER_ID:
        await msg.reply_text("❌ Only the owner can remove admins.")
        return
    if len(msg.command) < 2:
        await msg.reply_text("❌ Usage: `/deladmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(msg.command[1])
        removed = await db.remove_admin(uid)
        if removed:
            await msg.reply_text(f"✅ User `{uid}` removed from admins.", parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(f"ℹ️ User `{uid}` was not an admin.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await msg.reply_text("❌ Invalid user ID.")


@app.on_message(filters.command("admins"))
@admin_check
async def list_admins_cmd(client, msg: Message):
    admins = await db.get_admins()
    lines = [f"👑 **Owner:** `{Config.OWNER_ID}`\n\n**Admins:**"]
    if not admins:
        lines.append("_None added yet._")
    for a in admins:
        lines.append(f"• `{a['user_id']}` — added by `{a['added_by']}`")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ═══════════════════════════════════════════════════════════════
#  CAPTION & THUMBNAIL
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("setcaption"))
@admin_check
async def set_caption_cmd(client, msg: Message):
    caption = ""
    if msg.reply_to_message:
        caption = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if len(msg.command) > 1:
        caption = msg.text.split(None, 1)[1]

    if not caption:
        await msg.reply_text(
            "❌ Provide caption inline or reply to a text message.\n\n"
            "**Variables:** `{anime}` `{ep}` `{season}` `{quality}` `{audio}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await db.set_setting("caption", caption)
    await msg.reply_text(
        f"✅ **Caption saved!**\n\n**Preview:**\n{caption}",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("resetcaption"))
@admin_check
async def reset_caption_cmd(client, msg: Message):
    await db.delete_setting("caption")
    await msg.reply_text("✅ Caption reset to default.")


@app.on_message(filters.command("showcaption"))
@admin_check
async def show_caption_cmd(client, msg: Message):
    cap = await db.get_setting("caption") or Config.DEFAULT_CAPTION
    await msg.reply_text(f"📝 **Current Caption Template:**\n\n`{cap}`", parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("setthumb"))
@admin_check
async def set_thumb_cmd(client, msg: Message):
    target = msg.reply_to_message or msg
    if target.photo:
        fid = target.photo.file_id
        await db.set_setting("thumbnail", fid)
        await msg.reply_text("✅ Thumbnail set!")
    else:
        await msg.reply_text("❌ Reply to a photo to set it as thumbnail.")


@app.on_message(filters.command("resetthumb"))
@admin_check
async def reset_thumb_cmd(client, msg: Message):
    await db.delete_setting("thumbnail")
    await msg.reply_text("✅ Thumbnail removed.")


# ═══════════════════════════════════════════════════════════════
#  QUEUE STATUS
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("status"))
@admin_check
async def status_cmd(client, msg: Message):
    s = queue_mgr.get_status()
    await msg.reply_text(
        f"📊 **Queue Status**\n\n"
        f"• Queue size : `{s['queue_size']}`\n"
        f"• Processing : `{'Yes ⚙️' if s['is_processing'] else 'No 💤'}`\n"
        f"• Current    : `{s['current_task']}`",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("clearqueue"))
async def clear_queue_cmd(client, msg: Message):
    if msg.from_user.id != Config.OWNER_ID:
        await msg.reply_text("❌ Owner only.")
        return
    queue_mgr.clear()
    await msg.reply_text("✅ Queue cleared.")


# ═══════════════════════════════════════════════════════════════
#  /anime — SEARCH FLOW
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("anime"))
@admin_check
async def anime_cmd(client, msg: Message):
    if len(msg.command) < 2:
        await msg.reply_text(
            "❌ Usage: `/anime <name>`\nExample: `/anime Solo Leveling`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    query = " ".join(msg.command[1:])
    status = await msg.reply_text(f"🔍 Searching for **{query}**...", parse_mode=ParseMode.MARKDOWN)

    results = await asyncio.get_event_loop().run_in_executor(None, search_anime, query)

    if not results:
        await safe_edit(status, f"❌ No results found for **{query}**.\nTry a different spelling.")
        return

    uid = msg.from_user.id
    sessions[uid] = {"search_results": results, "query": query}

    buttons = []
    for i, r in enumerate(results[:8]):
        title = r["title"][:45] + ("…" if len(r["title"]) > 45 else "")
        buttons.append([InlineKeyboardButton(f"🎬 {title}", callback_data=f"AS:{i}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel")])

    await safe_edit(
        status,
        f"🔍 **Results for:** `{query}`\n\nFound **{len(results)}** anime. Select one:",
        markup=InlineKeyboardMarkup(buttons),
    )


# ── Anime selected ────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^AS:(\d+)$"))
@admin_check
async def cb_anime_select(client, cq: CallbackQuery):
    uid = cq.from_user.id
    idx = int(cq.data.split(":")[1])
    sess = sessions.get(uid, {})
    results = sess.get("search_results", [])

    if idx >= len(results):
        await cq.answer("❌ Invalid", show_alert=True)
        return

    anime = results[idx]
    sess["selected_anime"] = anime
    sessions[uid] = sess

    await safe_edit(cq.message, f"⏳ Fetching seasons for **{anime['title']}**...")

    seasons = await asyncio.get_event_loop().run_in_executor(None, get_seasons, anime["url"])
    sess["seasons"] = seasons
    sessions[uid] = sess

    buttons = []
    for num, data in sorted(seasons.items()):
        buttons.append([InlineKeyboardButton(f"📺 {data['name']}", callback_data=f"SS:{num}")])
    if len(seasons) > 1:
        buttons.append([InlineKeyboardButton("🌟 All Seasons", callback_data="SS:all")])
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="SS:back"),
        InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel"),
    ])

    await safe_edit(
        cq.message,
        f"🎌 **{anime['title']}**\n\nFound **{len(seasons)}** season(s). Select:",
        markup=InlineKeyboardMarkup(buttons),
    )
    await cq.answer()


@app.on_callback_query(filters.regex(r"^AS:cancel$"))
async def cb_cancel(client, cq: CallbackQuery):
    sessions.pop(cq.from_user.id, None)
    await safe_edit(cq.message, "❌ **Cancelled.**")
    await cq.answer()


@app.on_callback_query(filters.regex(r"^SS:back$"))
async def cb_back_to_search(client, cq: CallbackQuery):
    uid = cq.from_user.id
    sess = sessions.get(uid, {})
    results = sess.get("search_results", [])
    query = sess.get("query", "")
    if not results:
        await safe_edit(cq.message, "❌ Session expired. Search again.")
        await cq.answer()
        return
    buttons = []
    for i, r in enumerate(results[:8]):
        title = r["title"][:45] + ("…" if len(r["title"]) > 45 else "")
        buttons.append([InlineKeyboardButton(f"🎬 {title}", callback_data=f"AS:{i}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel")])
    await safe_edit(
        cq.message,
        f"🔍 **Results for:** `{query}`\n\nSelect one:",
        markup=InlineKeyboardMarkup(buttons),
    )
    await cq.answer()


# ── Season selected ───────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^SS:(.+)$"))
@admin_check
async def cb_season_select(client, cq: CallbackQuery):
    uid = cq.from_user.id
    val = cq.data.split(":", 1)[1]
    if val in ("back", "cancel"):
        return  # Handled by other handlers

    sess = sessions.get(uid, {})
    seasons = sess.get("seasons", {})
    anime = sess.get("selected_anime", {})

    await safe_edit(cq.message, "⏳ Fetching episode list...")

    try:
        if val == "all":
            download_plan = {}
            for num, data in sorted(seasons.items()):
                eps = await asyncio.get_event_loop().run_in_executor(
                    None, get_episodes, data["url"]
                )
                download_plan[num] = {"name": data["name"], "episodes": eps}
        else:
            num = int(val)
            data = seasons[num]
            eps = await asyncio.get_event_loop().run_in_executor(
                None, get_episodes, data["url"]
            )
            download_plan = {num: {"name": data["name"], "episodes": eps}}

        sess["download_plan"] = download_plan
        sessions[uid] = sess

        total_eps = sum(len(v["episodes"]) for v in download_plan.values())
        season_label = "All Seasons" if val == "all" else seasons[int(val)]["name"]

        buttons = [
            [InlineKeyboardButton("✅ Start Download", callback_data="DL:confirm")],
            [
                InlineKeyboardButton("🔙 Back", callback_data="SS:back"),
                InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel"),
            ],
        ]

        await safe_edit(
            cq.message,
            f"📋 **Download Plan**\n\n"
            f"🎌 Anime   : **{anime['title']}**\n"
            f"📺 Season  : **{season_label}**\n"
            f"🎬 Episodes: **{total_eps}**\n"
            f"🎞 Qualities: 360p → 480p → 720p → 1080p\n\n"
            f"📤 Videos → Storage Group → You\n"
            f"☁️ B2 temp storage → deleted after send\n\n"
            f"Ready to start?",
            markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        logger.error(f"Season select error: {e}")
        await safe_edit(cq.message, f"❌ Error fetching episodes:\n`{e}`")

    await cq.answer()


# ── Confirm download ──────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^DL:confirm$"))
@admin_check
async def cb_confirm_download(client, cq: CallbackQuery):
    uid = cq.from_user.id
    sess = sessions.get(uid, {})

    if not sess.get("download_plan"):
        await cq.answer("❌ Session expired. Search again.", show_alert=True)
        return

    added = await queue_mgr.add_to_queue({
        "user_id": uid,
        "chat_id": cq.message.chat.id,
        "anime": sess["selected_anime"],
        "download_plan": sess["download_plan"],
    })

    sessions.pop(uid, None)

    if added:
        await safe_edit(
            cq.message,
            "✅ **Queued!** Download will start shortly.\n\nUse /status to check progress.",
        )
    else:
        await safe_edit(cq.message, "❌ Queue is full. Wait for current tasks to finish.")

    await cq.answer()


# ═══════════════════════════════════════════════════════════════
#  CORE DOWNLOAD + SEND PROCESSOR
# ═══════════════════════════════════════════════════════════════

async def _download_file(url: str, path: str, progress_msg: Message, label: str) -> bool:
    """Async download with live progress bar."""
    try:
        timeout = aiohttp.ClientTimeout(total=3600)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                headers={
                    "User-Agent": Config.SCRAPER_HEADERS["User-Agent"],
                    "Referer": Config.ANIME_SITE,
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[DL] HTTP {resp.status} for {url}")
                    return False

                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_pct = -1

                with open(path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(Config.CHUNK_SIZE):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = int((downloaded * 100) / total)
                            if pct >= last_pct + 5:
                                last_pct = pct
                                bar = build_progress_bar(downloaded, total)
                                await safe_edit(
                                    progress_msg,
                                    f"⬇️ **{label}**\n\n{bar}",
                                )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[DL] Download error: {e}")
        return False


async def _send_to_storage(
    client: Client,
    file_path: str,
    caption: str,
    thumb_fid,
    progress_msg: Message,
    label: str,
) -> Message | None:
    """Upload video to Telegram storage group with progress."""
    last_pct = [-1]

    async def _progress(current, total):
        pct = int((current * 100) / total)
        if pct >= last_pct[0] + 5:
            last_pct[0] = pct
            bar = build_progress_bar(current, total)
            await safe_edit(progress_msg, f"📤 **Uploading: {label}**\n\n{bar}")

    try:
        kwargs = {
            "chat_id": Config.STORAGE_GROUP_ID,
            "video": file_path,
            "caption": caption,
            "parse_mode": ParseMode.HTML,
            "progress": _progress,
        }
        if thumb_fid:
            kwargs["thumb"] = thumb_fid

        return await client.send_video(**kwargs)
    except Exception as e:
        logger.error(f"[STORAGE] send_video error: {e}")
        return None


async def process_download_task(task: dict):
    """
    Main task processor called by QueueManager.
    Flow per episode-quality:
      scrape URL → download → B2 upload → Telegram storage group → forward to admin → B2 delete
    """
    client = app
    uid = task["user_id"]
    chat_id = task["chat_id"]
    anime = task["anime"]
    download_plan: dict = task["download_plan"]

    caption_tmpl = await db.get_setting("caption") or Config.DEFAULT_CAPTION
    thumb_fid = await db.get_setting("thumbnail")

    progress_msg = await client.send_message(
        chat_id,
        f"🚀 **Starting:** {anime['title']}\n\n_Fetching video links..._",
        parse_mode=ParseMode.MARKDOWN,
    )

    total_ok = 0
    total_fail = 0

    for season_num, season_data in sorted(download_plan.items()):
        season_name = season_data["name"]
        episodes = season_data["episodes"]

        for episode in episodes:
            ep_num = episode["number"]
            ep_url = episode["url"]

            await safe_edit(
                progress_msg,
                f"🔍 **Fetching links...**\n"
                f"🎌 {anime['title']}\n"
                f"📺 {season_name} | Episode {ep_num}",
            )

            try:
                video_links: dict = await asyncio.get_event_loop().run_in_executor(
                    None, get_video_links, ep_url
                )
            except Exception as e:
                logger.error(f"get_video_links error: {e}")
                total_fail += 1
                continue

            if not video_links:
                logger.warning(f"No video links for S{season_num}E{ep_num}")
                total_fail += 1
                continue

            # Send qualities in defined order: 360p, 480p, 720p, 1080p
            for quality in Config.QUALITIES_ORDER:
                if quality not in video_links:
                    continue

                video_url = video_links[quality]
                safe_title = re.sub(r'[\\/*?:"<>|]', "_", anime["title"])[:25]
                file_name = f"{safe_title}_S{season_num:02d}E{ep_num:03d}_{quality}.mp4"
                local_path = os.path.join(Config.DOWNLOAD_PATH, file_name)
                b2_key = f"temp/{file_name}"
                label = f"{anime['title']} S{season_num}E{ep_num} {quality}"

                try:
                    # ── Step 1: Download ──
                    await safe_edit(progress_msg, f"⬇️ **Downloading...**\n🎌 {label}\n\n`[░░░░░░░░░░░░░░░░░░░░]` 0%")
                    ok = await _download_file(video_url, local_path, progress_msg, label)
                    if not ok:
                        total_fail += 1
                        continue

                    # ── Step 2: B2 Upload (temp buffer) ──
                    b2_url = ""
                    if b2.is_available():
                        await safe_edit(progress_msg, f"☁️ **Uploading to B2...**\n🎌 {label}")
                        b2_url = await asyncio.get_event_loop().run_in_executor(
                            None, b2.upload_file, local_path, b2_key
                        )

                    # ── Step 3: Build caption ──
                    caption = caption_tmpl.format(
                        anime=anime["title"],
                        ep=ep_num,
                        season=season_num,
                        quality=quality,
                        audio="Japanese",
                    )

                    # ── Step 4: Send to storage group ──
                    storage_msg = await _send_to_storage(
                        client, local_path, caption, thumb_fid, progress_msg, label
                    )

                    # ── Step 5: Forward to admin ──
                    if storage_msg:
                        try:
                            await client.forward_messages(
                                chat_id=chat_id,
                                from_chat_id=Config.STORAGE_GROUP_ID,
                                message_ids=storage_msg.id,
                            )
                            total_ok += 1
                        except Exception as e:
                            logger.error(f"Forward error: {e}")
                            total_fail += 1
                    else:
                        total_fail += 1

                    # ── Step 6: Delete from B2 ──
                    if b2_url and b2.is_available():
                        await asyncio.get_event_loop().run_in_executor(
                            None, b2.delete_file, b2_key
                        )

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Task error [{label}]: {e}")
                    total_fail += 1
                finally:
                    if os.path.exists(local_path):
                        os.remove(local_path)

                await asyncio.sleep(3)  # Cooldown between uploads

    # ── Final summary ──
    await safe_edit(
        progress_msg,
        f"✅ **Finished: {anime['title']}**\n\n"
        f"✔️ Sent    : **{total_ok}** videos\n"
        f"❌ Failed  : **{total_fail}** videos",
    )


# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

async def main():
    await db.connect()
    queue_mgr.set_processor(process_download_task)
    queue_mgr.set_client(app)

    logger.info("Starting KenshinAnimeBot...")
    await app.start()
    me = await app.get_me()
    logger.info(f"✅ Bot started as @{me.username}")

    # Start queue worker
    asyncio.create_task(queue_mgr.process_queue())

    # Keep alive
    await asyncio.Event().wait()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
