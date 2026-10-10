# -*- coding: utf-8 -*-
"""token-meter-mcp 测试：协议 + 统计逻辑，用临时 SQLite 库"""
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import token_meter_mcp as tm  # noqa: E402


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.server = tm.MCPServer("t", "1.0", tm.TOOLS)
        self.server._send = lambda m: self.sent.append(m)

    def _call(self, method, **kw):
        msg = {"jsonrpc": "2.0", "id": 1, "method": method}
        if kw:
            msg["params"] = kw
        self.server.handle(msg)
        return self.sent[-1]

    def test_initialize(self):
        self.assertEqual(self._call("initialize")["result"]["serverInfo"]["name"], "t")

    def test_tools_list(self):
        names = [t["name"] for t in self._call("tools/list")["result"]["tools"]]
        self.assertEqual(sorted(names),
                         ["price_list", "saving_tips", "top_models", "usage_report"])

    def test_unknown_tool(self):
        self.assertEqual(self._call("tools/call", name="x", arguments={})["error"]["code"], -32602)

    def test_missing_db_iserror(self):
        out = self._call("tools/call", name="usage_report", arguments={})
        self.assertTrue(out["result"]["isError"])
        self.assertIn("relay-proxy", out["result"]["content"][0]["text"])


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (tm.DB_PATH, tm.PRICE_PATH)
        tm.DB_PATH = Path(self.tmp.name) / "usage.db"
        tm.PRICE_PATH = Path(self.tmp.name) / "prices.json"
        conn = sqlite3.connect(str(tm.DB_PATH))
        conn.execute("""CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT, ts REAL, site TEXT, model TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, ok INTEGER)""")
        now = time.time()
        rows = [
            (now, "gpt-4o", 1_000_000, 1_000_000),
            (now - 100, "gpt-4o-mini", 2_000_000, 1_000_000),
            (now - 200, "未知模型", 500, 500),
        ]
        for ts, model, pin, pout in rows:
            conn.execute("INSERT INTO usage (ts, model, prompt_tokens, completion_tokens) "
                         "VALUES (?,?,?,?)", (ts, model, pin, pout))
        conn.commit()
        conn.close()

    def tearDown(self):
        tm.DB_PATH, tm.PRICE_PATH = self._orig
        self.tmp.cleanup()

    def test_cost_calc(self):
        self.assertAlmostEqual(tm.cost_of(1_000_000, 1_000_000, [2.5, 10.0]), 12.5)
        self.assertIsNone(tm.cost_of(1, 1, None))

    def test_match_price_fuzzy(self):
        prices = {"gpt-4o": [2.5, 10.0]}
        self.assertEqual(tm.match_price("gpt-4o-2026-01-01", prices), [2.5, 10.0])
        self.assertIsNone(tm.match_price("nope", prices))

    def test_report_by_model(self):
        text = tm.tool_usage_report({"days": 7, "by": "model"})
        self.assertIn("gpt-4o", text)
        self.assertIn("合计", text)
        self.assertIn("无价格", text)

    def test_report_by_day(self):
        self.assertIn("按天", tm.tool_usage_report({"days": 7, "by": "day"}))

    def test_report_bad_group(self):
        self.assertIn("只能是", tm.tool_usage_report({"by": "xxx"}))

    def test_top_models_sorted_by_cost(self):
        text = tm.tool_top_models({"days": 7})
        self.assertIn("gpt-4o", text)
        self.assertLess(text.index("gpt-4o"), text.index("gpt-4o-mini"))

    def test_saving_tips(self):
        text = tm.tool_saving_tips({"days": 7})
        self.assertIn("总花费", text)
        self.assertIn("缓存", text)

    def test_price_list(self):
        self.assertIn("gpt-4o", tm.tool_price_list({}))
        self.assertIn("deepseek", tm.tool_price_list({"keyword": "deepseek"}))
        self.assertIn("没有匹配", tm.tool_price_list({"keyword": "zzzz"}))

    def test_report_empty_range(self):
        self.assertIn("没有用量记录", tm.tool_usage_report({"days": -1}))

    def test_days_arg_defaults(self):
        self.assertEqual(tm._days_arg({}), 30)
        self.assertEqual(tm._days_arg({"days": None}), 30)
        self.assertEqual(tm._days_arg({"days": "7"}), 7)
        self.assertEqual(tm._days_arg({"days": "abc"}), 30)
        self.assertEqual(tm._days_arg({"days": 0}), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
