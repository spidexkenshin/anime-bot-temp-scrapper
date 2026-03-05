"""
animesalt.top scraper
Uses requests + BeautifulSoup with multiple selector fallbacks
because anime sites often update their HTML structure.
"""

import re
import json
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

from config import Config

logger = logging.getLogger(__name__)

BASE = Config.ANIME_SITE
HDR = Config.SCRAPER_HEADERS


def _get(url: str, referer: str = BASE) -> BeautifulSoup:
    """HTTP GET and return BeautifulSoup"""
    h = {**HDR, "Referer": referer}
    resp = requests.get(url, headers=h, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _abs(url: str) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else urljoin(BASE, url)


# ── 1. SEARCH ─────────────────────────────────────────────────

def search_anime(query: str) -> list:
    """
    Returns list of dicts:
      { title, url, thumbnail, year }
    """
    results = []
    try:
        search_url = f"{BASE}/?s={quote(query)}"
        soup = _get(search_url)

        # Selector battery — works across common WordPress/Animalia themes
        card_selectors = [
            "div.search-page .result-item",
            "div.movies-list .ml-item",
            "article.TPost",
            "article.item",
            "div.item",
            ".film_list-wrap .flw-item",
            ".anime_list_body li",
            "ul.UltimosSeries li",
        ]

        for sel in card_selectors:
            items = soup.select(sel)
            if items:
                for item in items:
                    a = item.select_one("a[href]")
                    title_el = item.select_one("h3, h2, .title, .name, .film-name")
                    img_el = item.select_one("img")

                    if not a:
                        continue
                    title = (title_el.get_text(strip=True) if title_el
                             else a.get("title", a.get_text(strip=True)))
                    url = _abs(a.get("href", ""))
                    thumb = ""
                    if img_el:
                        thumb = (img_el.get("data-src") or
                                 img_el.get("data-lazy-src") or
                                 img_el.get("src", ""))
                    if title and url:
                        results.append({"title": title, "url": url, "thumbnail": thumb})
                break  # Found matching selector — stop

        # Fallback: grab every article with a link
        if not results:
            for article in soup.find_all(["article", "li"]):
                a = article.find("a", href=True)
                if not a:
                    continue
                title = a.get("title") or a.get_text(strip=True)
                url = _abs(a["href"])
                if title and url and BASE in url:
                    results.append({"title": title, "url": url, "thumbnail": ""})

    except Exception as e:
        logger.error(f"[scraper] search_anime error: {e}")

    # Remove duplicates by URL
    seen, unique = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique[:10]


# ── 2. GET SEASONS ────────────────────────────────────────────

def get_seasons(anime_url: str) -> dict:
    """
    Returns dict:  { season_num (int): { name, url } }
    Falls back to single season if site has no season tabs.
    """
    seasons = {}
    try:
        soup = _get(anime_url)

        # Common season tab selectors
        season_selectors = [
            "div.SeasonBx a",
            ".seasons-list a",
            ".tab-seasons a",
            "#seasons-list a",
            "ul.dropdown-menu a[href*='season']",
            ".TpTbs a",
            "div[id*='season'] h3",
            ".sbox .Strs a",
        ]

        for sel in season_selectors:
            items = soup.select(sel)
            if items:
                for el in items:
                    text = el.get_text(strip=True)
                    href = _abs(el.get("href", anime_url))
                    m = re.search(r"(\d+)", text)
                    num = int(m.group(1)) if m else len(seasons) + 1
                    seasons[num] = {"name": text or f"Season {num}", "url": href}
                break

        # Check for season-specific URL patterns on the page
        if not seasons:
            links = soup.find_all("a", href=True)
            for a in links:
                href = a["href"]
                if re.search(r"/season[-_](\d+)|[?&]season=(\d+)", href, re.I):
                    m = re.search(r"(\d+)", href)
                    if m:
                        num = int(m.group(1))
                        text = a.get_text(strip=True) or f"Season {num}"
                        seasons[num] = {"name": text, "url": _abs(href)}

    except Exception as e:
        logger.error(f"[scraper] get_seasons error: {e}")

    if not seasons:
        seasons[1] = {"name": "Season 1", "url": anime_url}

    return dict(sorted(seasons.items()))


# ── 3. GET EPISODES ───────────────────────────────────────────

def get_episodes(season_url: str) -> list:
    """
    Returns sorted list:
      [ { number, title, url } ]
    """
    episodes = []
    try:
        soup = _get(season_url)

        ep_selectors = [
            "ul.episodios li",
            "ul.episodes-list li",
            ".episode-list li",
            "#episodios-lista li",
            ".AACrdn li",
            "div.episodios li",
            "ul.Lst li",
            "table.episodes tr",
            ".ep-list li",
            ".episodes-grid a",
        ]

        for sel in ep_selectors:
            items = soup.select(sel)
            if items:
                for item in items:
                    a = item.select_one("a[href]") or (
                        item if item.name == "a" else None)
                    if not a:
                        continue
                    href = _abs(a.get("href", ""))
                    text = item.get_text(strip=True)

                    # Extract episode number
                    m = re.search(r"(?:episode|ep|episodio)[\s\-_]*(\d+)", text, re.I)
                    if not m:
                        m = re.search(r"(\d+)$", href.rstrip("/"))
                    if not m:
                        m = re.search(r"\b(\d{1,3})\b", text)

                    num = int(m.group(1)) if m else len(episodes) + 1
                    episodes.append({"number": num, "title": text or f"Episode {num}", "url": href})
                break

        # Fallback: href pattern matching
        if not episodes:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if re.search(r"episode|ep[-_/]\d", href, re.I):
                    m = re.search(r"(\d+)", href)
                    num = int(m.group(1)) if m else len(episodes) + 1
                    text = a.get_text(strip=True) or f"Episode {num}"
                    episodes.append({"number": num, "title": text, "url": _abs(href)})

    except Exception as e:
        logger.error(f"[scraper] get_episodes error: {e}")

    # Sort by episode number, deduplicate
    seen, unique = set(), []
    for ep in sorted(episodes, key=lambda x: x["number"]):
        if ep["url"] not in seen:
            seen.add(ep["url"])
            unique.append(ep)

    return unique


# ── 4. GET VIDEO LINKS ────────────────────────────────────────

def get_video_links(episode_url: str) -> dict:
    """
    Returns dict:  { '360p': url, '480p': url, ... }
    Tries multiple extraction methods.
    """
    qualities = {}
    try:
        soup = _get(episode_url)
        page_html = str(soup)

        # ── Method A: <source> tags ───
        for src in soup.select("source[src]"):
            url = src.get("src", "")
            label = src.get("label", src.get("size", ""))
            if not label:
                m = re.search(r"(\d{3,4})p", url, re.I)
                label = m.group(1) + "p" if m else "480p"
            if url:
                qualities[str(label)] = _abs(url)

        # ── Method B: Direct .mp4 links in <a> ───
        for a in soup.select("a[href*='.mp4'], .download-link a, .dl-server a"):
            href = a.get("href", "")
            text = a.get_text(strip=True) + href
            m = re.search(r"(\d{3,4})p", text, re.I)
            q = m.group(1) + "p" if m else "480p"
            if href:
                qualities[q] = _abs(href)

        # ── Method C: JSON sources in <script> ───
        for script in soup.select("script"):
            js = script.string or ""

            # jwplayer / plyr style sources array
            for pattern in [
                r'sources\s*:\s*(\[.*?\])',
                r'"sources"\s*:\s*(\[.*?\])',
                r"file\s*:\s*['\"]([^'\"]+\.mp4[^'\"]*)['\"]",
            ]:
                for m in re.finditer(pattern, js, re.DOTALL):
                    raw = m.group(1)
                    # Try JSON parse for array patterns
                    if raw.startswith("["):
                        try:
                            data = json.loads(raw)
                            for entry in data:
                                if isinstance(entry, dict):
                                    url = entry.get("file", entry.get("src", ""))
                                    lbl = str(entry.get("label", entry.get("size", "")))
                                    if url:
                                        if not lbl:
                                            mm = re.search(r"(\d{3,4})p", url, re.I)
                                            lbl = mm.group(1) + "p" if mm else "480p"
                                        qualities[lbl] = url
                        except Exception:
                            pass
                    else:
                        # Direct file URL
                        url = raw.strip("'\"")
                        mm = re.search(r"(\d{3,4})p", url, re.I)
                        q = mm.group(1) + "p" if mm else "480p"
                        qualities[q] = url

            # Inline mp4/m3u8 URLs
            for url in re.findall(r'https?://[^\s\'"<>]+\.(?:mp4|m3u8)[^\s\'"<>]*', js):
                mm = re.search(r"(\d{3,4})p", url, re.I)
                q = mm.group(1) + "p" if mm else ("HLS" if ".m3u8" in url else "480p")
                qualities.setdefault(q, url)

        # ── Method D: Iframes ───
        for iframe in soup.select("iframe[src], iframe[data-src]"):
            src = iframe.get("src") or iframe.get("data-src", "")
            if src and src.startswith("//"):
                src = "https:" + src
            if src:
                iframe_qualities = _extract_iframe(src, episode_url)
                for q, u in iframe_qualities.items():
                    qualities.setdefault(q, u)

    except Exception as e:
        logger.error(f"[scraper] get_video_links error: {e}")

    # Normalise quality keys like '720' → '720p'
    normalised = {}
    for k, v in qualities.items():
        k = str(k).strip()
        if re.match(r"^\d{3,4}$", k):
            k = k + "p"
        normalised[k] = v

    return normalised


def _extract_iframe(iframe_url: str, referer: str) -> dict:
    qualities = {}
    try:
        h = {**HDR, "Referer": referer}
        resp = requests.get(iframe_url, headers=h, timeout=12)
        content = resp.text

        for url in re.findall(r'https?://[^\s\'"<>]+\.mp4[^\s\'"<>]*', content):
            m = re.search(r"(\d{3,4})p", url, re.I)
            q = m.group(1) + "p" if m else "480p"
            qualities.setdefault(q, url)

        for url in re.findall(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*', content):
            m = re.search(r"(\d{3,4})p", url, re.I)
            q = m.group(1) + "p" if m else "HLS"
            qualities.setdefault(q, url)

        # Nested iframe sources array
        for m in re.finditer(r'sources\s*:\s*(\[.*?\])', content, re.DOTALL):
            try:
                data = json.loads(m.group(1))
                for entry in data:
                    if isinstance(entry, dict):
                        url = entry.get("file", "")
                        lbl = str(entry.get("label", ""))
                        if url:
                            if not lbl:
                                mm = re.search(r"(\d{3,4})p", url, re.I)
                                lbl = mm.group(1) + "p" if mm else "480p"
                            qualities[lbl] = url
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"[scraper] _extract_iframe error ({iframe_url}): {e}")

    return qualities
