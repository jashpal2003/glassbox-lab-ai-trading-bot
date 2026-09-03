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


# --- 3. RISK KERNEL SCHEMAS ---

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


# --- 4. VERIFICATION & RECONCILIATION SCHEMAS ---

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
