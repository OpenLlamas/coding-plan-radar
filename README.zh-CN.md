# 编码套餐雷达（Coding Plan Radar）

> OpenLlamas 出品 · [English README](README.md)（默认版本）· 中文

一个 Python 工具：爬取各家 AI 编码订阅的公开定价页（Claude Pro/Max、ChatGPT
Plus/Pro、GitHub Copilot、Cursor、Windsurf、智谱 GLM Coding Plan、Kimi、Trae 等），
把每家不同的表述口径换算成可比较的数字，按性价比排序，并追踪随时间的变化。

它是**分析助手，不是比价导购**。价格与额度都读自公开页面，可能是简化、促销或过期
的——下单前请以厂商官网为准。

## 它做什么

| 阶段 | 模块 | 产出 |
|------|------|------|
| 抓取 | `fetch.py` | 每个页面的快照（可离线复放、遵守 robots） |
| 提取 | `extract.py` + `ai.py` | 结构化套餐：价格、币种、额度、功能 |
| 归一 | `normalize.py` | 每月美元价 + 估算的每月容量 |
| 评分 | `score.py` | 相对性价比排行 |
| 变化 | `diff.py` | 新增/下架套餐、价格与额度变动 |
| 报告 | `report.py` | Markdown + JSON + CSV，中英双语 |

两套提取引擎，有意为之：

- **规则引擎**（默认，除 HTML 解析外无额外依赖）：在页面里看到「档位名 + 价格」就
  识别一个套餐，能读 `500 requests / 5h`、`1M tokens`、`$20/mo`、`¥199/月` 等。
  保守、可复核。
- **AI 引擎**（可选）：把页面正文交给任意 OpenAI 兼容模型，要求返回结构化 JSON。
  这正是规则搞不定的「无限 Tab、3× 核心额度、含 Claude/GPT 模型……」这类散文的克星。
  当某厂商的 AI 调用失败或未配置时，该厂商自动回退到规则引擎。

## 快速上手

需 Python 3.10+。

```bash
git clone https://github.com/OpenLlamas/coding-plan-radar.git
cd coding-plan-radar
python -m venv .venv
# Windows PowerShell：
.venv\Scripts\Activate.ps1
# Linux / macOS：
source .venv/bin/activate

pip install -e ".[dev]"

# 规则模式跑一遍（无需任何 key）：
coding-plan-radar run --no-ai
```

打开 `reports/latest.md` 看排行，`reports/changes.md` 看自上次运行以来变了什么，
`reports/latest.json` 看全量可复核数据。

### 启用 AI 提取

把 `.env.example` 里的值导入环境（任意 OpenAI 兼容端点均可——官方或自建）：

```bash
export CPR_AI_BASE_URL="https://api.openai.com/v1"
export CPR_AI_API_KEY="sk-..."
export CPR_AI_MODEL="gpt-4o-mini"

coding-plan-radar run          # 现在走 AI 提取，失败时逐厂商回退规则
```

### JS 动态渲染页面

少数页面在前端才渲染出价格，纯抓取看到的是空的。装可选的 Playwright 后端并启用：

```bash
pip install "coding-plan-radar[render]"
playwright install chromium
coding-plan-radar run --vendors bytedance-trae --render
```

## 命令

```text
coding-plan-radar run           抓取 → 提取 → 变化 → 评分 → 报告
coding-plan-radar report        用最近一次存储的运行结果重新生成报告
coding-plan-radar list-vendors  打印已配置的厂商清单
coding-plan-radar check-urls    对每个 pricing_url 发 HEAD，标记漂移（404/403/…）
```

常用参数：`--vendors a,b`（限定本次运行）、`--offline`（复用本地快照，不联网）、
`--no-ai`（仅规则）、`--render`（Playwright）、`--lang zh`（中文报告）、
`--delay` / `--timeout`（限速与 HTTP 超时）。

`python -m coding_plan_radar …` 等价，`cpr …` 是短别名。

## 配置

决定行为的东西都在 `config/` 里，无需改代码。

**`config/vendors.yaml`** —— 抓取目标。追加一条即新增一个厂商：

```yaml
vendors:
  - id: my-vendor              # 唯一 slug，用于 --vendors 与状态文件名
    name: "我的厂商"
    region: global             # 任意标签；用来给报告分组
    category: subscription     # subscription | api | free
    currency: USD              # 价格行没有货币符号时的兜底
    pricing_url: https://example.com/pricing
    notes: "给 AI 提取器的可选提示"
```

把某条设为 `enabled: false` 可临时停用而不删除。建议定期跑
`coding-plan-radar check-urls`：出现 `WARN` 说明页面搬家或开始拒绝爬虫，就该修正或
删掉这条。

**`config/scoring.yaml`** —— 价值模型。这份文件就是把「你如何定义性价比」显式写出来。
关键旋钮：

```yaml
fx_to_usd:        { USD: 1.0, CNY: 7.1, EUR: 0.92, GBP: 0.79 }
price:
  free_floor_usd: 0.5          # 免费档不能除以零
features:
  weights: { agent: 1.0, cli: 1.0, api_access: 0.8, ide: 0.6,
             model_choice: 0.6, mcp: 0.5, team: 0.4 }
capacity:
  weights: { tokens: 0.6, requests: 0.4 }
value:
  weights: { capacity: 0.7, features: 0.3 }
```

`fx_to_usd` 是静态参考汇率，不是实时行情——若在意精度请手动更新。

## 性价比如何评分

`value_score` 是**单份报告内**的相对指数——榜首为 100，其余相对缩放（不存在有意义的
绝对「价值数字」）。

```
USD/mo    = 价格 ÷ 汇率，年付再 ÷12
capacity  = 取对数归一的每月 tokens（0.6）+ 高级 requests（0.4）
feature   = 100 × Σ命中功能权重 ÷ Σ全部功能权重
value     = 0.7·capacity + 0.3·feature，除以 max(USD/mo, free_floor)
          = 再归一使最优套餐 = 100
```

以 5h/天/周 公布的额度会乘系数折算成月值（`5h→×144`、`天→×30.4`、`周→×4.35`）。
这是**重用上界**，报告会明确标注——不代表你每月都能打满。AI 从页面读不出的字段一律
留空，绝不臆造。

## 输出与数据布局

```text
reports/    latest.md · latest.json · latest.csv · changes.md   （纳入版本库）
data/       snapshots/<vendor>/<ts>.html   每次抓取，用于复放与复核
            extracted/<vendor>.json        每厂商最近一次提取结果
            latest.json / previous.json    最近两次运行，用于变化比对
```

`data/` 被 git 忽略；`reports/` 纳入版本库，这样 GitHub Actions 定时任务能把每周新报告
作为「本周市场长什么样」的记录提交上去。

## 边界与合规

只发普通 HTTP 请求抓公开页面。默认遵守 `robots.txt`（`--ignore-robots` 可关闭），
请求限速（`--delay`），User-Agent 标明本项目身份。它是分析助手——请在运行时尊重各
厂商的服务条款与所在地法律。

## 开发

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

## 许可

MIT © 2026 OpenLlamas
