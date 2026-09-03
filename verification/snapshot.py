"""
verification/snapshot.py - Canonical JSON Serialization & SHA-256 Snapshot Hasher.
Captures market state, account state, and intent ID at the exact millisecond of decision.
"""

import json
import hashlib
from typing import Dict, Any, Tuple
from shared.schemas import MarketContext, AccountState, Intent, AuditSnapshot, KernelDecision

def canonical_serialize(data: Dict[str, Any]) -> str:
    """Canonical JSON serialization with sorted keys and no extraneous whitespace."""
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True)

def compute_decision_hash(market_state: Dict[str, Any], account_state: Dict[str, Any], intent_id: str) -> str:
    """
    Computes SHA-256 hash over canonical JSON of market_state + account_state + intent_id.
    """
    payload = {
        "account_state": account_state,
        "intent_id": intent_id,
        "market_state": market_state
    }
    canonical_str = canonical_serialize(payload)
    sha_hash = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()
    return f"sha256:{sha_hash}"

def create_audit_snapshot(
    ctx: MarketContext,
    account: AccountState,
    intent: Intent,
    decision: KernelDecision,
    order_id: str = None
) -> AuditSnapshot:
    """
    Creates an immutable, cryptographically hashed audit snapshot.
    """
    market_state = {
        f"{ctx.underlying}_price": ctx.underlying_price,
        f"{ctx.underlying}_iv_rank": ctx.iv_rank,
        f"{ctx.underlying}_vrp": ctx.vrp,
        "vix": ctx.vix
    }
    
    account_state = {
        "buying_power": account.buying_power,
        "portfolio_delta": account.portfolio_delta,
        "portfolio_vega": account.portfolio_vega,
        "positions": [p.model_dump() for p in account.positions]
    }
    
    sha_hash = compute_decision_hash(market_state, account_state, intent.intent_id)
    intent.snapshot_ref = sha_hash
    
    checks_dict = [c.model_dump(by_alias=True) for c in decision.kernel_checks]
    
    return AuditSnapshot(
        hash=sha_hash,
        captured_at=intent.timestamp,
        market_state=market_state,
        account_state=account_state,
        intent_id=intent.intent_id,
        intent_data=intent.model_dump(),
        kernel_decision=decision.decision,
        kernel_checks=checks_dict,
        order_id=order_id,
        verified=True
    )
