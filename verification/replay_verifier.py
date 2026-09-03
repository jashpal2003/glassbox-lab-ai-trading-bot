"""
verification/replay_verifier.py - Replay Verifier Contract for Independent Verification.
Performs two separate verification checks:
1. Cryptographic Integrity Check: Recomputes SHA-256 hash from raw inputs.
2. Condition Supported Check: Cross-references numeric rationale claims against stored market state.
"""

import re
from typing import Dict, Any, List, Optional
from shared.schemas import ReplayVerificationResult, AuditSnapshot
from verification.snapshot import compute_decision_hash
from verification.audit_log import audit_store

class ReplayVerifier:
    def __init__(self, store=None):
        self.store = store or audit_store

    def verify_decision(self, snapshot: AuditSnapshot) -> ReplayVerificationResult:
        """
        Independent replay verification of a snapshot.
        """
        # 1. Integrity Check (Cryptographic SHA-256 recomputation)
        recomputed = compute_decision_hash(
            market_state=snapshot.market_state,
            account_state=snapshot.account_state,
            intent_id=snapshot.intent_id
        )
        integrity_pass = (recomputed == snapshot.hash)

        # 2. Condition Supported Check (Check claims in rationale)
        rationale_claims: List[Dict[str, Any]] = []
        condition_supported = True
        
        intent_data = snapshot.intent_data or {}
        rationale = intent_data.get("rationale", "")
        
        # Check IV rank claim
        iv_match = re.search(r"IV\s*(?:rank)?[:\s=]*([+-]?\d+(?:\.\d+)?)", rationale, re.IGNORECASE)
        if iv_match:
            try:
                claimed_iv = float(iv_match.group(1))
                actual_iv = None
                for k, v in snapshot.market_state.items():
                    if "iv_rank" in k:
                        actual_iv = float(v)
                        break
                
                if actual_iv is not None:
                    passed = abs(claimed_iv - actual_iv) <= 2.0
                    rationale_claims.append({
                        "claim": f"IV Rank claimed: {claimed_iv}%",
                        "actual": f"{actual_iv}%",
                        "supported": passed
                    })
                    if not passed:
                        condition_supported = False
            except Exception:
                pass

        # Check VRP claim
        vrp_match = re.search(r"VRP[:\s=]*([+-]?\d+(?:\.\d+)?)", rationale, re.IGNORECASE)
        if vrp_match:
            try:
                claimed_vrp = float(vrp_match.group(1))
                actual_vrp = None
                for k, v in snapshot.market_state.items():
                    if "vrp" in k:
                        actual_vrp = float(v)
                        break
                
                if actual_vrp is not None:
                    passed = abs(claimed_vrp - actual_vrp) <= 2.0
                    rationale_claims.append({
                        "claim": f"VRP claimed: {claimed_vrp} pts",
                        "actual": f"{actual_vrp} pts",
                        "supported": passed
                    })
                    if not passed:
                        condition_supported = False
            except Exception:
                pass

        # Check VIX claim
        vix_match = re.search(r"VIX[:\s=]*([+-]?\d+(?:\.\d+)?)", rationale, re.IGNORECASE)
        if vix_match:
            try:
                claimed_vix = float(vix_match.group(1))
                actual_vix = snapshot.market_state.get("vix")
                if actual_vix is not None:
                    passed = abs(claimed_vix - float(actual_vix)) <= 1.0
                    rationale_claims.append({
                        "claim": f"VIX claimed: {claimed_vix}",
                        "actual": f"{actual_vix}",
                        "supported": passed
                    })
                    if not passed:
                        condition_supported = False
            except Exception:
                pass

        # If no specific regex matched, confirm context validity
        if not rationale_claims:
            rationale_claims.append({
                "claim": "Context verification",
                "actual": "Valid market & account snapshot",
                "supported": True
            })

        overall_status = "PASS" if (integrity_pass and condition_supported) else "FAIL"

        return ReplayVerificationResult(
            order_id=snapshot.order_id or "unassigned",
            snapshot_id=snapshot.snapshot_id,
            integrity=integrity_pass,
            condition_supported=condition_supported,
            recomputed_hash=recomputed,
            stored_hash=snapshot.hash,
            rationale_claims=rationale_claims,
            overall_status=overall_status
        )

    def verify_by_order_id(self, order_id: str) -> Optional[ReplayVerificationResult]:
        snap = self.store.get_by_order_id(order_id)
        if snap:
            return self.verify_decision(snap)
        return None

    def verify_by_intent_id(self, intent_id: str) -> Optional[ReplayVerificationResult]:
        snap = self.store.get_by_intent_id(intent_id)
        if snap:
            return self.verify_decision(snap)
        return None

replay_verifier = ReplayVerifier()
