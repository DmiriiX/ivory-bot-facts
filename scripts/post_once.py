#!/usr/bin/env python3
"""IVORY-ART reliable Telegram + MAX posting (Python 3.11+)."""
import hashlib
import html
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
FACTS = os.getenv("BOT_KIND") == "facts"
TZ = ZoneInfo("Europe/Moscow")
STATE = ROOT / "state.json"
BUTTON = "Интересные картины на нашем сайте" if FACTS else "Больше картин на нашем сайте"

def key(post):
    value = ("fact:" + str(post.get("id") or post.get("title"))) if FACTS else ("painting:" + str(post.get("link") or post.get("title")))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

def save(state):
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE)

def call(url, payload, headers=None, method=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    hdr = dict(headers or {})
    if data is not None:
        hdr["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=hdr, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}"), False
    except urllib.error.HTTPError as exc:
        print("HTTP error", exc.code, exc.read().decode("utf-8", errors="replace")[:160])
        return {}, False
    except (OSError, ValueError) as exc:
        print("Transport uncertain", type(exc).__name__)
        return {}, True

def picture(url):
    if not url.startswith("https://"):
        raise ValueError("HTTPS photo URL required")
    req = urllib.request.Request(url, headers={"User-Agent": "IvoryArtPoster/2.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        content_type = resp.headers.get("Content-Type", "").split(";")[0]
        data = resp.read(15000001)
    if not content_type.startswith("image/") or not 500 <= len(data) <= 15000000:
        raise ValueError("Invalid photo data")
    return data, content_type

def multipart(url, fields, image):
    boundary = "Ivory" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\n' + str(value) + "\r\n").encode())
    parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="photo"; filename="painting.jpg"\r\nContent-Type: ' + image[1] + "\r\n\r\n").encode())
    parts.extend([image[0], ("\r\n--" + boundary + "--\r\n").encode()])
    req = urllib.request.Request(url, data=b"".join(parts), headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}"), False
    except urllib.error.HTTPError as exc:
        print("Photo HTTP error", exc.code)
        return {}, False
    except (OSError, ValueError) as exc:
        print("Photo delivery uncertain", type(exc).__name__)
        return {}, True

def telegram(post, image):
    token = os.environ["BOT_TOKEN"]
    channel = os.environ["CHANNEL_ID"]
    button = {"inline_keyboard": [[{"text": BUTTON, "url": post.get("link") or "https://ivory-art.com/catalog"}]]}
    caption = (post.get("caption") or post.get("text") or post.get("title") or "")
    api = "https://api.telegram.org/bot" + token + "/"
    if image:
        fields = {"chat_id": channel, "caption": caption[:1000], "reply_markup": json.dumps(button, ensure_ascii=False)}
        if not FACTS:
            fields["parse_mode"] = "HTML"
        return multipart(api + "sendPhoto", fields, image)
    if not FACTS:
        return {}, False
    return call(api + "sendMessage", {"chat_id": channel, "text": caption[:4000], "reply_markup": button})

def max_upload(image):
    token = os.environ["MAX_BOT_TOKEN"]
    meta, uncertain = call("https://platform-api2.max.ru/uploads?type=image", None, {"Authorization": token}, "POST")
    if uncertain:
        raise RuntimeError("Upload request uncertain")
    url = meta.get("url", "")
    if not url.startswith("https://"):
        raise RuntimeError("No MAX upload URL")
    boundary = "Ivory" + uuid.uuid4().hex
    body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="data"; filename="painting.jpg"\r\nContent-Type: ' + image[1] + '\r\n\r\n').encode() + image[0] + ('\r\n--' + boundary + '--\r\n').encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8") or "{}")
    photo_token = result.get("token")
    if not photo_token and isinstance(result.get("photos"), dict):
        photo_token = next((x.get("token") for x in result["photos"].values() if isinstance(x, dict) and x.get("token")), None)
    if not photo_token:
        raise RuntimeError("MAX upload token missing")
    return photo_token

def max_send(post, image):
    attachments = []
    if image:
        attachments.append({"type": "image", "payload": {"token": max_upload(image)}})
    elif not FACTS:
        return {}, False
    attachments.append({"type": "inline_keyboard", "payload": {"buttons": [[{"type": "link", "text": BUTTON, "url": post.get("link") or "https://ivory-art.com/catalog"}]]}})
    text = html.unescape(re.sub(r"<[^>]*>", "", post.get("caption") or post.get("text") or post.get("title") or ""))[:4000]
    url = "https://platform-api2.max.ru/messages?chat_id=" + urllib.parse.quote(os.environ["MAX_CHANNEL_ID"], safe="")
    return call(url, {"text": text, "attachments": attachments, "notify": True}, {"Authorization": os.environ["MAX_BOT_TOKEN"]})

def due(state, now):
    if FACTS and now.weekday() != 3:
        return False
    start, end = (18, 21) if FACTS else (11, 20)
    if not start <= now.hour < end:
        return False
    if state.get("last_post_at"):
        try:
            last = datetime.fromisoformat(state["last_post_at"])
            last = last.replace(tzinfo=TZ) if last.tzinfo is None else last.astimezone(TZ)
            if (now.date() - last.date()).days < (7 if FACTS else 3):
                return False
        except ValueError:
            pass
    date_key = ("facts:" if FACTS else "paintings:") + str(now.date())
    hour = start + int(hashlib.sha256(date_key.encode()).hexdigest(), 16) % (end - start)
    print("Scheduled hour:", hour, "MSK")
    return now.hour >= hour

def main():
    posts = json.loads((ROOT / "posts.json").read_text(encoding="utf-8"))
    if not isinstance(posts, list) or not posts or len(set(map(key, posts))) != len(posts):
        sys.exit("Invalid or duplicate posts")
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if "used_ids" not in state:
        state["used_ids"] = [key(posts[i]) for i in state.get("used_indices", []) if type(i) is int and 0 <= i < len(posts)]
        save(state)
    control_path = ROOT / "control.json"
    control = json.loads(control_path.read_text(encoding="utf-8")) if control_path.exists() else {}
    if control.get("paused"):
        print("Publication paused by IVORY-ART admin")
        return
    now = datetime.now(TZ)
    pending = state.get("pending")
    if pending:
        post = next((p for p in posts if key(p) == pending["id"]), None)
        if post is None:
            sys.exit("Pending post missing: do not discard it")
    else:
        if os.getenv("FORCE_POST") != "1" and not due(state, now):
            print("Not due")
            return
        excluded = set(control.get("excluded_ids") or [])
        available = [p for p in posts if key(p) not in set(state["used_ids"]) and key(p) not in excluded]
        if not available:
            print("No fresh posts left, not repeating")
            return
        preferred = control.get("next_id")
        choice = next((p for p in available if key(p) == preferred), None)
        if choice:
            post = choice
        elif FACTS:
            post = available[0]
        else:
            different = [p for p in available if p.get("artist") != state.get("last_artist")]
            post = random.choice(different or available)
        pending = {"id": key(post), "telegram": False, "max": False, "date": str(now.date())}
        if os.getenv("DRY_RUN") != "1":
            state["pending"] = pending
            save(state)
    print("Selected:", post.get("title"))
    if os.getenv("DRY_RUN") == "1":
        print("DRY RUN: no messages sent")
        return
    for envvar in ("BOT_TOKEN", "CHANNEL_ID", "MAX_BOT_TOKEN", "MAX_CHANNEL_ID"):
        if not os.getenv(envvar):
            sys.exit("Missing configuration: " + envvar)
    image = None
    if post.get("photo"):
        try:
            image = picture(post["photo"])
        except (OSError, ValueError) as exc:
            sys.exit("Image unavailable; will retry: " + str(exc))
    if not FACTS and not image:
        sys.exit("No painting image, refusing text-only post")
    for name, sender in (("telegram", telegram), ("max", max_send)):
        if pending[name] is True:
            continue
        if pending[name] == "uncertain":
            sys.exit(name + " might have been sent: inspect channel and resolve state manually")
        try:
            result, uncertain = sender(post, image)
            ok = bool(result.get("ok")) if name == "telegram" else isinstance(result.get("message"), dict)
        except Exception as exc:
            print(name, "delivery failed:", type(exc).__name__)
            ok, uncertain = False, False
        pending[name] = True if ok else ("uncertain" if uncertain else False)
        save(state)
        print(name, "sent" if ok else "uncertain" if uncertain else "failed")
    if pending["telegram"] is True and pending["max"] is True:
        state["used_ids"] = sorted(set(state["used_ids"] + [pending["id"]]))
        state["last_post_at"] = now.isoformat()
        if not FACTS:
            state["last_artist"] = post.get("artist")
        state.pop("pending", None)
        save(state)
        if control.get("next_id") == key(post):
            control["next_id"] = None
            control_path.write_text(json.dumps(control, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Published to both destinations")
    else:
        sys.exit("Partial publication saved; failed destination will retry")

if __name__ == "__main__":
    main()
