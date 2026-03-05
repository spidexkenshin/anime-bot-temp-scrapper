"""
scraper.py — animesalt.top specific scraper
Tries multiple patterns to handle different page structures.
Has verbose logging so /debug command can show what's happening.
"""

import json
import logging
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus

logger = logging.getLogger(__name__)

BASE = "https://animesalt.top"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def _get(url: str, referer: str = BASE) -> requests.Response:
    h = {**HEADERS, "Referer": referer}
    r = requests.get(url, headers=h, timeout=20, allow_redirects=True)
    r.raise_for_status()
    return r


def _soup(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "lxml")


def _abs(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return urljoin(BASE, url)


# ── SEARCH ────────────────────────────────────────────────────

def search_anime(query: str) -> list:
    """
    Returns list of:
    { title, url, thumbnail, languages: [] }
    """
    results = []
    debug_info = []

    # Try multiple search URL patterns
    search_urls = [
        f"{BASE}/?s={quote_plus(query)}",
        f"{BASE}/search/{quote_plus(query)}/",
        f"{BASE}/?s={quote_plus(query)}&post_type=post",
        f"{BASE}/?s={quote_plus(query)}&post_type=series",
    ]

    html_text = ""
    used_url = ""

    for search_url in search_urls:
        try:
            resp = _get(search_url)
            if resp.status_code == 200 and len(resp.text) > 500:
                html_text = resp.text
                used_url = search_url
                debug_info.append(f"✅ Got response from: {search_url} (len={len(resp.text)})")
                break
            else:
                debug_info.append(f"⚠️ {search_url} → status={resp.status_code}, len={len(resp.text)}")
        except Exception as e:
            debug_info.append(f"❌ {search_url} → {e}")

    if not html_text:
        logger.warning(f"[Scraper] All search URLs failed for: {query}")
        logger.warning("\n".join(debug_info))
        return []

    soup = _soup(requests.Response())
    soup = BeautifulSoup(html_text, "lxml")

    logger.info(f"[Scraper] Searching from: {used_url}")
    logger.info(f"[Scraper] Page title: {soup.title.string if soup.title else 'N/A'}")

    # ── Method 1: article tags (most common WordPress anime theme) ──
    articles = soup.find_all("article")
    if articles:
        debug_info.append(f"Found {len(articles)} <article> tags")
        for art in articles:
            result = _parse_card(art, soup)
            if result:
                results.append(result)

    # ── Method 2: div.item / div.ml-item ──
    if not results:
        for sel in [".ml-item", ".item", ".movies-list .item",
                    ".film_list-wrap .flw-item", ".TPost", ".result-item",
                    "div[class*='item']", "li[class*='item']"]:
            items = soup.select(sel)
            if items:
                debug_info.append(f"Found {len(items)} items with selector: {sel}")
                for item in items:
                    result = _parse_card(item, soup)
                    if result:
                        results.append(result)
                if results:
                    break

    # ── Method 3: Any link that looks like an anime page ──
    if not results:
        debug_info.append("Falling back to link scanning...")
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            # Skip nav/pagination/category links
            if not href or href in ("#", "/"):
                continue
            if any(x in href for x in ["/category/", "/tag/", "/page/", "wp-", "?p=", "feed"]):
                continue
            # Must be on animesalt.top
            if BASE.replace("https://", "") not in href and not href.startswith("/"):
                continue
            text = a.get("title") or a.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            href_abs = _abs(href)
            if href_abs in seen:
                continue
            seen.add(href_abs)
            # Get thumbnail from nearby img
            img = a.find("img") or (a.parent.find("img") if a.parent else None)
            thumb = ""
            if img:
                thumb = img.get("data-src") or img.get("data-lazy-src") or img.get("src", "")
            results.append({
                "title": text[:80],
                "url": href_abs,
                "thumbnail": thumb,
                "languages": [],
            })

    logger.info(f"[Scraper] Found {len(results)} results. Debug: {debug_info}")

    # Deduplicate
    seen, unique = set(), []
    for r in results:
        if r["url"] not in seen and len(r["title"]) > 1:
            seen.add(r["url"])
            unique.append(r)

    return unique[:10]


def _parse_card(el, soup) -> dict | None:
    """Extract anime info from a card element."""
    a = el.find("a", href=True)
    if not a:
        return None
    href = _abs(a.get("href", ""))
    if not href or BASE.replace("https://", "") not in href:
        return None

    # Title
    title_el = (el.select_one("h1, h2, h3, .title, .name, .film-name, .entry-title")
                or el.select_one("[class*='title'], [class*='name']"))
    title = ""
    if title_el:
        title = title_el.get_text(strip=True)
    if not title:
        title = a.get("title") or a.get_text(strip=True)
    if not title:
        return None

    # Thumbnail
    img = el.find("img")
    thumb = ""
    if img:
        thumb = (img.get("data-src") or img.get("data-lazy-src") or
                 img.get("data-original") or img.get("src", ""))

    # Languages — look for language badges/tags
    languages = _extract_languages(el)

    return {"title": title, "url": href, "thumbnail": thumb, "languages": languages}


def _extract_languages(el) -> list:
    """Try to find language info from card element."""
    langs = []
    text = el.get_text(" ", strip=True).lower()

    lang_map = {
        "english": "English", "hindi": "Hindi", "japanese": "Japanese",
        "tamil": "Tamil", "telugu": "Telugu", "dubbed": "Dubbed",
        "sub": "Subbed", "multi": "Multi-Audio", "korean": "Korean",
        "chinese": "Chinese",
    }
    for key, label in lang_map.items():
        if key in text:
            langs.append(label)

    # Also check dedicated language span/badge elements
    for badge in el.select(".badge, .label, [class*='lang'], [class*='audio'], span"):
        t = badge.get_text(strip=True).lower()
        for key, label in lang_map.items():
            if key in t and label not in langs:
                langs.append(label)

    return langs


# ── ANIME DETAIL PAGE (seasons + episodes) ───────────────────

def get_anime_detail(anime_url: str) -> dict:
    """
    Returns:
    {
      title, thumbnail, languages,
      seasons: {
        1: { name, url, episode_count },
        2: { name, url, episode_count },
        ...
      }
    }
    """
    detail = {
        "title": "",
        "thumbnail": "",
        "languages": [],
        "seasons": {},
    }

    try:
        resp = _get(anime_url)
        soup = BeautifulSoup(resp.text, "lxml")

        # Title
        for sel in ["h1.entry-title", "h1", ".TPost h1", ".Title", ".film-name"]:
            el = soup.select_one(sel)
            if el:
                detail["title"] = el.get_text(strip=True)
                break

        # Thumbnail
        for sel in [".TPostMv img", ".film-poster img", ".entry-thumbnail img",
                    "meta[property='og:image']", ".cover img"]:
            el = soup.select_one(sel)
            if el:
                detail["thumbnail"] = el.get("content") or el.get("src", "")
                if detail["thumbnail"]:
                    break

        # Languages
        detail["languages"] = _extract_languages(soup)

        # ── Find seasons ──────────────────────────────────────
        seasons = {}

        # Pattern 1: Season tabs / dropdowns
        season_selectors = [
            ".SeasonBx a", ".seasons-list a", ".tab-seasons a",
            "ul.dropdown-menu a[href*='season']",
            ".TpTbs a", "[id*='season'] a", ".sbox a",
            "a[href*='/season-']", "a[href*='season=']",
            ".episodes-list-container a[data-season]",
        ]
        for sel in season_selectors:
            items = soup.select(sel)
            if items:
                for item in items:
                    href = _abs(item.get("href", anime_url))
                    text = item.get_text(strip=True)
                    m = re.search(r"(\d+)", text)
                    num = int(m.group(1)) if m else len(seasons) + 1
                    seasons[num] = {"name": text or f"Season {num}", "url": href, "episode_count": 0}
                break

        # Pattern 2: Season in page URL structure
        if not seasons:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                m = re.search(r"/season[-_/](\d+)|[?&]season=(\d+)", href, re.I)
                if m:
                    num = int(m.group(1) or m.group(2))
                    text = a.get_text(strip=True) or f"Season {num}"
                    seasons[num] = {"name": text, "url": _abs(href), "episode_count": 0}

        # If no season structure, current page IS the only season
        if not seasons:
            # Count episodes on this page
            eps = _get_episodes_from_soup(soup, anime_url)
            seasons[1] = {
                "name": "Season 1",
                "url": anime_url,
                "episode_count": len(eps),
            }
        else:
            # Get episode count for each season
            for num, data in seasons.items():
                try:
                    if data["url"] == anime_url:
                        eps = _get_episodes_from_soup(soup, anime_url)
                    else:
                        s_resp = _get(data["url"])
                        s_soup = BeautifulSoup(s_resp.text, "lxml")
                        eps = _get_episodes_from_soup(s_soup, data["url"])
                    data["episode_count"] = len(eps)
                except Exception:
                    data["episode_count"] = 0

        detail["seasons"] = dict(sorted(seasons.items()))

    except Exception as e:
        logger.error(f"[Scraper] get_anime_detail error: {e}")

    return detail


# ── EPISODES ─────────────────────────────────────────────────

def get_episodes(season_url: str) -> list:
    """Returns sorted list of { number, title, url }"""
    try:
        resp = _get(season_url)
        soup = BeautifulSoup(resp.text, "lxml")
        return _get_episodes_from_soup(soup, season_url)
    except Exception as e:
        logger.error(f"[Scraper] get_episodes error: {e}")
        return []


def _get_episodes_from_soup(soup: BeautifulSoup, page_url: str) -> list:
    episodes = []

    ep_selectors = [
        "ul.episodios li", "ul.episodes-list li", ".episode-list li",
        "#episodios-lista li", ".AACrdn li", "div.episodios li",
        ".ep-list li", ".episodes-grid a", "ul.Lst li",
        "[class*='episode'] a", "[class*='ep-item']",
        "table.episodes tr",
    ]

    for sel in ep_selectors:
        items = soup.select(sel)
        if items:
            for item in items:
                a = item.select_one("a[href]") or (item if item.name == "a" else None)
                if not a:
                    continue
                href = _abs(a.get("href", ""))
                if not href:
                    continue
                text = item.get_text(" ", strip=True)
                # Extract episode number
                m = re.search(r"(?:episode|ep|episodio|eps?)[\s\-_#]*(\d+)", text, re.I)
                if not m:
                    m = re.search(r"(\d+)(?:\s*$|(?=\s*[-–]))", href.rstrip("/").split("/")[-1])
                if not m:
                    m = re.search(r"\b(\d{1,4})\b", text)
                num = int(m.group(1)) if m else len(episodes) + 1
                episodes.append({"number": num, "title": text or f"Episode {num}", "url": href})
            break

    # Fallback: scan all links for episode pattern
    if not episodes:
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"ep(?:isode)?[-_]?\d+|/\d+/?$", href, re.I):
                href_abs = _abs(href)
                if href_abs in seen:
                    continue
                seen.add(href_abs)
                text = a.get_text(strip=True)
                m = re.search(r"(\d+)", href)
                num = int(m.group(1)) if m else len(episodes) + 1
                episodes.append({"number": num, "title": text or f"Episode {num}", "url": href_abs})

    # Sort + dedupe
    seen_u, unique = set(), []
    for ep in sorted(episodes, key=lambda x: x["number"]):
        if ep["url"] not in seen_u:
            seen_u.add(ep["url"])
            unique.append(ep)

    return unique


