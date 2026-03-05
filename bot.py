"""
KenshinAnimeBot — animesalt.top scraper bot
Personal project only. No MongoDB — uses local JSON + B2 backup.
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

import storage
from b2_handler import B2Handler
from config import Config
from queue_system import QueueManager
from scraper import get_episodes, get_seasons, get_video_links, search_anime

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Init ──────────────────────────────────────────────────────
app = Client(
    "KenshinAnimeBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

b2 = B2Handler(Config.B2_KEY_ID, Config.B2_APPLICATION_KEY, Config.B2_BUCKET_NAME)
queue_mgr = QueueManager()

# In-memory session state per user
sessions: dict = {}

os.makedirs(Config.DOWNLOAD_PATH, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _is_admin(user_id: int) -> bool:
    return storage.is_admin(user_id, Config.OWNER_ID)


async def safe_edit(msg: Message, text: str, markup=None, parse_mode=ParseMode.MARKDOWN):
    try:
        kw = {"text": text, "parse_mode": parse_mode}
        if markup:
            kw["reply_markup"] = markup
        await msg.edit(**kw)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        try:
            await msg.edit(**kw)
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


def admin_only(func):
    """Decorator — blocks non-admins silently or with a message."""
    async def wrapper(client, update, *args, **kwargs):
        uid = (update.from_user.id
               if hasattr(update, "from_user") and update.from_user else 0)
        if not _is_admin(uid):
            if isinstance(update, Message):
                await update.reply_text("❌ You are not authorized to use this bot.")
            elif isinstance(update, CallbackQuery):
                await update.answer("❌ Not authorized!", show_alert=True)
            return
        return await func(client, update, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


# ═══════════════════════════════════════════════════════════════
#  /start  /help
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
@admin_only
async def help_cmd(client, msg: Message):
    text = (
        "🎌 **KenshinAnimeBot — Commands**\n\n"
        "**🔍 Anime**\n"
        "• `/anime <name>` — Search & send anime episodes\n"
        "• `/status` — Download queue status\n"
        "• `/clearqueue` — Clear queue _(owner only)_\n\n"
        "**✏️ Customise**\n"
        "• `/setcaption <text>` — Set custom caption\n"
        "  _(or reply to a text message)_\n"
        "• `/resetcaption` — Restore default caption\n"
        "• `/showcaption` — Preview current caption\n"
        "• `/setthumb` — Reply to photo → set thumbnail\n"
        "• `/resetthumb` — Remove thumbnail\n\n"
        "**👥 Admins** _(owner only)_\n"
        "• `/addadmin <user_id>` — Add admin\n"
        "• `/deladmin <user_id>` — Remove admin\n"
        "• `/admins` — List all admins\n\n"
        "**📌 Caption Variables**\n"
        "`{anime}` `{ep}` `{season}` `{quality}` `{audio}`"
    )
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ═══════════════════════════════════════════════════════════════
#  ADMIN MANAGEMENT  (owner only)
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
        if storage.add_admin(uid):
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
        if storage.remove_admin(uid):
            await msg.reply_text(f"✅ User `{uid}` removed from admins.", parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(f"ℹ️ User `{uid}` was not an admin.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await msg.reply_text("❌ Invalid user ID.")


@app.on_message(filters.command("admins"))
@admin_only
async def list_admins_cmd(client, msg: Message):
    admins = storage.get_admins()
    lines = [f"👑 **Owner:** `{Config.OWNER_ID}`\n\n**Admins:**"]
    if not admins:
        lines.append("_None added yet._")
    for uid in admins:
        lines.append(f"• `{uid}`")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ═══════════════════════════════════════════════════════════════
#  CAPTION & THUMBNAIL
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("setcaption"))
@admin_only
async def set_caption_cmd(client, msg: Message):
    caption = ""
    if msg.reply_to_message:
        caption = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if len(msg.text.split(None, 1)) > 1:
        caption = msg.text.split(None, 1)[1]

    if not caption.strip():
        await msg.reply_text(
            "❌ Usage: `/setcaption <your caption text>`\n"
            "Or reply to a text message with `/setcaption`\n\n"
            "**Variables:** `{anime}` `{ep}` `{season}` `{quality}` `{audio}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    storage.set_caption(caption)
    await msg.reply_text(
        f"✅ **Caption saved!**\n\n**Preview:**\n{caption}",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("resetcaption"))
@admin_only
async def reset_caption_cmd(client, msg: Message):
    storage.reset_caption()
    await msg.reply_text("✅ Caption reset to default.")


@app.on_message(filters.command("showcaption"))
@admin_only
async def show_caption_cmd(client, msg: Message):
    cap = storage.get_caption() or Config.DEFAULT_CAPTION
    label = "Custom" if storage.get_caption() else "Default"
    await msg.reply_text(
        f"📝 **Current Caption ({label}):**\n\n`{cap}`",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("setthumb"))
@admin_only
async def set_thumb_cmd(client, msg: Message):
    target = msg.reply_to_message or msg
    if target.photo:
        fid = target.photo.file_id
        storage.set_thumbnail(fid)
        await msg.reply_text("✅ Thumbnail set!")
    else:
        await msg.reply_text("❌ Reply to a photo with /setthumb to set it as thumbnail.")


@app.on_message(filters.command("resetthumb"))
@admin_only
async def reset_thumb_cmd(client, msg: Message):
    storage.reset_thumbnail()
    await msg.reply_text("✅ Thumbnail removed.")


# ═══════════════════════════════════════════════════════════════
#  QUEUE STATUS
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("status"))
@admin_only
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
#  /anime SEARCH FLOW
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("anime"))
@admin_only
async def anime_cmd(client, msg: Message):
    if len(msg.command) < 2:
        await msg.reply_text(
            "❌ Usage: `/anime <name>`\n\nExample: `/anime Solo Leveling`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    query = " ".join(msg.command[1:])
    status = await msg.reply_text(
        f"🔍 Searching for **{query}**...",
        parse_mode=ParseMode.MARKDOWN,
    )

    results = await asyncio.get_event_loop().run_in_executor(
        None, search_anime, query
    )

    if not results:
        await safe_edit(status, f"❌ No results found for **{query}**.\n\nTry a different spelling.")
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
        f"🔍 **Results for:** `{query}`\n\nFound **{len(results)}** — select one:",
        markup=InlineKeyboardMarkup(buttons),
    )


@app.on_callback_query(filters.regex(r"^AS:(\d+)$"))
@admin_only
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

    seasons = await asyncio.get_event_loop().run_in_executor(
        None, get_seasons, anime["url"]
    )
    sess["seasons"] = seasons
    sessions[uid] = sess

    buttons = []
    for num, data in sorted(seasons.items()):
        buttons.append([InlineKeyboardButton(
            f"📺 {data['name']}", callback_data=f"SS:{num}"
        )])
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
async def cb_back(client, cq: CallbackQuery):
    uid = cq.from_user.id
    sess = sessions.get(uid, {})
    results = sess.get("search_results", [])
    query = sess.get("query", "")
    if not results:
        await safe_edit(cq.message, "❌ Session expired. Search again with /anime")
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


@app.on_callback_query(filters.regex(r"^SS:(.+)$"))
@admin_only
async def cb_season_select(client, cq: CallbackQuery):
    uid = cq.from_user.id
    val = cq.data.split(":", 1)[1]
    if val in ("back", "cancel"):
        return  # handled by separate handlers

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
        total_files = total_eps * len(Config.QUALITIES_ORDER)

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
            f"🎌 Anime    : **{anime['title']}**\n"
            f"📺 Season   : **{season_label}**\n"
            f"🎬 Episodes : **{total_eps}**\n"
            f"📁 Files    : **~{total_files}** (360p→480p→720p→1080p)\n\n"
            f"📤 Flow: Download → Storage Group → You\n"
            f"☁️ B2 used as temp buffer, deleted after send\n\n"
            f"Ready?",
            markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        logger.error(f"Season select error: {e}")
        await safe_edit(cq.message, f"❌ Error fetching episodes:\n`{e}`")

    await cq.answer()


@app.on_callback_query(filters.regex(r"^DL:confirm$"))
@admin_only
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
        s = queue_mgr.get_status()
        pos = s["queue_size"]
        await safe_edit(
            cq.message,
            f"✅ **Added to queue!**\n\n"
            f"Queue position: `{pos}`\n"
            f"Use /status to track progress.",
        )
    else:
        await safe_edit(cq.message, "❌ Queue is full (max 50). Wait for current tasks.")

    await cq.answer()


# ═══════════════════════════════════════════════════════════════
#  DOWNLOAD + SEND PROCESSOR
# ═══════════════════════════════════════════════════════════════

async def _download_file(url: str, path: str, progress_msg: Message, label: str) -> bool:
    """Async stream download with live progress bar."""
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
                    logger.warning(f"[DL] HTTP {resp.status} → {url}")
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
                                    f"⬇️ **Downloading**\n`{label}`\n\n{bar}",
                                )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[DL] Error: {e}")
        return False


async def _send_to_storage(
    client: Client,
    file_path: str,
    caption: str,
    thumb_fid,
    progress_msg: Message,
    label: str,
) -> Message | None:
    """Upload video to Telegram storage group."""
    last_pct = [-1]

    async def _progress(current, total):
        pct = int((current * 100) / total)
        if pct >= last_pct[0] + 5:
            last_pct[0] = pct
            bar = build_progress_bar(current, total)
            await safe_edit(progress_msg, f"📤 **Uploading to Storage**\n`{label}`\n\n{bar}")

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
    Main task processor.
    Per episode per quality:
      scrape → download → B2 temp → Telegram storage group → forward to admin → B2 delete
    """
    uid = task["user_id"]
    chat_id = task["chat_id"]
    anime = task["anime"]
    download_plan: dict = task["download_plan"]

    caption_tmpl = storage.get_caption() or Config.DEFAULT_CAPTION
    thumb_fid = storage.get_thumbnail()

    progress_msg = await app.send_message(
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
                f"🔍 **Getting video links...**\n"
                f"🎌 {anime['title']}\n"
                f"📺 {season_name} · Episode {ep_num}",
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
                logger.warning(f"No links found for S{season_num}E{ep_num}")
                total_fail += 1
                continue

            # Process qualities in order: 360p → 480p → 720p → 1080p
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
                    # ── 1. Download ───────────────────────────
                    await safe_edit(
                        progress_msg,
                        f"⬇️ **Downloading**\n`{label}`\n\n`[░░░░░░░░░░░░░░░░░░░░]` 0%",
                    )
                    ok = await _download_file(video_url, local_path, progress_msg, label)
                    if not ok:
                        total_fail += 1
                        continue

                    # ── 2. B2 temp upload ─────────────────────
                    if b2.is_available():
                        await safe_edit(
                            progress_msg,
                            f"☁️ **Buffering on B2...**\n`{label}`",
                        )
                        await asyncio.get_event_loop().run_in_executor(
                            None, b2.upload_file, local_path, b2_key
                        )

                    # ── 3. Build caption ──────────────────────
                    caption = caption_tmpl.format(
                        anime=anime["title"],
                        ep=ep_num,
                        season=season_num,
                        quality=quality,
                        audio="Japanese",
                    )

                    # ── 4. Send to Storage Group ──────────────
                    storage_msg = await _send_to_storage(
                        app, local_path, caption, thumb_fid, progress_msg, label
                    )

                    # ── 5. Forward to admin ───────────────────
                    if storage_msg:
                        try:
                            await app.forward_messages(
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

                    # ── 6. Delete from B2 ─────────────────────
                    if b2.is_available():
                        await asyncio.get_event_loop().run_in_executor(
                            None, b2.delete_file, b2_key
                        )

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Task error [{label}]: {e}")
                    total_fail += 1
                finally:
                    # Always clean up local file
                    if os.path.exists(local_path):
                        os.remove(local_path)

                await asyncio.sleep(2)  # Small cooldown between uploads

    await safe_edit(
        progress_msg,
        f"✅ **Finished!**\n\n"
        f"🎌 **{anime['title']}**\n\n"
        f"✔️ Sent    : **{total_ok}** videos\n"
        f"❌ Failed  : **{total_fail}** videos",
    )


# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

async def main():
    # Init B2 first (needed by storage for config backup)
    storage.init_storage(b2)

    # Load persisted data (admins, caption, thumbnail)
    storage.load()

    # Register queue processor
    queue_mgr.set_processor(process_download_task)
    queue_mgr.set_client(app)

    logger.info("Starting KenshinAnimeBot...")
    await app.start()
    me = await app.get_me()
    logger.info(f"✅ Bot started as @{me.username}")

    # Start queue worker loop
    asyncio.create_task(queue_mgr.process_queue())

    # Keep alive forever
    await asyncio.Event().wait()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
