"""
kernel/kill_switch.py - Emergency Kill Switch and Trading Halt Manager.
Can be triggered by:
1. Automated Reconciliation Loop on state mismatch (Act 2 TradeTrap defense).
2. VIX spike >= vix_kill_switch_level.
3. Earnings blackout window.
4. Manual administrator override.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone

class KillSwitch:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(KillSwitch, cls).__new__(cls)
            cls._instance.is_halted = False
            cls._instance.halt_reason = ""
            cls._instance.halted_at = None
            cls._instance.trigger_source = None
        return cls._instance

    def trigger_halt(self, reason: str, source: str = "MANUAL"):
        """Engage trading halt immediately."""
        self.is_halted = True
        self.halt_reason = reason
        self.halted_at = datetime.now(timezone.utc).isoformat()
        self.trigger_source = source
        print(f"[KILL SWITCH ACTIVATED] Reason: {reason} | Source: {source}")

    def reset(self):
        """Reset the kill switch."""
        self.is_halted = False
        self.halt_reason = ""
        self.halted_at = None
        self.trigger_source = None
        print("[KILL SWITCH RESET] Normal trading operations resumed.")

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "halted_at": self.halted_at,
            "trigger_source": self.trigger_source
        }

kill_switch = KillSwitch()
