#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")


class StartupRecoveryTests(unittest.TestCase):
    def _start_main(self, db_path, code="import main"):
        env = os.environ.copy()
        env["DB_PATH"] = db_path
        env["MOCK_SDR"] = "true"
        env["TELEGRAM_BOT_TOKEN"] = "test-token"
        env["TELEGRAM_CHAT_ID"] = "111"
        env["TELEGRAM_OPERATOR_ID"] = "222"
        env["PYTHONPATH"] = os.pathsep.join(
            [ROOT, APP, env.get("PYTHONPATH", "")])
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=APP, env=env, capture_output=True, text=True, timeout=30)

    def _create_db(self, path, with_pending_column):
        conn = sqlite3.connect(path)
        pending_column = (
            ", proposal_pending_at REAL DEFAULT NULL"
            if with_pending_column else "")
        conn.execute(f"""CREATE TABLE vessels (
            mmsi INTEGER PRIMARY KEY, imo INTEGER, shipname TEXT, length INTEGER,
            lat REAL, lon REAL, last_seen REAL, prev_lat REAL, prev_lon REAL,
            prev_seen REAL, cog REAL, sog REAL,
            proposal_sent INTEGER DEFAULT 0{pending_column})""")
        conn.commit()
        conn.close()

    def test_migrates_existing_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy.db")
            self._create_db(path, with_pending_column=False)

            result = self._start_main(path)

            self.assertEqual(result.returncode, 0, result.stderr)
            conn = sqlite3.connect(path)
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(vessels)")}
            conn.close()
            self.assertIn("proposal_pending_at", columns)

    def test_recovers_stale_pending_but_keeps_recent_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pending.db")
            self._create_db(path, with_pending_column=True)
            now = time.time()
            conn = sqlite3.connect(path)
            conn.executemany(
                "INSERT INTO vessels (mmsi, proposal_sent, proposal_pending_at) "
                "VALUES (?, 1, ?)",
                [(273000001, now - 601), (273000002, now - 599)])
            conn.commit()
            conn.close()

            result = self._start_main(path)

            self.assertEqual(result.returncode, 0, result.stderr)
            conn = sqlite3.connect(path)
            rows = conn.execute(
                "SELECT mmsi, proposal_sent, proposal_pending_at "
                "FROM vessels ORDER BY mmsi").fetchall()
            conn.close()
            self.assertEqual(rows[0], (273000001, 0, None))
            self.assertEqual(rows[1][0:2], (273000002, 1))
            self.assertIsNotNone(rows[1][2])

    def test_group_callback_from_another_user_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "callback.db")
            code = """
import main
from unittest.mock import Mock, patch

callback = {
    "id": "callback-1",
    "from": {"id": 333},
    "message": {"chat": {"id": 111}},
    "data": "send:273123456:11:1",
}
assert not main._authorized_callback(callback)
assert main._authorized_callback({
    "from": {"id": 222}, "message": {"chat": {"id": 111}}
})
response = Mock()
response.json.return_value = {"result": [{
    "update_id": 1, "callback_query": callback
}]}
with patch.object(main.requests, "get",
                  side_effect=[response, KeyboardInterrupt]):
    with patch.object(main, "send_manual") as send_manual:
        try:
            main.tg_poll_loop()
        except KeyboardInterrupt:
            pass
        send_manual.assert_not_called()
"""

            result = self._start_main(path, code)

            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