# ── VIDEO LINKS ───────────────────────────────────────────────

def get_video_links(episode_url: str) -> dict:
    """Returns { '360p': url, '480p': url, ... }"""
    qualities = {}
    try:
        resp = _get(episode_url, referer=BASE)
        soup = BeautifulSoup(resp.text, "lxml")
        page_html = resp.text

        # ── Method A: <source> tags ──
        for src in soup.select("source[src], source[data-src]"):
            url = src.get("src") or src.get("data-src", "")
            label = src.get("label") or src.get("size") or src.get("res", "")
            if not label:
                m = re.search(r"(\d{3,4})p", url, re.I)
                label = m.group(1) + "p" if m else "480p"
            if url:
                qualities[str(label).strip()] = _abs(url)

        # ── Method B: Download links ──
        for a in soup.select("a[href*='.mp4'], .download-link a, .dl-server a, "
                             "[class*='download'] a, [class*='btn-dl'] a"):
            href = a.get("href", "")
            text = a.get_text(strip=True) + href
            m = re.search(r"(\d{3,4})p", text, re.I)
            q = m.group(1) + "p" if m else "480p"
            if href:
                qualities.setdefault(q, _abs(href))

        # ── Method C: Script parsing ──
        for script in soup.select("script"):
            js = script.string or ""
            if len(js) < 10:
                continue

            # jwplayer/plyr sources array
            for pattern in [
                r'sources\s*[=:]\s*(\[[\s\S]*?\])',
                r'"sources"\s*:\s*(\[[\s\S]*?\])',
                r"'sources'\s*:\s*(\[[\s\S]*?\])",
            ]:
                for m in re.finditer(pattern, js):
                    raw = m.group(1)
                    try:
                        data = json.loads(raw)
                        for entry in (data if isinstance(data, list) else []):
                            if not isinstance(entry, dict):
                                continue
                            url = entry.get("file") or entry.get("src") or entry.get("url", "")
                            lbl = str(entry.get("label") or entry.get("size") or "")
                            if url:
                                if not lbl:
                                    mm = re.search(r"(\d{3,4})p", url, re.I)
                                    lbl = mm.group(1) + "p" if mm else "480p"
                                qualities[lbl.strip()] = url
                    except Exception:
                        pass

            # Direct file= assignments
            for m in re.finditer(r'(?:file|src|url)\s*[=:]\s*["\']([^"\']+\.mp4[^"\']*)["\']', js):
                url = m.group(1)
                mm = re.search(r"(\d{3,4})p", url, re.I)
                q = mm.group(1) + "p" if mm else "480p"
                qualities.setdefault(q, url)

            # Raw mp4/m3u8 URLs
            for url in re.findall(r'https?://[^\s\'"<>\\]+\.(?:mp4|m3u8)[^\s\'"<>\\]*', js):
                mm = re.search(r"(\d{3,4})p", url, re.I)
                q = mm.group(1) + "p" if mm else ("HLS" if ".m3u8" in url else "480p")
                qualities.setdefault(q, url)

        # ── Method D: Iframes ──
        for iframe in soup.select("iframe[src], iframe[data-src], iframe[data-lazy-src]"):
            src = (iframe.get("src") or iframe.get("data-src") or
                   iframe.get("data-lazy-src", ""))
            if src:
                iframe_q = _extract_from_iframe(_abs(src), episode_url)
                for q, u in iframe_q.items():
                    qualities.setdefault(q, u)

    except Exception as e:
        logger.error(f"[Scraper] get_video_links error ({episode_url}): {e}")

    # Normalize: "720" → "720p"
    norm = {}
    for k, v in qualities.items():
        k = str(k).strip()
        if re.match(r"^\d{3,4}$", k):
            k += "p"
        norm[k] = v

    logger.info(f"[Scraper] Video links found: {list(norm.keys())} for {episode_url}")
    return norm


