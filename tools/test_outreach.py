#!/usr/bin/env python3
import os
import sqlite3
import sys
import threading
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import outreach


class SendManualTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""CREATE TABLE vessels (
            mmsi INTEGER PRIMARY KEY, imo INTEGER, shipname TEXT, length INTEGER,
            proposal_sent INTEGER DEFAULT 0, proposal_pending_at REAL DEFAULT NULL)""")
        self.conn.execute(
            "INSERT INTO vessels (mmsi, imo, shipname, length) "
            "VALUES (273123456, 9123456, '<b>TEST</b>', 100)")
        self.conn.commit()
        self.lock = threading.Lock()

    def tearDown(self):
        self.conn.close()

    def _row(self):
        return self.conn.execute(
            "SELECT proposal_sent, proposal_pending_at FROM vessels "
            "WHERE mmsi=273123456").fetchone()

    @patch.object(outreach, "tg")
    @patch.object(outreach, "enrich")
    @patch.object(outreach.smtplib, "SMTP_SSL")
    def test_too_young_vessel_is_not_claimed(self, smtp, enrich, tg):
        enrich.return_value = {
            "manager_email": "manager@example.test",
            "year_built": datetime.now().year - outreach.MIN_AGE + 1,
        }

        result = outreach.send_manual(
            self.conn, self.lock, 273123456, 11.0, 1.0)

        self.assertFalse(result)
        self.assertEqual(self._row(), (0, None))
        smtp.assert_not_called()
        self.assertIn("age=", tg.call_args.args[0])

    @patch.object(outreach, "tg")
    @patch.object(outreach, "enrich")
    @patch.object(outreach.smtplib, "SMTP_SSL")
    def test_success_marks_sent_and_clears_pending(self, smtp, enrich, tg):
        enrich.return_value = {
            "manager_email": "manager@example.test",
            "year_built": datetime.now().year - outreach.MIN_AGE,
        }

        result = outreach.send_manual(
            self.conn, self.lock, 273123456, 11.0, 1.0)

        self.assertTrue(result)
        self.assertEqual(self._row(), (1, None))
        smtp.return_value.__enter__.return_value.send_message.assert_called_once()

    @patch.object(outreach, "tg")
    @patch.object(outreach, "enrich")
    @patch.object(outreach.smtplib, "SMTP_SSL")
    def test_smtp_failure_releases_claim_and_escapes_error(
            self, smtp, enrich, tg):
        enrich.return_value = {
            "manager_email": "manager@example.test",
            "year_built": datetime.now().year - outreach.MIN_AGE,
        }
        smtp.return_value.__enter__.return_value.send_message.side_effect = (
            RuntimeError("<b>SMTP failure</b>"))

        result = outreach.send_manual(
            self.conn, self.lock, 273123456, 11.0, 1.0)

        self.assertFalse(result)
        self.assertEqual(self._row(), (0, None))
        self.assertIn("&lt;b&gt;SMTP failure&lt;/b&gt;",
                      tg.call_args.args[0])


class TelegramFormattingTests(unittest.TestCase):
    @patch.object(outreach.requests, "post")
    def test_telegram_api_url_can_be_overridden(self, post):
        post.return_value.status_code = 200
        with patch.object(outreach, "TG_TOKEN", "test-token"):
            with patch.object(outreach, "TG_CHAT", "test-chat"):
                with patch.object(
                        outreach, "TELEGRAM_API_URL", "http://localhost:8080"):
                    outreach.tg("hello")

        self.assertEqual(
            post.call_args.args[0],
            "http://localhost:8080/bottest-token/sendMessage")

    @patch.object(outreach, "tg")
    def test_candidate_name_and_reasons_are_html_escaped(self, tg):
        candidate = {
            "eligible": True, "proposal_sent": False, "name": "<b>VESSEL</b>",
            "mmsi": 273123456, "imo": 9123456, "length": 100,
            "dist_km": 1.0, "delta_deg": 2.0, "confidence": 0.9,
            "age_s": 1.0, "reasons": "<script>alert(1)</script>",
        }

        outreach.notify_candidates(11.0, 0.9, [candidate])

        text = tg.call_args.args[0]
        self.assertIn("&lt;b&gt;VESSEL&lt;/b&gt;", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)


if __name__ == "__main__":
    unittest.main()
