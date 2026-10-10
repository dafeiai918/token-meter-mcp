#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token-meter-mcp · Token 用量与成本分析 MCP
让 AI 直接算账：「这个月花了多少钱」「哪个模型最烧钱」「怎么省」
零依赖 stdio MCP 服务器（读 token-meter 的数据库）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

__version__ = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"

DATA_DIR = Path.home() / ".token-meter"
DB_PATH = DATA_DIR / "usage.db"
PRICE_PATH = DATA_DIR / "prices.json"

DEFAULT_PRICES = {
    "gpt-4o": [2.5, 10.0], "gpt-4o-mini": [0.15, 0.6], "gpt-4.1": [2.0, 8.0],
    "gpt-4.1-mini": [0.4, 1.6], "gpt-3.5-turbo": [0.5, 1.5],
    "claude-sonnet-4": [3.0, 15.0], "claude-opus-4": [15.0, 75.0], "claude-haiku": [0.8, 4.0],
    "deepseek-chat": [0.27, 1.1], "deepseek-reasoner": [0.55, 2.19],
    "qwen-plus": [0.4, 1.2], "qwen-turbo": [0.05, 0.2], "qwen-max": [1.6, 6.4],
    "glm-4": [0.6, 0.6], "gemini-2.0-flash": [0.1, 0.4], "gemini-2.5-pro": [1.25, 10.0],
    "moonshot-v1-8k": [1.68, 1.68],
}


# ================= MCP 内核 =================

