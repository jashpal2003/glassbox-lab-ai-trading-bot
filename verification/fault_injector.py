"""
verification/fault_injector.py - Dev-Only Local State Fault Injector (Act 2: TradeTrap Defense).
Simulates local ledger / memory corruption for defensive demonstration.
NEVER touches Alpaca's real systems or API.
"""

from typing import Dict, Any

class FaultInjector:
    def __init__(self):
        self.is_active = False
        self.corrupted_positions: Dict[str, int] = {}
        self.injection_description = ""

    def inject_phantom_position(self, symbol: str = "AAPL", qty: int = 9):
        """
        Injects an out-of-sync position into the local belief state.
        Demonstrates the documented 'TradeTrap' failure mode.
        """
        self.is_active = True
        self.corrupted_positions[symbol] = qty
        self.injection_description = f"Phantom position injected: {symbol} x {qty}"
        print(f"[FAULT INJECTOR ACTIVATED (DEV DEMO ONLY)] {self.injection_description}")

    def clear(self):
        """Clear injected faults."""
        self.is_active = False
        self.corrupted_positions = {}
        self.injection_description = ""
        print("[FAULT INJECTOR CLEARED] Local state restored.")

    def get_status(self) -> Dict[str, Any]:
        return {
            "active": self.is_active,
            "corrupted_positions": self.corrupted_positions,
            "description": self.injection_description
        }

fault_injector = FaultInjector()
