"""
kernel/executor.py - Authorized Alpaca Order Execution Module.
The ONLY module permitted to place multi-leg orders with Alpaca.
Strictly requires an APPROVED KernelDecision.
"""

from typing import Optional, Dict, Any
from shared.schemas import Intent, KernelDecision
from perception.alpaca_client import AlpacaClient

class AlpacaExecutor:
    def __init__(self, client: Optional[AlpacaClient] = None):
        self.client = client or AlpacaClient()

    def execute_intent(self, intent: Intent, decision: KernelDecision) -> Optional[str]:
        """
        Executes approved trade Intent on Alpaca.
        Throws error or returns None if decision was not APPROVED.
        """
        if not decision.approved or decision.decision != "APPROVED":
            print(f"[SECURITY ALERT] Unauthorized execution attempt for unapproved Intent {intent.intent_id}")
            return None

        legs_data = [leg.model_dump() for leg in intent.legs]
        order_id = self.client.place_multi_leg_order(
            legs=legs_data,
            size=intent.size,
            underlying=intent.underlying
        )
        if order_id:
            print(f"[EXECUTION SUCCESS] Placed Alpaca order {order_id} for Intent {intent.intent_id}")
        else:
            print(f"[EXECUTION FAILED] Order could not be placed for Intent {intent.intent_id}.")
        return order_id
