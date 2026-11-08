import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "post_once.py"

def load(kind):
    with patch.dict(os.environ, {"BOT_KIND": kind}):
        spec = importlib.util.spec_from_file_location("ivory_" + kind, SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module

class PostingTests(unittest.TestCase):
    def test_identity_stable_after_reordering(self):
        bot = load("paintings")
        a = {"link": "https://ivory-art.com/shop/one", "title": "First"}
        b = {"link": "https://ivory-art.com/shop/one", "title": "Renamed"}
        self.assertEqual(bot.key(a), bot.key(b))
        facts = load("facts")
        self.assertEqual(facts.key({"id": 2, "title": "First"}), facts.key({"id": 2, "title": "Renamed"}))

    def test_migration_and_no_repeats(self):
        bot = load("facts")
        with tempfile.TemporaryDirectory() as directory:
            bot.ROOT = Path(directory)
            bot.STATE = bot.ROOT / "state.json"
            posts = [{"id": 1, "title": "Previously posted"}, {"id": 2, "title": "New"}]
            (bot.ROOT / "posts.json").write_text(json.dumps(posts))
            bot.STATE.write_text(json.dumps({"used_indices": [0]}))
            with patch.object(bot, "due", return_value=True), patch.dict(os.environ, {"DRY_RUN": "1"}):
                bot.main()
            state = json.loads(bot.STATE.read_text())
            self.assertEqual(state["used_ids"], [bot.key(posts[0])])
            self.assertNotIn("pending", state)

    def test_partial_delivery_only_retries_missing_destination(self):
        bot = load("paintings")
        with tempfile.TemporaryDirectory() as directory:
            bot.ROOT = Path(directory)
            bot.STATE = bot.ROOT / "state.json"
            post = {"link": "https://ivory-art.com/shop/test", "title": "Test", "artist": "Artist", "photo": "https://example.com/photo.jpg"}
            (bot.ROOT / "posts.json").write_text(json.dumps([post]))
            bot.STATE.write_text("{}")
            with patch.dict(os.environ, {"BOT_TOKEN": "x", "CHANNEL_ID": "x", "MAX_BOT_TOKEN": "x", "MAX_CHANNEL_ID": "x", "DRY_RUN": "0"}), patch.object(bot, "due", return_value=True), patch.object(bot, "picture", return_value=(b"photo", "image/jpeg")), patch.object(bot, "telegram", return_value=({"ok": True}, False)) as tg, patch.object(bot, "max_send", side_effect=[({}, False), ({"message": {"body": {"mid": "ok"}}}, False)]) as max_send:
                with self.assertRaises(SystemExit):
                    bot.main()
                state = json.loads(bot.STATE.read_text())
                self.assertTrue(state["pending"]["telegram"])
                self.assertFalse(state["pending"]["max"])
                bot.main()
                self.assertEqual(tg.call_count, 1)
                self.assertEqual(max_send.call_count, 2)
                self.assertNotIn("pending", json.loads(bot.STATE.read_text()))

    def test_unsafe_tls_bypass_absent(self):
        self.assertNotIn("_create_unverified_context", SCRIPT.read_text())

if __name__ == "__main__":
    unittest.main()
