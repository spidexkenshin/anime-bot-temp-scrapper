"""
KenshinAnimeBot — animesalt.top scraper bot
New flow:
  /anime <name> → results with languages
  → Select anime → seasons with episode count
  → Select season / All / individual episode
  → Downloads + sends all qualities
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
from scraper import (
    get_anime_detail,
    get_episodes,
    get_video_links,
    search_anime,
)

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

# Session state per user
sessions: dict = {}

os.makedirs(Config.DOWNLOAD_PATH, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _is_admin(uid: int) -> bool:
    return storage.is_admin(uid, Config.OWNER_ID)


async def safe_edit(msg: Message, text: str, markup=None,
                    parse_mode=ParseMode.MARKDOWN):
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


def progress_bar(current: int, total: int) -> str:
    if total == 0:
        return "`[░░░░░░░░░░░░░░░░░░░░]` 0%"
    pct = (current * 100) / total
    filled = int(pct / 5)
    bar = "█" * filled + "░" * (20 - filled)
    mb_d = current / 1048576
    mb_t = total / 1048576
    return f"`[{bar}]` **{pct:.1f}%**  {mb_d:.1f}/{mb_t:.1f} MB"


def admin_only(func):
    async def wrapper(client, update, *args, **kwargs):
        uid = getattr(getattr(update, "from_user", None), "id", 0)
        if not _is_admin(uid):
            if isinstance(update, Message):
                await update.reply_text("❌ Not authorized.")
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
        "Personal anime scraper — /help for commands.",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("help"))
@admin_only
async def help_cmd(client, msg: Message):
    await msg.reply_text(
        "🎌 **KenshinAnimeBot Commands**\n\n"
        "**🔍 Search & Download**\n"
        "• `/anime <name>` — Search anime\n"
        "• `/status` — Queue status\n"
        "• `/clearqueue` — Clear queue _(owner)_\n\n"
        "**🛠 Debug**\n"
        "• `/debug <url>` — Show raw page info\n"
        "• `/testurl <url>` — Test video link extraction\n\n"
        "**✏️ Customise**\n"
        "• `/setcaption <text>` — Set caption\n"
        "• `/resetcaption` — Default caption\n"
        "• `/showcaption` — View caption\n"
        "• `/setthumb` — Reply to photo\n"
        "• `/resetthumb` — Remove thumbnail\n\n"
        "**👥 Admins** _(owner only)_\n"
        "• `/addadmin <id>` `/deladmin <id>` `/admins`\n\n"
        "**Caption vars:** `{anime}` `{ep}` `{season}` `{quality}` `{audio}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════
#  DEBUG TOOLS
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("debug"))
@admin_only
async def debug_cmd(client, msg: Message):
    """Fetch a URL and show its structure to diagnose scraper issues."""
    if len(msg.command) < 2:
        await msg.reply_text(
            "Usage: `/debug <url>`\n"
            "Example: `/debug https://animesalt.top/?s=solo+leveling`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    url = msg.command[1]
    status = await msg.reply_text(f"🔍 Fetching: `{url}`...", parse_mode=ParseMode.MARKDOWN)

    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": Config.SCRAPER_HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml",
            "Referer": Config.ANIME_SITE,
        }
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        title = soup.title.string if soup.title else "N/A"
        articles = len(soup.find_all("article"))
        links = soup.find_all("a", href=True)
        anime_links = [
            a["href"] for a in links
            if Config.ANIME_SITE.replace("https://", "") in a.get("href", "")
        ][:10]

        # Find all unique CSS classes used
        all_classes = set()
        for el in soup.find_all(class_=True)[:50]:
            for c in el.get("class", []):
                all_classes.add(c)
        class_sample = ", ".join(list(all_classes)[:20])

        info = (
            f"📊 **Debug Report**\n\n"
            f"**URL:** `{resp.url}`\n"
            f"**Status:** `{resp.status_code}`\n"
            f"**Page title:** `{title[:80]}`\n"
            f"**Page size:** `{len(resp.text)} chars`\n"
            f"**Articles found:** `{articles}`\n\n"
            f"**Anime links ({len(anime_links)}):**\n"
        )
        for lnk in anime_links[:8]:
            info += f"• `{lnk[:70]}`\n"

        info += f"\n**CSS classes sample:**\n`{class_sample[:200]}`"

        await safe_edit(status, info)

    except Exception as e:
        await safe_edit(status, f"❌ Error: `{e}`")


@app.on_message(filters.command("testurl"))
@admin_only
async def testurl_cmd(client, msg: Message):
    """Test video link extraction for a specific episode URL."""
    if len(msg.command) < 2:
        await msg.reply_text("Usage: `/testurl <episode_url>`", parse_mode=ParseMode.MARKDOWN)
        return

    url = msg.command[1]
    status = await msg.reply_text(f"🔍 Testing video extraction for:\n`{url}`...", parse_mode=ParseMode.MARKDOWN)

    try:
        links = await asyncio.get_event_loop().run_in_executor(None, get_video_links, url)
        if links:
            text = f"✅ **Found {len(links)} quality links:**\n\n"
            for q, u in sorted(links.items()):
                text += f"• **{q}:** `{u[:80]}`\n"
        else:
            text = "❌ No video links found.\n\nTry `/debug <url>` to see page structure."
        await safe_edit(status, text)
    except Exception as e:
        await safe_edit(status, f"❌ Error: `{e}`")


# ═══════════════════════════════════════════════════════════════
#  ADMIN MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("addadmin"))
async def add_admin_cmd(client, msg: Message):
    if msg.from_user.id != Config.OWNER_ID:
        return await msg.reply_text("❌ Owner only.")
    if len(msg.command) < 2:
        return await msg.reply_text("Usage: `/addadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
    try:
        uid = int(msg.command[1])
        if storage.add_admin(uid):
            await msg.reply_text(f"✅ `{uid}` is now admin.", parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(f"ℹ️ `{uid}` already admin.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await msg.reply_text("❌ Invalid user ID.")


@app.on_message(filters.command("deladmin"))
async def del_admin_cmd(client, msg: Message):
    if msg.from_user.id != Config.OWNER_ID:
        return await msg.reply_text("❌ Owner only.")
    if len(msg.command) < 2:
        return await msg.reply_text("Usage: `/deladmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
    try:
        uid = int(msg.command[1])
        if storage.remove_admin(uid):
            await msg.reply_text(f"✅ `{uid}` removed.", parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(f"ℹ️ `{uid}` was not admin.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await msg.reply_text("❌ Invalid user ID.")


@app.on_message(filters.command("admins"))
@admin_only
async def list_admins_cmd(client, msg: Message):
    admins = storage.get_admins()
    text = f"👑 **Owner:** `{Config.OWNER_ID}`\n\n**Admins:**\n"
    text += "\n".join(f"• `{uid}`" for uid in admins) if admins else "_None_"
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


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
        return await msg.reply_text(
            "❌ Usage: `/setcaption <text>`\n\n"
            "Variables: `{anime}` `{ep}` `{season}` `{quality}` `{audio}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    storage.set_caption(caption)
    await msg.reply_text(f"✅ **Caption saved!**\n\n{caption}", parse_mode=ParseMode.MARKDOWN)


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
    await msg.reply_text(f"📝 **{label} Caption:**\n\n`{cap}`", parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("setthumb"))
@admin_only
async def set_thumb_cmd(client, msg: Message):
    target = msg.reply_to_message or msg
    if target and target.photo:
        storage.set_thumbnail(target.photo.file_id)
        await msg.reply_text("✅ Thumbnail set!")
    else:
        await msg.reply_text("❌ Reply to a photo with /setthumb")


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
        f"• Queued    : `{s['queue_size']}`\n"
        f"• Processing: `{'Yes ⚙️' if s['is_processing'] else 'No 💤'}`\n"
        f"• Current   : `{s['current_task']}`",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("clearqueue"))
async def clear_queue_cmd(client, msg: Message):
    if msg.from_user.id != Config.OWNER_ID:
        return await msg.reply_text("❌ Owner only.")
    queue_mgr.clear()
    await msg.reply_text("✅ Queue cleared.")


# ═══════════════════════════════════════════════════════════════
#  /anime  — SEARCH
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("anime"))
@admin_only
async def anime_cmd(client, msg: Message):
    if len(msg.command) < 2:
        return await msg.reply_text(
            "❌ Usage: `/anime <name>`\nExample: `/anime Solo Leveling`",
            parse_mode=ParseMode.MARKDOWN,
        )

    query = " ".join(msg.command[1:])
    status = await msg.reply_text(
        f"🔍 Searching **{query}** on animesalt.top...",
        parse_mode=ParseMode.MARKDOWN,
    )

    results = await asyncio.get_event_loop().run_in_executor(None, search_anime, query)

    if not results:
        await safe_edit(
            status,
            f"❌ **No results for:** `{query}`\n\n"
            f"**Possible fixes:**\n"
            f"• Try `/debug https://animesalt.top/?s={query.replace(' ', '+')}`\n"
            f"  to see what the site returns\n"
            f"• Check the site manually to confirm anime name",
        )
        return

    uid = msg.from_user.id
    sessions[uid] = {"results": results, "query": query}

    buttons = []
    for i, r in enumerate(results[:8]):
        title = r["title"][:40]
        langs = r.get("languages", [])
        lang_tag = f" [{', '.join(langs[:3])}]" if langs else ""
        label = f"🎬 {title}{lang_tag}"
        buttons.append([InlineKeyboardButton(label[:60], callback_data=f"AS:{i}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel")])

    await safe_edit(
        status,
        f"🔍 **Results for:** `{query}`\n\nFound **{len(results)}** — select:",
        markup=InlineKeyboardMarkup(buttons),
    )


@app.on_callback_query(filters.regex(r"^AS:cancel$"))
async def cb_cancel(client, cq: CallbackQuery):
    sessions.pop(cq.from_user.id, None)
    await safe_edit(cq.message, "❌ Cancelled.")
    await cq.answer()


@app.on_callback_query(filters.regex(r"^AS:(\d+)$"))
@admin_only
async def cb_anime_select(client, cq: CallbackQuery):
    uid = cq.from_user.id
    idx = int(cq.data.split(":")[1])
    sess = sessions.get(uid, {})
    results = sess.get("results", [])

    if idx >= len(results):
        return await cq.answer("❌ Invalid", show_alert=True)

    anime = results[idx]
    sess["anime"] = anime
    sessions[uid] = sess

    await safe_edit(cq.message, f"⏳ Loading **{anime['title']}**...")

    detail = await asyncio.get_event_loop().run_in_executor(
        None, get_anime_detail, anime["url"]
    )
    sess["detail"] = detail
    sessions[uid] = sess

    seasons = detail.get("seasons", {})
    langs = detail.get("languages", anime.get("languages", []))
    lang_str = f"\n🌐 Languages: {', '.join(langs)}" if langs else ""

    buttons = []
    for num, data in sorted(seasons.items()):
        ep_count = data.get("episode_count", 0)
        ep_str = f" ({ep_count} eps)" if ep_count else ""
        buttons.append([InlineKeyboardButton(
            f"📺 {data['name']}{ep_str}", callback_data=f"SN:{num}"
        )])

    if len(seasons) > 1:
        buttons.append([InlineKeyboardButton("🌟 All Seasons", callback_data="SN:all")])

    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="SN:back"),
        InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel"),
    ])

    await safe_edit(
        cq.message,
        f"🎌 **{detail.get('title') or anime['title']}**{lang_str}\n\n"
        f"**{len(seasons)}** season(s) found — select:",
        markup=InlineKeyboardMarkup(buttons),
    )
    await cq.answer()


# ── Back button ───────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^SN:back$"))
async def cb_back(client, cq: CallbackQuery):
    uid = cq.from_user.id
    sess = sessions.get(uid, {})
    results = sess.get("results", [])
    query = sess.get("query", "")
    if not results:
        await safe_edit(cq.message, "❌ Session expired. Search again.")
        await cq.answer()
        return
    buttons = []
    for i, r in enumerate(results[:8]):
        title = r["title"][:40]
        langs = r.get("languages", [])
        lang_tag = f" [{', '.join(langs[:3])}]" if langs else ""
        buttons.append([InlineKeyboardButton(f"🎬 {title}{lang_tag}"[:60], callback_data=f"AS:{i}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel")])
    await safe_edit(
        cq.message,
        f"🔍 **Results for:** `{query}`\n\nSelect:",
        markup=InlineKeyboardMarkup(buttons),
    )
    await cq.answer()


# ── Season selected ───────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^SN:(.+)$"))
@admin_only
async def cb_season_select(client, cq: CallbackQuery):
    uid = cq.from_user.id
    val = cq.data.split(":", 1)[1]
    if val in ("back",):
        return

    sess = sessions.get(uid, {})
    detail = sess.get("detail", {})
    anime = sess.get("anime", {})
    seasons = detail.get("seasons", {})
    title = detail.get("title") or anime.get("title", "Unknown")

    await safe_edit(cq.message, f"⏳ Loading episodes for **{title}**...")

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

        if total_eps == 0:
            await safe_edit(
                cq.message,
                f"❌ No episodes found!\n\n"
                f"Try `/debug {data['url']}` to check page structure.",
            )
            await cq.answer()
            return

        season_label = "All Seasons" if val == "all" else seasons[int(val)]["name"]

        # Build episode buttons (show individual eps if only 1 season selected and ≤ 24 eps)
        buttons = []
        if val != "all" and total_eps <= 24:
            # Show individual episode buttons
            num_val = int(val)
            eps_list = download_plan[num_val]["episodes"]
            row = []
            for ep in eps_list:
                row.append(InlineKeyboardButton(
                    f"Ep {ep['number']}", callback_data=f"EP:{num_val}:{ep['number']}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

        buttons.append([InlineKeyboardButton(
            f"📥 Download All ({total_eps} eps × 4 qualities)",
            callback_data="DL:confirm"
        )])
        buttons.append([
            InlineKeyboardButton("🔙 Back", callback_data=f"SN:back_season"),
            InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel"),
        ])

        await safe_edit(
            cq.message,
            f"📋 **{title}**\n\n"
            f"📺 Season  : **{season_label}**\n"
            f"🎬 Episodes: **{total_eps}**\n"
            f"🎞 Quality : 360p → 480p → 720p → 1080p\n\n"
            f"Select episode or download all:",
            markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        logger.error(f"Season select error: {e}")
        await safe_edit(cq.message, f"❌ Error: `{e}`")

    await cq.answer()


# ── Back to season list ───────────────────────────────────────

@app.on_callback_query(filters.regex(r"^SN:back_season$"))
async def cb_back_to_seasons(client, cq: CallbackQuery):
    uid = cq.from_user.id
    sess = sessions.get(uid, {})
    detail = sess.get("detail", {})
    anime = sess.get("anime", {})
    seasons = detail.get("seasons", {})
    langs = detail.get("languages", [])
    lang_str = f"\n🌐 {', '.join(langs)}" if langs else ""
    title = detail.get("title") or anime.get("title", "")

    buttons = []
    for num, data in sorted(seasons.items()):
        ep_count = data.get("episode_count", 0)
        ep_str = f" ({ep_count} eps)" if ep_count else ""
        buttons.append([InlineKeyboardButton(
            f"📺 {data['name']}{ep_str}", callback_data=f"SN:{num}"
        )])
    if len(seasons) > 1:
        buttons.append([InlineKeyboardButton("🌟 All Seasons", callback_data="SN:all")])
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="SN:back"),
        InlineKeyboardButton("❌ Cancel", callback_data="AS:cancel"),
    ])

    await safe_edit(
        cq.message,
        f"🎌 **{title}**{lang_str}\n\n**{len(seasons)}** season(s) — select:",
        markup=InlineKeyboardMarkup(buttons),
    )
    await cq.answer()


# ── Individual episode selected ───────────────────────────────

@app.on_callback_query(filters.regex(r"^EP:(\d+):(\d+)$"))
@admin_only
async def cb_episode_select(client, cq: CallbackQuery):
    uid = cq.from_user.id
    parts = cq.data.split(":")
    season_num = int(parts[1])
    ep_num = int(parts[2])

    sess = sessions.get(uid, {})
    dp = sess.get("download_plan", {})
    anime = sess.get("anime", {})
    detail = sess.get("detail", {})
    title = detail.get("title") or anime.get("title", "Unknown")

    # Build single-episode download plan
    season_eps = dp.get(season_num, {}).get("episodes", [])
    ep_data = next((e for e in season_eps if e["number"] == ep_num), None)

    if not ep_data:
        await cq.answer("❌ Episode not found", show_alert=True)
        return

    single_plan = {season_num: {
        "name": dp[season_num]["name"],
        "episodes": [ep_data],
    }}

    added = await queue_mgr.add_to_queue({
        "user_id": uid,
        "chat_id": cq.message.chat.id,
        "anime": {"title": title, "url": anime.get("url", "")},
        "download_plan": single_plan,
    })

    if added:
        await safe_edit(
            cq.message,
            f"✅ **Queued!**\n\n"
            f"🎌 {title}\n"
            f"📺 {dp[season_num]['name']} — Episode {ep_num}\n"
            f"🎞 All qualities will be sent\n\n"
            f"Use /status to track.",
        )
    else:
        await safe_edit(cq.message, "❌ Queue full. Try later.")
    await cq.answer()


# ── Confirm full download ─────────────────────────────────────

@app.on_callback_query(filters.regex(r"^DL:confirm$"))
@admin_only
async def cb_confirm_dl(client, cq: CallbackQuery):
    uid = cq.from_user.id
    sess = sessions.get(uid, {})

    if not sess.get("download_plan"):
        return await cq.answer("❌ Session expired.", show_alert=True)

    anime = sess.get("anime", {})
    detail = sess.get("detail", {})
    title = detail.get("title") or anime.get("title", "Unknown")

    added = await queue_mgr.add_to_queue({
        "user_id": uid,
        "chat_id": cq.message.chat.id,
        "anime": {"title": title, "url": anime.get("url", "")},
        "download_plan": sess["download_plan"],
    })
    sessions.pop(uid, None)

    if added:
        s = queue_mgr.get_status()
        await safe_edit(
            cq.message,
            f"✅ **Added to queue!**\n\n"
            f"Position: `{s['queue_size']}`\n"
            f"Use /status to track.",
        )
    else:
        await safe_edit(cq.message, "❌ Queue full (max 50). Wait and retry.")
    await cq.answer()


# ═══════════════════════════════════════════════════════════════
#  DOWNLOAD PROCESSOR
# ═══════════════════════════════════════════════════════════════

async def _download_file(url: str, path: str, prog_msg: Message, label: str) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=3600)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(
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
                done = 0
                last = -1

                with open(path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(Config.CHUNK_SIZE):
                        f.write(chunk)
                        done += len(chunk)
                        if total > 0:
                            pct = int((done * 100) / total)
                            if pct >= last + 5:
                                last = pct
                                bar = progress_bar(done, total)
                                await safe_edit(prog_msg, f"⬇️ **Downloading**\n`{label}`\n\n{bar}")
        return True
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[DL] Error: {e}")
        return False


async def _send_video(client, path: str, caption: str, thumb, prog_msg: Message, label: str):
    last = [-1]

    async def _prog(current, total):
        pct = int((current * 100) / total)
        if pct >= last[0] + 5:
            last[0] = pct
            bar = progress_bar(current, total)
            await safe_edit(prog_msg, f"📤 **Uploading to Storage**\n`{label}`\n\n{bar}")

    try:
        kw = {
            "chat_id": Config.STORAGE_GROUP_ID,
            "video": path,
            "caption": caption,
            "parse_mode": ParseMode.HTML,
            "progress": _prog,
        }
        if thumb:
            kw["thumb"] = thumb
        return await client.send_video(**kw)
    except Exception as e:
        logger.error(f"[UPLOAD] Error: {e}")
        return None


async def process_download_task(task: dict):
    uid = task["user_id"]
    chat_id = task["chat_id"]
    anime = task["anime"]
    plan: dict = task["download_plan"]

    caption_tmpl = storage.get_caption() or Config.DEFAULT_CAPTION
    thumb = storage.get_thumbnail()

    prog_msg = await app.send_message(
        chat_id,
        f"🚀 **Starting:** {anime['title']}\n_Preparing..._",
        parse_mode=ParseMode.MARKDOWN,
    )

    total_ok = total_fail = 0

    for season_num, season_data in sorted(plan.items()):
        season_name = season_data["name"]
        for episode in season_data["episodes"]:
            ep_num = episode["number"]
            ep_url = episode["url"]

            await safe_edit(
                prog_msg,
                f"🔍 **Getting links...**\n"
                f"🎌 {anime['title']} · {season_name} · Ep {ep_num}",
            )

            try:
                links = await asyncio.get_event_loop().run_in_executor(
                    None, get_video_links, ep_url
                )
            except Exception as e:
                logger.error(f"get_video_links failed: {e}")
                total_fail += 1
                continue

            if not links:
                logger.warning(f"No links: S{season_num}E{ep_num}")
                total_fail += 1
                continue

            for quality in Config.QUALITIES_ORDER:
                if quality not in links:
                    continue

                safe_t = re.sub(r'[\\/*?:"<>|]', "_", anime["title"])[:20]
                fname = f"{safe_t}_S{season_num:02d}E{ep_num:03d}_{quality}.mp4"
                fpath = os.path.join(Config.DOWNLOAD_PATH, fname)
                b2_key = f"temp/{fname}"
                label = f"{anime['title']} S{season_num}E{ep_num} {quality}"

                try:
                    # 1. Download
                    ok = await _download_file(links[quality], fpath, prog_msg, label)
                    if not ok:
                        total_fail += 1
                        continue

                    # 2. B2 temp buffer
                    if b2.is_available():
                        await safe_edit(prog_msg, f"☁️ **B2 buffer...**\n`{label}`")
                        await asyncio.get_event_loop().run_in_executor(
                            None, b2.upload_file, fpath, b2_key
                        )

                    # 3. Caption
                    caption = caption_tmpl.format(
                        anime=anime["title"], ep=ep_num,
                        season=season_num, quality=quality, audio="Japanese"
                    )

                    # 4. Send to storage group
                    storage_msg = await _send_video(app, fpath, caption, thumb, prog_msg, label)

                    # 5. Forward to admin
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

                    # 6. Delete from B2
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
                    if os.path.exists(fpath):
                        os.remove(fpath)

                await asyncio.sleep(2)

    await safe_edit(
        prog_msg,
        f"✅ **Done! — {anime['title']}**\n\n"
        f"✔️ Sent  : **{total_ok}** videos\n"
        f"❌ Failed: **{total_fail}** videos",
    )


# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

async def main():
    storage.init_storage(b2)
    storage.load()

    queue_mgr.set_processor(process_download_task)
    queue_mgr.set_client(app)

    logger.info("Starting KenshinAnimeBot...")
    await app.start()
    me = await app.get_me()
    logger.info(f"✅ Bot live as @{me.username}")

    asyncio.create_task(queue_mgr.process_queue())
    await asyncio.Event().wait()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Stopped.")
