import os
import re
import json
import time
import html
import requests
import feedparser
from typing import Dict, Any, List

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]          # e.g. -1001234567890
FEED_URL = os.environ["FEED_URL"]        # RFI RSS (culture/arts)
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))
SEEN_LIMIT = int(os.environ.get("SEEN_LIMIT", "400"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))

UA = os.environ.get(
    "USER_AGENT",
    "RegardiRSSBot/1.0 (+https://t.me/; contact@regardi.fr)"
)

def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": []}

def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def strip_html(s: str) -> str:
    s = s or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)

def short_summary(entry: Dict[str, Any], max_chars: int = 280) -> str:
    # Prefer RSS summary/description, fallback to title only
    raw = entry.get("summary", "") or entry.get("description", "")
    txt = norm_space(strip_html(raw))
    if not txt:
        return ""
    if len(txt) <= max_chars:
        return txt
    return txt[: max_chars - 1].rstrip() + "…"

def entry_key(entry: Dict[str, Any]) -> str:
    # Stable ID for dedup: guid/id/link + published
    guid = entry.get("id") or entry.get("guid") or ""
    link = entry.get("link") or ""
    published = entry.get("published") or entry.get("updated") or ""
    base = guid or link
    return norm_space(f"{base}||{published}")

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

def build_message(entry: Dict[str, Any]) -> str:
    title = norm_space(entry.get("title", ""))
    link = entry.get("link", "")
    summary = short_summary(entry)

    # French-only template
    parts: List[str] = []
    if title:
        parts.append(f"<b>{html.escape(title)}</b>")

    if summary:
        parts.append(html.escape(summary))

    if link:
        parts.append(f"🔗 <a href=\"{html.escape(link)}\">Lire sur RFI</a>")

    parts.append("Source: RFI")
    return "\n\n".join(parts)

def main() -> None:
    state = load_state()
    seen: List[str] = state.get("seen", [])
    seen_set = set(seen)

    headers = {"User-Agent": UA}
    feed = feedparser.parse(FEED_URL, request_headers=headers)

    entries = feed.entries or []
    if not entries:
        # Nothing to do
        return

    # Process from oldest to newest so channel reads naturally
    entries = list(reversed(entries))

    posted = 0
    new_seen: List[str] = []

    for e in entries:
        k = entry_key(e)
        if not k or k in seen_set:
            continue

        msg = build_message(e)

        try:
            telegram_send(msg, disable_preview=False)
            posted += 1
            seen_set.add(k)
            new_seen.append(k)
            time.sleep(1)  # soft rate-limit
        except Exception:
            # Stop on API errors to avoid loops / duplicates
            break

        if posted >= MAX_POSTS_PER_RUN:
            break

    # Keep only last SEEN_LIMIT
    merged = (seen + new_seen)[-SEEN_LIMIT:]
    state["seen"] = merged
    save_state(state)

if __name__ == "__main__":
    main()
