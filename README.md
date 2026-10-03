# Coding Plan Radar

> Made by OpenLlamas · English (default) · [中文 README](README.zh-CN.md)

A Python tool that crawls the public pricing pages of AI coding subscriptions
(Claude Pro/Max, ChatGPT Plus/Pro, GitHub Copilot, Cursor, Windsurf, GLM Coding
Plan, Kimi, Trae …), turns each vendor's different wording into comparable
numbers, ranks them by value-for-money, and tracks changes over time.

It is deliberately an **analysis aid, not a deal feed**. Prices and quotas are
read from public pages and can be simplified, promotional or outdated — always
verify on the vendor's own site before you buy.

## What it does

| Stage | Module | What comes out |
|-------|--------|----------------|
| Crawl | `fetch.py` | Snapshot of every page (replayable offline, robots-aware) |
| Extract | `extract.py` + `ai.py` | Structured plans: price, currency, quota, features |
| Normalize | `normalize.py` | Monthly USD + estimated monthly capacity |
| Score | `score.py` | Relative value ranking |
| Diff | `diff.py` | Added / removed plans, price and quota changes |
| Report | `report.py` | Markdown + JSON + CSV, EN / 中文 |

Two extraction engines, on purpose:

- **Rule-based** (default, zero dependencies beyond HTML parsing): finds a plan
  when it sees a tier name next to a price, reads `500 requests / 5h`, `1M
  tokens`, `$20/mo`, `¥199/月` … Conservative and auditable.
- **AI-assisted** (optional): hands the page text to any OpenAI-compatible model
  and asks for structured JSON. This is what copes with "Unlimited Tab, 3× Core
  usage, Claude/GPT models …" prose that regexes cannot parse. When the AI call
  fails or is not configured, the tool falls back to rules for that vendor.

## Quickstart

Requires Python 3.10+.

```bash
git clone https://github.com/OpenLlamas/coding-plan-radar.git
cd coding-plan-radar
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

pip install -e ".[dev]"

# rule-based run (no key needed):
coding-plan-radar run --no-ai
```

Open `reports/latest.md` for the ranking, `reports/changes.md` for what moved
since the previous run, `reports/latest.json` for the full auditable data.

### Enable AI extraction

Copy `.env.example` values into your environment (any OpenAI-compatible
endpoint works — vendor-hosted or self-hosted):

```bash
export CPR_AI_BASE_URL="https://api.openai.com/v1"
export CPR_AI_API_KEY="sk-..."
export CPR_AI_MODEL="gpt-4o-mini"

coding-plan-radar run          # now AI-extracts, falling back to rules per vendor
```

### JS-heavy pages

A few pages render prices client-side and look empty to a plain fetch. Install
the optional Playwright backend and ask for it:

```bash
pip install "coding-plan-radar[render]"
playwright install chromium
coding-plan-radar run --vendors bytedance-trae --render
```

## Commands

```text
coding-plan-radar run           crawl → extract → diff → score → report
coding-plan-radar report        re-render reports from the last stored run
coding-plan-radar list-vendors  show the configured catalog
coding-plan-radar check-urls    HEAD every pricing_url and flag drift (404/403/…)
```

Useful flags: `--vendors a,b` (limit the run), `--offline` (reuse local
snapshots, no network), `--no-ai` (rules only), `--render` (Playwright),
`--lang zh` (Chinese report), `--delay` / `--timeout` (politeness and HTTP).

`python -m coding_plan_radar …` is equivalent, and `cpr …` is a short alias.

## Configuration

Everything that steers behavior lives in `config/` — no code edits needed.

**`config/vendors.yaml`** — the crawl targets. Add a vendor by appending an entry:

```yaml
vendors:
  - id: my-vendor              # unique slug, used in --vendors and state files
    name: "My Vendor"
    region: global             # any label; groups report sections
    category: subscription     # subscription | api | free
    currency: USD              # fallback when a price line has no symbol
    pricing_url: https://example.com/pricing
    notes: "Optional caveats for the AI extractor"
```

Set `enabled: false` to park an entry without deleting it. Run
`coding-plan-radar check-urls` periodically: a `WARN` means a page moved or now
blocks the crawler, so fix or drop the entry.

**`config/scoring.yaml`** — the value model. This file *is* the "how do you
define 性价比" question made explicit. Key knobs:

```yaml
fx_to_usd:        { USD: 1.0, CNY: 7.1, EUR: 0.92, GBP: 0.79 }
price:
  free_floor_usd: 0.5          # free tiers can't divide by zero
features:
  weights: { agent: 1.0, cli: 1.0, api_access: 0.8, ide: 0.6,
             model_choice: 0.6, mcp: 0.5, team: 0.4 }
capacity:
  weights: { tokens: 0.6, requests: 0.4 }
value:
  weights: { capacity: 0.7, features: 0.3 }
```

The static `fx_to_usd` rates are a reference, not live FX — update them if
precision matters to you.

## How value is scored

`value_score` is a **relative** index inside one report — the top plan is 100
and the rest scale against it (there is no meaningful absolute "value number").

```
USD/mo      = price ÷ fx_rate, ÷12 if billed annually
capacity    = log-scaled monthly tokens (0.6) + premium requests (0.4)
feature     = 100 × Σ weights of features present ÷ Σ all feature weights
value       = 0.7·capacity + 0.3·feature, divided by max(USD/mo, free_floor)
            = then normalized so the best plan = 100
```

Quotas published per 5h / day / week are multiplied to a monthly figure
(`5h → ×144`, `day → ×30.4`, `week → ×4.35`). That is a *heavy-use ceiling*, and
the report flags it — it is not a promise you can hit every month. Anything the
AI could not read from the page is left empty rather than guessed.

## Output & data layout

```text
reports/    latest.md · latest.json · latest.csv · changes.md   (committed)
data/       snapshots/<vendor>/<ts>.html   every fetch, for replay & audit
            extracted/<vendor>.json         last extraction per vendor
            latest.json / previous.json     two most recent runs, for diffing
```

`data/` is git-ignored; `reports/` is committed so a GitHub Actions schedule can
open a fresh report as a record of what the market looked like that week.

## Ethics & limits

Plain HTTP fetches of public pages only. `robots.txt` is honored by default
(`--ignore-robots` overrides), requests are rate-limited (`--delay`), and the
User-Agent identifies this project. It is an analysis aid — respect each vendor's
terms and the law where you run it.

## Development

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

## License

MIT © 2026 OpenLlamas
