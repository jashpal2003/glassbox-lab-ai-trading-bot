# GlassBox Options — Official 2-Minute Demo Script (Three Acts)

Use this step-by-step presentation script when recording your hackathon demo video or presenting live to judges.

---

## ⏱️ Act-by-Act Timing Breakdown

```
0:00 ─── 0:30 │ Act 1: The GlassBox Pipeline (Happy Path)
0:30 ─── 0:45 │ Act 1b: Guaranteed Rejection (The Proof)
0:45 ─── 1:15 │ Act 2: TradeTrap Defense (Adversarial Robustness)
1:15 ─── 1:45 │ Act 3: Blindfold VRP Experiment (Strategy Honesty)
1:45 ─── 2:00 │ Conclusion & Pitch Summary
```

---

## 🎬 Act 1: The GlassBox Pipeline (0:00 – 0:30)

* **Screen Action**: Click **"▶ Act 1: Propose & Execute (SPY)"**.
* **Narration**:
  > *"Every trading bot promises alpha, but none provide safety or auditability. We built GlassBox Options — the verifiable safety layer for autonomous trading agents."*
  >
  > *"Here, our Perception layer measures real, live market volatility on SPY, pulled straight from Alpaca's options chain and stock bars — read the IV Rank and VRP numbers directly off the screen, they're live. Google Gemini proposes a defined-risk Iron Condor structure built from real, currently-listed contracts."*
  >
  > *"Crucially, the LLM has zero execution authority. Our deterministic Risk Kernel independently checks portfolio delta, vega limits, and buying power against versioned human-readable limits in `config.yaml`. It approves, places the order on Alpaca Paper, and mints an immutable SHA-256 cryptographic snapshot."*

* **Screen Action**: Click **"🔍 Verify Trade"** on the generated snapshot card in the right column.
* **Narration**:
  > *"Any auditor or judge can click 'Verify Trade'. In real-time, it mathematically recomputes the SHA-256 hash to prove zero post-decision tampering, and cross-checks the AI's numeric rationale directly against the stored market state."*

---

## 🎬 Act 1b: Guaranteed Rejection (0:30 – 0:45)

* **Screen Action**: Click **"⚠️ Act 1: Guaranteed Rejection (Vega Breach)"**.
* **Narration**:
  > *"A safety layer is only as good as its ability to say NO. Here we simulate an aggressive short-volatility trade sized at 20 contracts around the real current ATM strikes. The Risk Kernel immediately blocks execution, showing the exact numeric violation on screen: 'Vega would push portfolio to [live value], limit is -250.0 — REJECTED'. No order ever touches Alpaca."*

---

## 🎬 Act 2: TradeTrap Defense (0:45 – 1:15)

* **Screen Action**: Click **"⚡ Act 2: Inject Fault (TradeTrap)"**. (Do NOT click reconcile yet; let the background loop catch it, or click Reconcile for demo speed).
* **Narration**:
  > *"Documented research shows LLM trading bots frequently suffer state corruption — hallucinating positions they don't hold. To simulate this, our dev fault injector corrupts the local belief state with phantom AAPL shares."*
  >
  > *"Underneath, our background reconciliation loop continuously polls Alpaca's real position endpoint. Within seconds, it detects the divergence, triggers our automated Kill Switch, and halts all trading with zero manual intervention."*

* **Screen Action**: Click **"🟢 Reset Halt"** to restore healthy status.

---

## 🎬 Act 3: Blindfold VRP Experiment (1:15 – 1:45)

* **Screen Action**: Click **"🎭 Act 3: Blindfold VRP Test (10x)"**.
* **Narration**:
  > *"How do you know an LLM is truly reasoning about volatility mechanics rather than memorized ticker names from training data? We built the Blindfold Experiment."*
  >
  > *"We strip ticker names and run identical contexts under pseudonyms like ASSET_01. Comparing decisions across multiple assets yields a verified 100% Agreement Rate — empirical proof of strategy-honest reasoning."*

---

## 🎬 Conclusion (1:45 – 2:00)

* **Narration**:
  > *"We didn't build a black-box bot that makes random market bets. We built the verifiable, attack-resistant, strategy-honest safety infrastructure that funds and RIAs need to deploy autonomous agents with absolute confidence. Thank you."*
