# token-meter-mcp · Token 用量与成本分析 MCP

**让 AI 直接算账：「这个月花了多少钱？」「哪个模型最烧钱？」「怎么省？」**

零依赖 · 单文件 · stdio MCP 服务器 · 中文

---

## 它让 AI 能做什么

> 「我这个月在 AI 上花了多少钱？」
> → AI 调用 `usage_report`，给出按模型/按天/按站点的花费表

> 「哪个模型最烧钱？」
> → AI 调用 `top_models`，花费排行

> 「怎么省点钱？」
> → AI 调用 `saving_tips`，**基于你的真实用量**给建议（不是泛泛而谈）

> 「gpt-4o 什么价？」
> → AI 调用 `price_list`

---

## 提供的工具

| 工具 | 作用 |
|---|---|
| `usage_report` | 用量报表（可 `by=day/model/site`） |
| `top_models` | 花费排行 Top 10 |
| `saving_tips` | 省钱建议（结合你的实际数据） |
| `price_list` | 内置参考价表 |

---

## 安装

```json
{
  "mcpServers": {
    "token-meter-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/dafeiai918/token-meter-mcp", "token-meter-mcp"]
    }
  }
}
```

零第三方依赖，Python 3.9+。

---

## 数据从哪来

```bash
# 1. 用 relay-proxy 记录每次调用
python relay_proxy.py -u https://api.example.com/v1 -k sk-xxx --log requests.jsonl

# 2. 导入
python token_meter.py import -f requests.jsonl
```

之后 AI 就能通过本 MCP 直接分析你的用量。

---

## 省钱建议是怎么给的

不是套模板，而是**读你的真实数据**：

| 发现 | 建议 |
|---|---|
| 某模型占 >50% 花费 | 「把简单任务分流到便宜模型」 |
| 有单次输入 >5 万 token | 「长文档先摘要再问答」 |
| 已在用便宜模型 | 确认并建议扩大使用比例 |

再加几条通用建议：缓存重复问题、精简系统提示词、按任务分档选模型。

---

## 价格说明

- 内置参考价（美元/百万 token），**各中转站实际价格不同**
- 要按实付价计算，改 `~/.token-meter/prices.json`
- 没有价格的模型仍统计 token，只是不显示金额

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [token-meter](https://github.com/dafeiai918/token-meter) | 命令行版看板 |
| [relay-proxy](https://github.com/dafeiai918/relay-proxy) | 产出用量日志的本地代理 |
| [price-radar-mcp](https://github.com/dafeiai918/price-radar-mcp) | 比价与选型 MCP |
| [key-watch](https://github.com/dafeiai918/key-watch) | 余额监控与告警 |

---

## License

MIT
