import os
import re
import json
import time
import html
import hashlib
from typing import Dict, Any, List, Set, Tuple, Optional
from urllib.parse import urlparse

import requests
import feedparser


# ---------------------------
# Configuration (ENV)
# ---------------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]  # e.g. -1001234567890

FEED_URLS = [
    # RFI Culture
    "https://www.rfi.fr/fr/culture/rss",
    # France Culture (culture)
    "https://www.franceculture.fr/rss/culture.xml",
    # Le Monde (culture)
    "https://www.lemonde.fr/culture/rss_full.xml",
    # Télérama (rss)
    "https://www.telerama.fr/rss",
    # ARTE Culture (may vary; keep if it returns entries)
    "https://www.arte.tv/rss/fr/culture/",
]

STATE_FILE = os.environ.get("STATE_FILE", "state.json")

MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "6"))
SEEN_LIMIT = int(os.environ.get("SEEN_LIMIT", "2000"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
SLEEP_BETWEEN_POSTS = float(os.environ.get("SLEEP_BETWEEN_POSTS", "1.2"))

# Diversity setting: max 1 post per source per run
ONE_PER_SOURCE = os.environ.get("ONE_PER_SOURCE", "1") == "1"

UA = os.environ.get(
    "USER_AGENT",
    "RegardiRSSBot/1.0 (+https://regardi.fr; contact@regardi.fr)"
)


# ---------------------------
# State
# ---------------------------
def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "seen" in data and isinstance(data["seen"], list):
                return data
    except Exception:
        pass
    return {"seen": []}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------
# Text utilities
# ---------------------------
def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def strip_html(s: str) -> str:
    s = s or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def normalize_text(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def short_summary(entry: Any, max_chars: int = 280) -> str:
    raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    txt = norm_space(strip_html(raw))
    if not txt:
        return ""
    if len(txt) <= max_chars:
        return txt
    return txt[: max_chars - 1].rstrip() + "…"


# ---------------------------
# Source detection
# ---------------------------
def host_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
        return host
    except Exception:
        return ""


def nice_source_name(feed_obj: Any, fallback_link: str = "") -> str:
    # Prefer the feed title if present
    title = ""
    try:
        title = norm_space(str(getattr(feed_obj.feed, "title", "") or ""))
    except Exception:
        title = ""

    if title:
        # Some feeds include long titles; keep them readable
        return title

    host = host_from_url(fallback_link)
    return host or "Source"


# ---------------------------
# Dedup fingerprint (strong)
# ---------------------------
def make_fingerprint(entry: Any, source: str) -> str:
    title = normalize_text(getattr(entry, "title", ""))
    summary = normalize_text(getattr(entry, "summary", "") or getattr(entry, "description", ""))
    link = normalize_text(getattr(entry, "link", ""))
    # include source to reduce weird collisions
    base = f"{normalize_text(source)}|{title}|{summary[:600]}|{link}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ---------------------------
# Telegram
# ---------------------------
def telegram_send(message_html: str, disable_preview: bool = False) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()


def build_message(entry: Any, source: str) -> str:
    title = norm_space(getattr(entry, "title", "") or "")
    link = getattr(entry, "link", "") or ""
    summary = short_summary(entry)

    parts: List[str] = []

    if title:
        parts.append(f"<b>{html.escape(title)}</b>")

    if summary:
        parts.append(html.escape(summary))

    if link:
        parts.append(f"🔗 <a href=\"{html.escape(link)}\">Lire l’article</a>")

    parts.append(f"Source : {html.escape(source)}")
    return "\n\n".join(parts)


# ---------------------------
# Feed parsing
# ---------------------------
def entry_time(entry: Any) -> float:
    # Best-effort ordering; if not available -> 0
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if t:
        try:
            return time.mktime(t)
        except Exception:
            return 0.0
    return 0.0


def parse_feed(url: str) -> Tuple[List[Any], str]:
    headers = {"User-Agent": UA}
    f = feedparser.parse(url, request_headers=headers)

    # Determine source name
    fallback_link = ""
    try:
        if f.entries and getattr(f.entries[0], "link", ""):
            fallback_link = getattr(f.entries[0], "link", "")
    except Exception:
        fallback_link = ""

    source = nice_source_name(f, fallback_link=fallback_link)

    entries = list(f.entries or [])
    return entries, source


# ---------------------------
# Main
# ---------------------------
def main() -> None:
    state = load_state()
    seen: List[str] = state.get("seen", [])
    seen_set: Set[str] = set(seen)

    collected: List[Tuple[Any, str]] = []

    # Collect entries from all feeds
    for url in FEED_URLS:
        try:
            entries, source = parse_feed(url)
            for e in entries:
                collected.append((e, source))
        except Exception:
            continue

    if not collected:
        return

    # Sort old -> new
    collected.sort(key=lambda x: entry_time(x[0]))

    posted = 0
    new_seen: List[str] = []
    used_sources: Set[str] = set()

    for e, source in collected:
        fp = make_fingerprint(e, source)
        if not fp or fp in seen_set:
            continue

        if ONE_PER_SOURCE and source in used_sources:
            continue

        msg = build_message(e, source)

        try:
            telegram_send(msg, disable_preview=False)
            posted += 1
            used_sources.add(source)

            seen_set.add(fp)
            new_seen.append(fp)

            time.sleep(SLEEP_BETWEEN_POSTS)
        except Exception:
            # Stop to avoid repeated failures / duplicates
            break

        if posted >= MAX_POSTS_PER_RUN:
            break

    # Persist state
    state["seen"] = (seen + new_seen)[-SEEN_LIMIT:]
    save_state(state)


if __name__ == "__main__":
    main()