def _extract_from_iframe(iframe_url: str, referer: str) -> dict:
    qualities = {}
    try:
        h = {**HEADERS, "Referer": referer}
        resp = requests.get(iframe_url, headers=h, timeout=12)
        html = resp.text

        for url in re.findall(r'https?://[^\s\'"<>\\]+\.mp4[^\s\'"<>\\]*', html):
            m = re.search(r"(\d{3,4})p", url, re.I)
            q = m.group(1) + "p" if m else "480p"
            qualities.setdefault(q, url)

        for url in re.findall(r'https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*', html):
            m = re.search(r"(\d{3,4})p", url, re.I)
            q = m.group(1) + "p" if m else "HLS"
            qualities.setdefault(q, url)

        for pattern in [r'sources\s*[=:]\s*(\[[\s\S]*?\])', r'"sources"\s*:\s*(\[[\s\S]*?\])']:
            for m in re.finditer(pattern, html):
                try:
                    data = json.loads(m.group(1))
                    for entry in (data if isinstance(data, list) else []):
                        if isinstance(entry, dict):
                            url = entry.get("file") or entry.get("src", "")
                            lbl = str(entry.get("label") or "")
                            if url:
                                if not lbl:
                                    mm = re.search(r"(\d{3,4})p", url, re.I)
                                    lbl = mm.group(1) + "p" if mm else "480p"
                                qualities[lbl] = url
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"[Scraper] iframe extract error: {e}")
    return qualities
