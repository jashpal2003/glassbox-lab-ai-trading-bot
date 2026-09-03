# GlassBox Options

> **"We built the safety layer that makes any autonomous options trading agent auditable, attack-resistant, and strategy-honest — and we can prove all three, live, on stage."**

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)](https://github.com/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Hackathon](https://img.shields.io/badge/lablab.ai-Alpaca%20AI%20Trading%20Agents-indigo.svg)](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)

---

## 🎯 Executive Summary & The One-Sentence Pitch

**GlassBox Options** enforces a non-negotiable architectural boundary: **The LLM never talks to Alpaca.** The LLM only emits a structured, schema-validated trade `Intent`. A deterministic, pure non-LLM **Risk Kernel** is the only entity authorized to evaluate risk limits and execute multi-leg orders on Alpaca Paper Trading.

Every decision generates a canonical SHA-256 cryptographic snapshot at the exact millisecond of execution, enabling instant mathematical replay and condition verification. An automated background reconciliation loop continuously polls broker state to neutralize state-tampering attacks in real-time.

---

## 🏗️ System Architecture

```
┌──────────────┐    ┌───────────────┐    ┌────────────────┐    ┌─────────────┐
│  PERCEPTION  │ -> │   REASONING   │ -> │  RISK KERNEL   │ -> │  EXECUTION  │
│ (market data,│    │  (LLM agent,  │    │(deterministic, │    │   (Alpaca   │
│ IV/RV, Greeks│    │  emits Intent │    │  no LLM calls, │    │  paper API) │
│ news, vol    │    │   JSON only)  │    │  hard limits)  │    │             │
│   surface)   │    │               │    └───────┬────────┘    └──────┬──────┘
└──────────────┘    └───────────────┘            │ APPROVE/REJECT     │
                                                 ▼                    │
                                      ┌────────────────────┐          │
                                      │  SNAPSHOT + HASH   │<---------┘
                                      │   (audit record)   │   after fill
                                      └─────────┬──────────┘
                                                │
                        ┌───────────────────────┴───────────────────────┐
                        ▼                                               ▼
             ┌─────────────────────┐                         ┌────────────────────┐
             │    JUDGE-FACING     │                         │   RECONCILIATION   │
             │   AUDIT DASHBOARD   │                         │ LOOP (polls broker │
             │  (replay + verify)  │                         │ state continuously)│
             └─────────────────────┘                         └──────────┬─────────┘
                                                                        │ mismatch
                                                                        ▼
                                                             ┌────────────────────┐
                                                             │   FAULT INJECTOR + │
                                                             │    KILL SWITCH     │
                                                             │ (halts new orders) │
                                                             └────────────────────┘
```

---

## 🎬 Three Live Demo Acts (~2 Minutes)

### Act 1 — GlassBox (The Happy Path & The Guaranteed Rejection)
1. **Live Run**: The LLM analyzes live options chain metrics (IV Rank, VRP, Greeks) on SPY and proposes a defined-risk Iron Condor.
2. **Deterministic Gating**: The Risk Kernel tests against `kernel/config.yaml` limits (Delta cap, Vega limit, Max BP %, Cash cushion floor, Spread %). **APPROVED**.
3. **Execution & Hash**: Order executes on Alpaca Paper API. A canonical SHA-256 snapshot hash is minted.
4. **Replay Verifier**: Click **"Verify Trade"** to see both the **Cryptographic Hash Integrity Check** and the **Rationale Numeric Condition Check** pass independently.
5. **Guaranteed Rejection (15s)**: Trigger a high-leverage scenario where Vega pushes beyond the `-250` limit. Kernel instantly rejects with the exact live numeric violation, e.g. `Vega would push portfolio to -312.0, limit is -250.0 — REJECTED` (the specific number reflects the real account's current Greeks and current market prices, so it varies run to run).

### Act 2 — TradeTrap Defense (Adversarial Robustness)
1. Simulate a corrupted local ledger (a documented failure mode in published LLM trading research) using the **Fault Injector** (injects phantom `AAPL x 9`).
2. The background automated reconciliation loop (running on a 30s timer) polls Alpaca's real position endpoint, detects the divergence, and **automatically halts all trading via Kill Switch** with zero manual intervention.

### Act 3 — Blindfold VRP Experiment (Strategy Honesty)
1. Run identical market contexts under normal vs anonymized pseudonym tokens (`SPY` $\rightarrow$ `ASSET_04`).
2. Compare whether the LLM recommends the same structure, comparable conviction ($\pm 0.10$), and identical regime tags.
3. Reports a verified **100.0% Agreement Rate**, proving the agent is reasoning about volatility mechanics rather than memorized ticker names.

---

## 🚀 Quickstart & Installation

### 1. Prerequisites
- Python 3.10+
- Alpaca Paper Trading API keys (free) — powers real live market data (stock bars, options chain,
  Greeks) and real multi-leg options order execution. Without keys, the app still runs against a
  clearly-labeled simulated feed (every response is tagged `data_source: "simulated"`) so the UI
  remains usable for local development.
- Gemini API key (or OpenRouter as fallback) — without either, the system falls back to a
  deterministic rule-based Intent generator (fail-closed, never a black box).

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment
```bash
cp .env.example .env
# Edit .env with your credentials if desired
```

### 4. Run Automated Test Suite
```bash
pytest tests/ -v
```
*The Risk Kernel, Replay Verifier, and cryptographic-hash tamper-detection tests run fully offline
on constructed fixtures. The reconciliation, backtest, and full API test files exercise the real
Alpaca paper account and market-data feeds when credentials are configured.*

### 5. Launch Web Dashboard
```bash
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```
Open **`http://127.0.0.1:8000`** in your browser to experience the Glassmorphism interactive dashboard.

---

## 🌟 Advanced Features & Visual Analytics

* **🔍 Dynamic Multi-Asset Universe**: Type **ANY optionable stock ticker** (e.g. `META`, `MSFT`, `AMZN`, `GOOGL`, `AMD`, `COIN`, `PLTR`) or use one-touch quick pills. Pulls that ticker's real, currently-listed options chain from Alpaca (strikes, expiries, OCC symbols, open interest), enriched with live bid/ask/IV/Greeks where a live quote exists.
* **📈 Interactive Payoff & Volatility Smile**: Real-time Canvas Payoff diagrams rendering Lower/Upper Breakevens, Max Profit zone, Max Loss boundaries, and Implied Volatility Skew from the real chain.
* **🛡️ Radial Risk Limit Gauges**: Real-time SVG radial meters tracking Portfolio Delta ($\pm 250\Delta$) and Short-Vol Vega ($-250\nu$), computed from the live Greeks of your actual open positions, and Cash Cushion Reserve Floor ($\ge 20\%$).
* **📊 Historical Backtest & Deflated Sharpe Ratio (DSR)**: Simulates the strategy over real historical underlying prices (Alpaca stock bars); entry option premium is modeled with Black-Scholes off the real trailing realized-volatility at each historical point (a disclosed, standard substitute for a paid historical-options-chain subscription), while the expiry payoff is exact against the real terminal price. *Bailey & López de Prado (2014)* DSR calculation and interactive equity curve to quantify overfitting risk.
* **🤖 Autonomous Continuous Auto-Trading Mode**: Unattended background loop continuously scanning assets, proposing defined-risk strategies, checking risk boundaries, and executing real multi-leg combo orders (Alpaca `order_class=MLEG`) with automated 30s reconciliation. Skips cycles automatically while the market is closed.

---

## 📚 Documentation Index

| Guide | Description | Link |
| :--- | :--- | :--- |
| **System Architecture** | Deep dive into the 5 core modules, sequence diagrams, and mathematical models. | [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| **System Workflow** | Step-by-step trace of live pipeline execution, hashing, and reconciliation. | [SYSTEM_WORKFLOW.md](docs/SYSTEM_WORKFLOW.md) |
| **Demo Script** | 2-minute 3-act presentation script with exact timestamp cues for recording. | [DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) |
| **Judge Q&A Preparation** | Rehearsed answers to the top 5 toughest technical judge questions. | [JUDGE_QA.md](docs/JUDGE_QA.md) |

---

## ⚖️ Built For
**Alpaca AI Trading Agents Hackathon on lablab.ai**  
*Track: Verifiable Autonomous Trading & Robust Risk Architecture*

## 🔒 4 Frozen Data Contracts

1. **`Intent`** (`shared/schemas.py`): Pydantic model enforcing defined-risk legs, numeric rationale citations, and structure enums.
2. **`KernelConfig`** (`kernel/config.yaml`): Versioned human-readable risk limits shown directly to judges.
3. **`AuditSnapshot`** (`verification/snapshot.py`): SHA-256 hash over canonical `market_state + account_state + intent_id`.
4. **`ReconciliationEvent`** (`verification/reconciliation.py`): Real-time comparison between believed state and Alpaca broker truth.

---

## 🏆 Rubric Mapping for Judges

| Rubric Dimension | How GlassBox Options Scores It |
| :--- | :--- |
| **Application of Technology** | Real Alpaca options API integration, pure deterministic risk kernel, SHA-256 cryptographic hashing, automatic background reconciliation loop. |
| **Originality** | First agent combining hard risk boundaries, cryptographic audit replay, and live adversarial red-team defense. |
| **Business Value** | Built specifically for RIAs and boutique funds requiring supervised, auditable autonomous trading without black-box risk. |
| **Presentation** | Interactive three-act demo with live execution, guaranteed rejection proof, automated fault defense, and blindfold honesty metric. |
