# GlassBox Options

> **Most AI trading agents ask a model what to trade. GlassBox asks a harder question first: has this strategy earned the right to trade at all?**

[![Tests](https://img.shields.io/badge/offline%20tests-68%20passing-brightgreen.svg)](tests/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Hackathon](https://img.shields.io/badge/lablab.ai-Alpaca%20AI%20Trading%20Agents-indigo.svg)](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)

---

## The One-Sentence Pitch

**GlassBox Options is a self-learning options trading agent whose strategies must prove themselves
against real historical evidence before the AI is even allowed to propose them — and whose every
resulting order is gated by a deterministic, non-LLM risk kernel and recorded as a tamper-evident
cryptographic receipt.**

Two independent boundaries, and neither one is an LLM:

| | The Strategy Lab | The Risk Kernel |
|---|---|---|
| **Job** | Decides what *deserves consideration* | Decides what is *allowed to execute* |
| **Behaviour** | Learns and evolves | Never learns. Never changes at runtime. |
| **Powered by** | Real historical replay | Ten fixed numeric limits in `kernel/config.yaml` |

---

## Architecture

```
                          ALPACA  (market data + paper broker)
                                     |
                                     v
   PERCEPTION      real bars, real options chain, real OI/Greeks, CBOE VIX
        |          + trend & vol-term-structure signals from the same bars
        v
   REGIME ENGINE   deterministic, 6 named regimes, fails closed when VIX is missing
        |
        v
   STRATEGY ARENA  6 strategy variants replayed over the SAME real history,
        |          ranked by a published composite score.
        |          Only variants eligible for the CURRENT regime may win.
        v
   REASONING       LLM proposes a structured Intent, constrained by arena evidence
        |          (Gemini -> OpenRouter -> deterministic rules; never fabricates)
        v
   RISK KERNEL     PURE, NO LLM. 10 enforced limits. APPROVE or REJECT.
        |
   +----+-----------------------------+
   | APPROVED                  REJECTED
   v                                  v
   EXECUTION (real MLEG order)    AUDIT ONLY
   |                                  |
   +----------------+-----------------+
                    v
        SHA-256 AUDIT SNAPSHOT  ->  independent replay verifier
                    |
                    v
        RECONCILIATION LOOP (30s) -> KILL SWITCH on any broker/ledger divergence
                    |
                    v
        LEARNING ENGINE -> post-trade reflection -> adaptive parameters
```

---

## What makes it different

### Strategy Arena — strategies compete for the right to trade
The agent maintains a **population** of strategy variants (iron condors at different wing widths
and profit targets, put/call credit spreads, a debit spread, a long strangle). Every variant is
replayed over the **same real historical closes** and scored on a published composite:

```
35% expectancy + 20% Sharpe + 15% win rate + 15% drawdown (inverted) + 15% deflated Sharpe
```

The champion must be **eligible for the current regime AND out-score the field**. Eligibility is
set by deterministic code, never by the score — an ineligible variant cannot win even with the
best numbers, and there is a test asserting exactly that. Losing variants stay on the leaderboard
with their real numbers so the selection is inspectable rather than a black box.

> **Disclosed bias:** entry premium is modeled as `IV = trailing realized vol x 1.15`, because free
> granular historical options pricing does not exist. That structurally flatters premium *sellers*.
> Cross-variant ranking within a family is meaningful; absolute returns and short-vs-long-vol
> comparisons are not. The dashboard states this on the Arena tab rather than hiding it.

### Regime Engine — six regimes, from real numbers only
`HIGH_VOL_RANGE`, `HIGH_VOL_TREND`, `LOW_VOL_TREND`, `LOW_VOL_RANGE`, `VOL_EXPANSION`, `EVENT_RISK`
— classified from IV rank, VRP, real CBOE VIX, 20-session trend, EMA stretch and the 10d/60d
realized-vol expansion ratio. **A missing VIX classifies as `EVENT_RISK`, not as "probably fine".**

### Nothing is ever invented
This is the project's core discipline, enforced throughout:

- No LLM reachable → deterministic rules, and the output is **labelled** as such.
- No live option quote → Black-Scholes from a real reference price, tagged via `quote_source`.
- No VIX → the kernel **rejects**; it does not substitute a plausible constant.
- No historical bars → the arena reports "unavailable" rather than scoring on nothing.
- The dashboard renders `—` while loading. It never shows a placeholder number that looks real.
- The provider chip reports **READY** (configured, unverified) versus **LIVE** (a call actually
  succeeded), because an exhausted API key looks perfectly configured until it returns 429.

---

## The three live demos

**Act 1 — the gate actually bites.** A real trade runs the full path and is approved and executed.
Then an intentionally oversized short-vol structure is proposed. It is rejected with the exact
numbers it broke (e.g. `Position size 15.6% exceeds max 5.0%`, `Vega would push portfolio to
-396.9, limit is -250.0`). The rejection is sized dynamically against the account's **actual**
current vega, so it is a genuine breach every time rather than a scripted one.

**Act 2 — TradeTrap.** A phantom position is injected into the local ledger only. The 30-second
reconciliation loop compares believed vs. real broker state, detects the divergence and fires the
kill switch with no human in the loop. Clearing the fault restores a clean, tradable state.

**Act 3 — Blindfold.** The same real numbers are put to the model twice: once as `SPY`, once as
`ASSET_04`. Matching structure and conviction is evidence the model is reasoning from volatility
mechanics rather than a memorised ticker.

**Or press one button.** The **Judge Demo** tab runs all of it end to end as a 12-step timeline —
perception, regime, tournament, reasoning, kernel, audit, replay verification, guaranteed
rejection, TradeTrap halt, recovery, and the measured learning state. Every step is the real
production code path, not an animation.

---

## Quickstart

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
```

| Variable | Required? | What happens without it |
|---|---|---|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Recommended | Falls back to a clearly-labelled simulated feed (`data_source: "simulated"`) |
| `OPENROUTER_API_KEY` | **Recommended** | See below |
| `GEMINI_API_KEY` | Optional | Falls through to OpenRouter |

**On LLM providers:** every LLM feature — Intent generation, post-trade reflection, and the copilot
chat — goes through one client that tries **Gemini first, then OpenRouter**, then deterministic
rules. Configuring a second provider is what keeps the AI panels alive when the first key is rate
limited or out of credit. `OPENROUTER_MODEL` accepts any OpenRouter model slug.

### 3. Run
```bash
python run.py
```
The entrypoint reads `PORT` from `.env` and **automatically shifts to the next free port** if that
one is taken, printing the URL it actually bound to. Use `python run.py --strict` to fail instead.

### 4. Test
```bash
pytest tests/ --ignore=tests/test_api.py -q     # 68 offline tests, no side effects
```

> ⚠️ `tests/test_api.py` exercises the **real** Alpaca paper account: it places a real multi-leg
> order and closes real profitable positions. Paper money, but real side effects. It is excluded
> from the command above deliberately.

---

## Dashboard

| Tab | What it shows |
|---|---|
| **Terminal** | Live chart, real options chain, payoff and volatility-smile curves |
| **Studio** | Arena-gated pipeline with a **dry-run** toggle, all 10 live kernel checks, AI copilot |
| **Memory** | Win rate, realized P&L, adaptive parameters, post-trade reflections (labelled by provider) |
| **Holdings** | Live Alpaca positions and orders, profit-target harvesting |
| **Audit** | SHA-256 snapshots with independent replay verification |
| **Backtest** | Real historical replay with Deflated Sharpe Ratio |
| **Arena** | Regime engine signals + the strategy tournament leaderboard |
| **TradeTrap** | Fault injector, kill switch, reconciliation log |
| **Blindfold** | Ticker-blindness honesty experiment |
| **Demo** | The whole story, one button |

---

## Frozen data contracts

1. **`Intent`** (`shared/schemas.py`) — schema-validated legs, structures, and numeric rationale.
2. **`kernel/config.yaml`** — versioned, human-readable risk limits. Limits that are declared but
   *not* enforced (e.g. `min_daily_volume`, which no free feed supports) say so explicitly, and the
   dashboard reports the count the kernel **actually** enforces.
3. **`AuditSnapshot`** (`verification/snapshot.py`) — SHA-256 over canonical
   `market_state + account_state + intent_id`.
4. **`ReconciliationEvent`** (`verification/reconciliation.py`) — believed state vs. broker truth.

---

## Documentation

| Guide | Description |
| :--- | :--- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module deep dive, sequence diagrams, mathematical models |
| [SYSTEM_WORKFLOW.md](docs/SYSTEM_WORKFLOW.md) | Step-by-step trace of pipeline, hashing, reconciliation |
| [DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Presentation script with timing cues |
| [JUDGE_QA.md](docs/JUDGE_QA.md) | Rehearsed answers to the toughest technical questions |

---

## Built for
**Alpaca AI Trading Agents Hackathon — lablab.ai**
*Track: Verifiable Autonomous Trading & Robust Risk Architecture*

All trading occurs on **Alpaca paper trading**. No real capital is at risk.
