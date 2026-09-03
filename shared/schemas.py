"""
shared/schemas.py - The single source of truth data contracts for GlassBox Options.
Frozen contracts as specified in Section 4 of the Developer Build Specification.
"""

from typing import List, Dict, Optional, Literal, Any
from pydantic import BaseModel, Field, model_validator
from datetime import datetime, timezone
import uuid

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# --- 1. OPTION LEG & INTENT SCHEMA ---

StructureType = Literal[
    "iron_condor",
    "iron_butterfly",
    "credit_spread_put",
    "credit_spread_call",
    "straddle",
    "strangle",
    "debit_spread"
]

LegAction = Literal["buy", "sell"]
OptionType = Literal["call", "put"]

class OptionLeg(BaseModel):
    action: LegAction
    type: OptionType
    strike: float
    expiry: str  # YYYY-MM-DD

class Intent(BaseModel):
    intent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=utc_now_iso)
    underlying: str
    structure: StructureType
    legs: List[OptionLeg]
    size: int = Field(default=1, ge=0)  # 0 is a legitimate "stand down" signal (VIX/earnings/etc.)
    rationale: str
    conviction: float = Field(..., ge=0.0, le=1.0)
    regime_tags: List[str] = Field(default_factory=list)
    snapshot_ref: str = ""

    @model_validator(mode="after")
    def validate_defined_risk_structure(self) -> 'Intent':
        """
        Enforce basic structure rules before kernel evaluation.
        Naked single-leg structures are strictly rejected at schema level.
        """
        if not self.legs or len(self.legs) < 2:
            if self.structure not in ["straddle", "strangle"] or len(self.legs) < 2:
                raise ValueError(f"Structure {self.structure} requires at least 2 legs to be defined.")
        
        # Verify iron condor / butterfly has 4 legs
        if self.structure in ["iron_condor", "iron_butterfly"] and len(self.legs) != 4:
            raise ValueError(f"{self.structure} requires exactly 4 legs.")
            
        return self


# --- 2. MARKET DATA & PERCEPTION SCHEMAS ---

class OptionContractQuote(BaseModel):
    symbol: str
    strike: float
    expiry: str
    type: OptionType
    bid: float
    ask: float
    mid: float
    spread_pct: float
    open_interest: int
    volume: int
    delta: float
    gamma: float
    theta: float
    vega: float
    implied_volatility: float
    # Provenance of this quote: "live_indicative" (real Alpaca options quote feed),
    # "modeled_from_last_price" (Black-Scholes IV solved from a real last-traded/close price),
    # "modeled_no_trade_history" (Black-Scholes priced from realized-vol proxy; contract never traded),
    # or "simulated" (no broker credentials configured at all).
    quote_source: str = "live_indicative"

class Position(BaseModel):
    symbol: str
    qty: int
    current_price: float
    market_value: float
    cost_basis: float
    unrealized_pl: float
    asset_class: str = "option"  # "option" or "us_equity"

class AccountState(BaseModel):
    buying_power: float
    cash: float
    portfolio_value: float
    positions: List[Position] = Field(default_factory=list)
    portfolio_delta: float = 0.0
    portfolio_vega: float = 0.0
    data_source: str = "live"  # "live" or "simulated"

class MarketContext(BaseModel):
    underlying: str
    underlying_price: float
    iv_rank: float  # 0 to 100 percentile
    realized_vol: float  # Annualized %
    vrp: float  # IV - Realized Vol in percentage points
    vix: Optional[float] = None  # None means live VIX fetch failed - kernel fails closed on this
    earnings_days: Optional[int] = None
    is_earnings_blackout: bool = False
    earnings_data_source: str = "static_reference"  # "static_reference", "provider", or "unknown_no_data"
    contracts: List[OptionContractQuote] = Field(default_factory=list)
    account_state: AccountState
    timestamp: str = Field(default_factory=utc_now_iso)
    data_source: str = "live"  # "live" or "simulated" (no broker credentials configured)

    # --- Trend / volatility-term-structure signals, all derived from the SAME real daily closes
    # already fetched for realized vol. None means the bar history was too short to compute it -
    # the regime engine degrades its confidence rather than substituting a guess.
    trend_20d_pct: Optional[float] = None        # % price change over the trailing 20 sessions
    price_vs_ema20_pct: Optional[float] = None   # % distance of spot from its 20-session EMA
    rv_short: Optional[float] = None             # 10-session realized vol, annualized %
    rv_long: Optional[float] = None              # 60-session realized vol, annualized %
    rv_expansion_ratio: Optional[float] = None   # rv_short / rv_long (>1 = vol expanding)


