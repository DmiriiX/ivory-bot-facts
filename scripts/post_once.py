#!/usr/bin/env python3
"""
Автопостинг рубрики «Интересные факты об искусстве по четвергам»
в Telegram и MAX.

Работает так же, как основной бот картин:
- GitHub Actions запускает скрипт каждый час в окне 11–20 МСК
- Скрипт сам решает, публиковать ли сегодня (только четверг + не раньше чем через 7 дней)
- Случайный час внутри окна
- Отдельные посты в Telegram и MAX
"""

import json
import os
import random
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# MAX API на GitHub runners иногда ругается на сертификаты
ssl._create_default_https_context = ssl._create_unverified_context

ROOT = Path(__file__).resolve().parent.parent
POSTS_FILE = ROOT / "posts.json"
STATE_FILE = ROOT / "state.json"

TG_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TG_CHANNEL = os.environ.get("CHANNEL_ID", "").strip()

MAX_TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
MAX_CHANNEL = os.environ.get("MAX_CHANNEL_ID", "").strip()

TZ = ZoneInfo("Europe/Moscow")
# Только по четвергам, не чаще раза в 7 дней
POST_WEEKDAY = 3  # 0=пн … 3=чт
POST_EVERY_DAYS = 7
WINDOW_START_HOUR = 11
WINDOW_END_HOUR = 20
BUTTON_TEXT = "Интересные картины на нашем сайте"
DEFAULT_LINK = "https://www.ivory-art.com"


def load_json(path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_json(url, payload=None, headers=None, method=None):
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
        method = method or "POST"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method or "GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def should_post_now(state, now):
    """Решаем, публиковать ли прямо сейчас."""
    # Только четверг
    if now.weekday() != POST_WEEKDAY:
        print(f"Not Thursday (weekday={now.weekday()}), skip")
        return False

    # Только в окне 11:00–19:59 МСК
    if not (WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR):
        print(f"Outside window ({now.hour}:00 MSK), skip")
        return False

    last = None
    if state.get("last_post_at"):
        try:
            last = datetime.fromisoformat(state["last_post_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=TZ)
            else:
                last = last.astimezone(TZ)
        except Exception:
            last = None

    # Уже публиковали сегодня
    if last and last.date() == now.date():
        print("Already posted today, skip")
        return False

    # Не прошло 7 дней
    if last and (now - last) < timedelta(days=POST_EVERY_DAYS):
        print(f"Need {POST_EVERY_DAYS} days since last post, skip")
        return False

    # Случайный час из оставшихся в окне
    remaining = list(range(now.hour, WINDOW_END_HOUR))
    if not remaining:
        return False
    chosen = random.choice(remaining)
    print(f"Remaining hours {remaining}, chosen {chosen}, now {now.hour}")
    if now.hour != chosen:
        print("Not this hour, skip")
        return False
    return True


def pick_post(posts, state):
    used = set(state.get("used_indices", []))
    available = [i for i in range(len(posts)) if i not in used]
    if not available:
        print("All posts used, resetting cycle")
        available = list(range(len(posts)))
        state["used_indices"] = []
    return random.choice(available)


def post_telegram(post):
    if not TG_TOKEN or not TG_CHANNEL:
        print("Skip Telegram: no BOT_TOKEN or CHANNEL_ID")
        return False

    caption = post.get("caption", post.get("title", ""))
    photo = (post.get("photo") or "").strip()
    link = (post.get("link") or DEFAULT_LINK).strip()
    keyboard = {"inline_keyboard": [[{"text": BUTTON_TEXT, "url": link}]]}

    if photo:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TG_CHANNEL,
            "photo": photo,
            "caption": caption,
            "reply_markup": keyboard,
        }
    else:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = {
            "chat_id": TG_CHANNEL,
            "text": caption,
            "reply_markup": keyboard,
        }

    result = http_json(url, payload)
    if not result.get("ok"):
        print("Telegram error:", result)
        return False
    print("Telegram OK")
    return True


def post_max(post):
    if not MAX_TOKEN or not MAX_CHANNEL:
        print("Skip MAX: no MAX_BOT_TOKEN or MAX_CHANNEL_ID")
        return False

    text = post.get("caption", post.get("title", ""))
    # MAX не любит HTML-теги
    for tag in ("<b>", "</b>", "<i>", "</i>"):
        text = text.replace(tag, "")

    photo = (post.get("photo") or "").strip()
    link = (post.get("link") or DEFAULT_LINK).strip()

    attachments = []
    if photo:
        attachments.append({"type": "image", "payload": {"url": photo}})
    attachments.append(
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [[{"type": "link", "text": BUTTON_TEXT, "url": link}]]
            },
        }
    )

    url = f"https://platform-api2.max.ru/messages?chat_id={MAX_CHANNEL}"
    headers = {"Authorization": MAX_TOKEN}
    payload = {"text": text, "attachments": attachments}

    try:
        result = http_json(url, payload, headers=headers)
        print("MAX OK:", result.get("message", {}).get("body", {}).get("mid", result))
        return True
    except Exception as e:
        print("MAX error:", e)
        return False


def main():
    now = datetime.now(TZ)
    print(f"Now MSK: {now.isoformat()}")

    posts = load_json(POSTS_FILE, [])
    state = load_json(STATE_FILE, {"last_post_at": None, "used_indices": []})

    if not posts:
        print("No posts in posts.json")
        sys.exit(1)

    force = os.environ.get("FORCE_POST", "").strip() == "1"
    if not force and not should_post_now(state, now):
        print("No post this run")
        sys.exit(0)

    idx = pick_post(posts, state)
    post = posts[idx]
    title = post.get("title", f"#{idx}")

    ok_tg = post_telegram(post)
    ok_max = post_max(post)

    if not ok_tg and not ok_max:
        print("Both channels failed")
        sys.exit(1)

    used = list(state.get("used_indices", []))
    if idx not in used:
        used.append(idx)
    state["used_indices"] = used
    state["last_post_at"] = now.isoformat()
    save_json(STATE_FILE, state)
    print(f"Done: {title}")


if __name__ == "__main__":
    main()
