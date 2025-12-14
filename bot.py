import os
import json
import requests
import feedparser

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
FEED_URL = os.environ["FEED_URL"]

STATE_FILE = "state.json"

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"seen": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": False
    })

def main():
    state = load_state()
    seen = set(state["seen"])

    feed = feedparser.parse(FEED_URL)
    entries = feed.entries[:10]

    for entry in reversed(entries):
        link = entry.get("link")
        if link in seen:
            continue

        title = entry.get("title", "RFI")
        message = f"{title}\n{link}"

        send_message(message)
        seen.add(link)

    state["seen"] = list(seen)[-300:]
    save_state(state)

if __name__ == "__main__":
    main()
