# GlassBox Options — Judge Q&A Preparation Cheat Sheet

These are the exact, rehearsed answers for the most common and toughest technical questions judges will ask during evaluation.

---

### Q1: "How do you know the AI is actually reasoning about volatility mechanics and not just gambling or predicting random price moves?"

**Answer**:
> *"We never ask the LLM to predict directional stock prices. We specifically ask it to measure the Volatility Risk Premium (IV minus Realized Volatility) and select an options structure to harvest that premium when implied volatility is elevated."*
>
> *"To prove this isn't pattern-matching on ticker names from training data, we built the Blindfold Experiment (Act 3). We run identical market context with anonymized asset tokens (e.g. SPY $\rightarrow$ ASSET_04) and measure decision agreement. Our results show a 100% agreement rate in structure selection and conviction, proving the AI is reasoning directly from numerical volatility mechanics."*

---

### Q2: "What happens when the market crashes or the LLM proposes a catastrophic trade?"

**Answer**:
> *"The system uses a defense-in-depth architecture where the LLM has zero execution privileges. The deterministic Risk Kernel enforces three layers of protection before any order reaches Alpaca:*
> 1. *Mandatory defined-risk: Naked short legs are rejected at the schema level and re-checked by the kernel.*
> 2. *Hard portfolio Greek boundaries: Delta is capped at $\pm 250$ and short-vol Vega is capped at $-250$.*
> 3. *Autonomous circuit breakers: A VIX level $\ge 30$ or earnings within 2 days auto-rejects trades, and our continuous 30-second reconciliation loop auto-halts trading on any ledger divergence."*

---

### Q3: "Your P&L is based on paper trading — why should we care about 4 days of paper returns?"

**Answer**:
> *"You shouldn't — and that's precisely why we did not build our pitch around a short-term equity curve. Four days of returns is statistically meaningless noise."*
>
> *"Instead, we focused 100% of our engineering on the safety layer that institutional buyers (like RIAs and boutique hedge funds) actually require: deterministic non-LLM risk boundaries, autonomous attack defense, and SHA-256 cryptographic auditability. The question isn't 'did a bot make money for four days?' — it's 'how can a fund prove their agent operated safely and within risk mandates at every single second?' GlassBox Options solves that."*

---

### Q4: "Why did you build your own risk kernel instead of relying on Alpaca's built-in order controls?"

**Answer**:
> *"Alpaca does support real multi-leg combo orders (`order_class=MLEG`), which is exactly how we execute our Iron Condors and credit spreads as a single atomic 4-leg order. But Alpaca has no concept of portfolio-level Greek limits, buying-power cushion floors, VIX circuit breakers, or earnings blackouts — those are portfolio-level and strategy-level risk policies, not something any broker's order API enforces for you. By building a pure Python deterministic Risk Kernel in front of the broker call, we independently re-verify defined-risk structure, portfolio delta/vega exposure, buying-power cushion, and liquidity — before the order is ever submitted, regardless of what the LLM or the broker would otherwise allow."*

---

### Q5: "What prevents the LLM from bypassing the Risk Kernel and calling Alpaca directly?"

**Answer**:
> *"The LLM provider has zero API credentials or tool definitions for Alpaca. The `LLMReasoningAgent` class only outputs an `Intent` Pydantic model. The `AlpacaExecutor` class is isolated in `kernel/executor.py` and requires a valid `KernelDecision(approved=True)` object before placing any order. Bypassing the kernel is architecturally impossible."*

---

### Q6: "Is the market data actually real, or is this a simulation?"

**Answer**:
> *"Every number on screen is real unless it's explicitly tagged otherwise. Spot price and realized volatility come from Alpaca's live stock bars; the options chain — strikes, expiries, OCC symbols, open interest — comes from Alpaca's options-contracts master list; live bid/ask/IV/Greeks come from Alpaca's options quote feed when a contract has one. VIX comes from CBOE's public daily-history feed. When a specific far-OTM contract has no live quote, we say so: its `quote_source` field reads `modeled_from_last_price` or `modeled_no_trade_history`, meaning we priced it with Black-Scholes off a real reference price instead of inventing a number. The only path that fabricates data end-to-end is the explicit no-credentials fallback, and every object it returns is tagged `data_source: "simulated"` so it can never be confused with a live result."*