class MCPServer:
    def __init__(self, name: str, version: str, tools: list):
        self.name, self.version, self.tools = name, version, tools

    def _send(self, msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _result(self, id_, result) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "result": result})

    def _error(self, id_, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})

    def handle(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        method, id_ = msg.get("method"), msg.get("id")
        if method == "initialize":
            self._result(id_, {"protocolVersion": PROTOCOL_VERSION,
                               "capabilities": {"tools": {"listChanged": False}},
                               "serverInfo": {"name": self.name, "version": self.version}})
        elif method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            return
        elif method == "ping":
            self._result(id_, {})
        elif method == "tools/list":
            self._result(id_, {"tools": [
                {"name": t["name"], "description": t["description"],
                 "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}}
                for t in self.tools]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            tool = next((t for t in self.tools if t["name"] == params.get("name")), None)
            if tool is None:
                self._error(id_, -32602, f"未知工具: {params.get('name')}")
                return
            try:
                out = tool["handler"](params.get("arguments") or {})
                self._result(id_, {"content": [{"type": "text", "text": str(out)}], "isError": False})
            except Exception as exc:
                self._result(id_, {"content": [{"type": "text",
                                                "text": f"错误: {type(exc).__name__}: {exc}"}],
                                   "isError": True})
        elif id_ is not None:
            self._error(id_, -32601, f"不支持的方法: {method}")

    def serve(self) -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            for item in (msg if isinstance(msg, list) else [msg]):
                self.handle(item)


# ================= 数据层 =================

def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"用量数据库不存在：{DB_PATH}\n"
            "先用 relay-proxy 记录日志（--log requests.jsonl），"
            "再用 token-meter import -f requests.jsonl 导入。")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_prices() -> dict:
    prices = {k: list(v) for k, v in DEFAULT_PRICES.items()}
    if PRICE_PATH.exists():
        try:
            prices.update(json.loads(PRICE_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return prices


def match_price(model: str, prices: dict) -> list[float] | None:
    if model in prices:
        return prices[model]
    low = (model or "").lower()
    for key, value in prices.items():
        if key.lower() in low:
            return value
    return None


def cost_of(prompt: int, completion: int, price) -> float | None:
    if not price:
        return None
    return prompt / 1_000_000 * price[0] + completion / 1_000_000 * price[1]


def summarize(days: int, group: str) -> tuple[list[dict], float, float]:
    """返回 (分组明细, 总花费, 未定价token数)"""
    conn = connect()
    since = (datetime.now() - timedelta(days=days)).timestamp()
    column = {"day": "date(ts,'unixepoch','localtime')", "model": "model", "site": "site"}[group]
    rows = conn.execute(f"""
        SELECT {column} AS k, COUNT(*) AS calls,
               SUM(prompt_tokens) AS pin, SUM(completion_tokens) AS pout
        FROM usage WHERE ts >= ? GROUP BY k ORDER BY pin + pout DESC
    """, (since,)).fetchall()
    conn.close()
    prices = load_prices()
    out, total_cost, unpriced = [], 0.0, 0
    for row in rows:
        pin, pout = row["pin"] or 0, row["pout"] or 0
        cost = cost_of(pin, pout, match_price(str(row["k"]), prices))
        if cost is None:
            unpriced += pin + pout
        else:
            total_cost += cost
        out.append({"key": row["k"], "calls": row["calls"], "in": pin, "out": pout, "cost": cost})
    return out, total_cost, unpriced


# ================= 工具 =================

def _days_arg(args: dict) -> int:
    raw = args.get("days")
    if raw is None:
        return 30
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 30


def tool_usage_report(args: dict) -> str:
    days = _days_arg(args)
    group = (args.get("by") or "model").lower()
    if group not in ("day", "model", "site"):
        return "by 只能是 day / model / site。"
    rows, total_cost, unpriced = summarize(days, group)
    if not rows:
        return f"近 {days} 天没有用量记录。"
    label = {"day": "按天", "model": "按模型", "site": "按站点"}[group]
    lines = [f"近 {days} 天用量（{label}）：", "",
             f"{'项目':<24}{'调用':<8}{'输入':<14}{'输出':<14}{'花费'}", "-" * 72]
    total_in = total_out = 0
    for row in rows[:30]:
        total_in += row["in"]
        total_out += row["out"]
        cost = f"${row['cost']:.4f}" if row["cost"] is not None else "无价格"
        lines.append(f"{str(row['key']):<24}{row['calls']:<8}{row['in']:<14,}"
                     f"{row['out']:<14,}{cost}")
    lines += ["-" * 72,
              f"{'合计':<24}{'':<8}{total_in:<14,}{total_out:<14,}${total_cost:.4f}"]
    if unpriced:
        lines.append(f"\n注意：{unpriced:,} 个 token 没有对应价格，未计入金额。")
    return "\n".join(lines)


def tool_top_models(args: dict) -> str:
    days = _days_arg(args)
    rows, _, _ = summarize(days, "model")
    if not rows:
        return f"近 {days} 天没有用量记录。"
    by_cost = sorted([r for r in rows if r["cost"] is not None],
                     key=lambda r: -r["cost"])
    lines = [f"近 {days} 天花费排行：", ""]
    for idx, row in enumerate(by_cost[:10], 1):
        total = row["in"] + row["out"]
        lines.append(f"{idx}. {row['key']}  ${row['cost']:.4f}  "
                     f"({row['calls']} 次调用 · {total:,} tokens)")
    if not by_cost:
        lines.append("（没有可计算价格的模型）")
    return "\n".join(lines)


def tool_saving_tips(args: dict) -> str:
    days = _days_arg(args)
    rows, total_cost, _ = summarize(days, "model")
    lines = [f"近 {days} 天总花费 ${total_cost:.4f}", ""]
    tips = []

    if rows:
        expensive = sorted([r for r in rows if r["cost"] is not None],
                           key=lambda r: -r["cost"])
        if expensive:
            top = expensive[0]
            share = (top["cost"] / total_cost * 100) if total_cost else 0
            if share > 50:
                tips.append(f"「{top['key']}」占了 {share:.0f}% 的花费。"
                            f"把简单任务（翻译、总结、改写）换到便宜模型能省一大截。")
        cheap = [r for r in rows if r["cost"] is not None and r["out"] > 0]
        if len(cheap) >= 2:
            cheapest = min(cheap, key=lambda r: r["cost"] / max(1, r["in"] + r["out"]))
            tips.append(f"你已在用「{cheapest['key']}」这类便宜模型，"
                        f"可以把更多任务分流过去。")
        long_calls = [r for r in rows if r["in"] > 50000]
        if long_calls:
            tips.append("有单次输入超过 5 万 token 的调用。"
                        "长文档先摘要再问答，比整篇塞进去便宜得多。")

    tips += [
        "缓存重复问题：同样的问题问两遍就是花两份钱。",
        "精简系统提示词：每次调用都会重复计费。",
        "用 relay-proxy 记录日志，配合本项目看清单次成本，找出浪费点。",
        "不同任务用不同档位的模型：翻译/分类/抽取用 mini 级就够。",
    ]
    lines += [f"- {t}" for t in tips]
    return "\n".join(lines)


def tool_price_list(args: dict) -> str:
    prices = load_prices()
    keyword = (args.get("keyword") or "").lower()
    items = [(k, v) for k, v in sorted(prices.items()) if not keyword or keyword in k.lower()]
    if not items:
        return f"没有匹配「{keyword}」的模型。"
    lines = ["参考单价（美元 / 每百万 token）：", "",
             f"{'模型':<26}{'输入':<12}{'输出'}", "-" * 50]
    for model, (pin, pout) in items:
        lines.append(f"{model:<26}${pin:<11}${pout}")
    lines += ["", "注意：这是内置参考价，各中转站实际价格可能不同。",
              "要按你实付价格计算，修改 ~/.token-meter/prices.json。"]
    return "\n".join(lines)


TOOLS = [
    {"name": "usage_report",
     "description": "生成 Token 用量与花费报表。用户问「这个月花了多少」"
                    "「我用了多少 token」时使用。",
     "inputSchema": {"type": "object", "properties": {
         "days": {"type": "integer", "description": "统计天数，默认30"},
         "by": {"type": "string", "enum": ["day", "model", "site"],
                "description": "分组维度，默认 model"}}},
     "handler": tool_usage_report},
    {"name": "top_models",
     "description": "花费排行榜。用户问「哪个模型最烧钱」时使用。",
     "inputSchema": {"type": "object", "properties": {
         "days": {"type": "integer"}}},
     "handler": tool_top_models},
    {"name": "saving_tips",
     "description": "根据实际用量给出省钱建议。用户问「怎么省 token 钱」时使用。",
     "inputSchema": {"type": "object", "properties": {
         "days": {"type": "integer"}}},
     "handler": tool_saving_tips},
    {"name": "price_list",
     "description": "查看内置模型参考单价。用户问「xx模型多少钱」时使用。",
     "inputSchema": {"type": "object", "properties": {
         "keyword": {"type": "string", "description": "按模型名过滤"}}},
     "handler": tool_price_list},
]


def main() -> int:
    if os.name == "nt":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    MCPServer("token-meter-mcp", __version__, TOOLS).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