# --- 3. MARKET REGIME & STRATEGY ARENA SCHEMAS ---

RegimeName = Literal[
    "HIGH_VOL_RANGE",     # rich premium, no strong direction -> sell defined-risk premium
    "HIGH_VOL_TREND",     # rich premium but directional -> one-sided credit spreads
    "LOW_VOL_TREND",      # cheap premium, directional -> debit spreads
    "LOW_VOL_RANGE",      # cheap premium, no direction -> little edge, size down
    "VOL_EXPANSION",      # realized vol accelerating past implied -> long vol / stand aside
    "EVENT_RISK",         # earnings window, VIX ceiling breach, or missing VIX -> stand down
]


class MarketRegime(BaseModel):
    """The classified trading environment, with the real numbers that produced the label."""
    regime: RegimeName
    label: str                                    # human-readable name for the dashboard
    description: str
    tags: List[str] = Field(default_factory=list)
    signals: Dict[str, Any] = Field(default_factory=dict)   # the actual inputs used, for audit
    confidence_pct: float = 0.0                   # how cleanly the signals separated (0-100)
    data_complete: bool = True                    # False when some signals were unavailable
    classified_at: str = Field(default_factory=utc_now_iso)


class StrategyVariant(BaseModel):
    """
    One competitor in the Strategy Arena: a structure plus a concrete parameter set.
    Variants differ only by parameters, so the arena can test e.g. a 0.75-sigma condor against
    a 1.25-sigma condor on identical real history and let the evidence pick.
    """
    variant_id: str
    name: str
    structure: StructureType
    params: Dict[str, float] = Field(default_factory=dict)
    eligible_regimes: List[RegimeName] = Field(default_factory=list)
    thesis: str = ""                              # why this variant should work where it's eligible
    status: Literal["CANDIDATE", "ACTIVE", "RETIRED"] = "CANDIDATE"


class StrategyScore(BaseModel):
    """A variant's measured performance on real historical bars, plus its composite arena score."""
    variant_id: str
    name: str
    structure: StructureType
    eligible: bool                                # eligible in the CURRENT regime
    trades: int = 0
    win_rate_pct: float = 0.0
    expectancy_pct: float = 0.0                   # avg % return on risk per trade
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    deflated_sharpe: float = 0.0
    score: float = 0.0                            # composite, 0-100
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    equity_curve: List[float] = Field(default_factory=list)
    note: str = ""


class ArenaResult(BaseModel):
    """Full arena tournament output for one symbol in one regime."""
    symbol: str
    regime: MarketRegime
    scored_at: str = Field(default_factory=utc_now_iso)
    lookback_days: int = 0
    scores: List[StrategyScore] = Field(default_factory=list)
    champion_id: Optional[str] = None
    champion_name: Optional[str] = None
    champion_rationale: str = ""
    data_source: str = "real_historical_bars"     # or "unavailable"
    methodology: str = ""
    known_bias: str = ""                          # disclosed limitations of the scoring method


# --- 4. RISK KERNEL SCHEMAS ---

class KernelCheck(BaseModel):
    check: str
    value: float
    limit: float
    pass_status: bool = Field(..., serialization_alias="pass")
    description: Optional[str] = None

class KernelDecision(BaseModel):
    intent_id: str
    approved: bool
    decision: Literal["APPROVED", "REJECTED"]
    kernel_checks: List[KernelCheck]
    reasons: List[str] = Field(default_factory=list)
    max_loss: float = 0.0
    projected_delta: float = 0.0
    projected_vega: float = 0.0
    evaluated_at: str = Field(default_factory=utc_now_iso)


# --- 5. VERIFICATION & RECONCILIATION SCHEMAS ---

class AuditSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    hash: str
    captured_at: str
    market_state: Dict[str, Any]
    account_state: Dict[str, Any]
    intent_id: str
    intent_data: Optional[Dict[str, Any]] = None
    kernel_decision: Literal["APPROVED", "REJECTED"]
    kernel_checks: List[Dict[str, Any]]
    order_id: Optional[str] = None
    verified: bool = True

class ReconciliationEvent(BaseModel):
    checked_at: str = Field(default_factory=utc_now_iso)
    believed_positions: Dict[str, int]
    actual_positions: Dict[str, int]
    match: bool
    action_taken: str
    fault_injected: bool = False

class ReplayVerificationResult(BaseModel):
    order_id: str
    snapshot_id: str
    integrity: bool
    condition_supported: bool
    recomputed_hash: str
    stored_hash: str
    rationale_claims: List[Dict[str, Any]]
    overall_status: str  # "PASS" or "FAIL"
