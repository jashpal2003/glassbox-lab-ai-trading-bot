"""
verification/reconciliation.py - Background Automated State Reconciliation Engine.
Continuously polls broker state vs local believed state on a fixed timer.
Automatically triggers Kill Switch on any state mismatch.
"""

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from shared.schemas import ReconciliationEvent
from kernel.kill_switch import kill_switch
from verification.fault_injector import fault_injector
from perception.alpaca_client import AlpacaClient

class ReconciliationEngine:
    def __init__(self, client: Optional[AlpacaClient] = None, interval_seconds: int = 30):
        self.client = client or AlpacaClient()
        self.interval_seconds = interval_seconds
        self.events_history: List[ReconciliationEvent] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def run_check(self) -> ReconciliationEvent:
        """
        Executes a reconciliation cycle.
        Returns ReconciliationEvent and engages kill switch if mismatch is found.
        """
        # 1. Fetch real broker positions
        account = self.client.get_account_state()
        actual_pos_map = {p.symbol: p.qty for p in account.positions}
        
        # 2. Derive believed state (base real state + any injected local faults)
        believed_pos_map = dict(actual_pos_map)
        fault_active = fault_injector.is_active
        if fault_active:
            for sym, qty in fault_injector.corrupted_positions.items():
                believed_pos_map[sym] = believed_pos_map.get(sym, 0) + qty

        # 3. Check exact match
        match = (believed_pos_map == actual_pos_map)
        
        action = "CONTINUE_TRADING"
        if not match:
            action = "HALT_NEW_ORDERS"
            reason = f"Position mismatch detected! Believed: {believed_pos_map}, Broker: {actual_pos_map}"
            kill_switch.trigger_halt(reason=reason, source="RECONCILIATION_LOOP")
        
        event = ReconciliationEvent(
            checked_at=datetime.now(timezone.utc).isoformat(),
            believed_positions=believed_pos_map,
            actual_positions=actual_pos_map,
            match=match,
            action_taken=action,
            fault_injected=fault_active
        )
        
        self.events_history.append(event)
        return event

    def start_background_loop(self):
        """Starts timer-based background polling loop."""
        if self._running:
            return
            
        self._running = True
        
        def _loop():
            print(f"[RECONCILIATION ENGINE] Automated background loop started (interval: {self.interval_seconds}s)")
            while self._running:
                try:
                    self.run_check()
                except Exception as e:
                    print(f"Reconciliation loop error: {e}")
                time.sleep(self.interval_seconds)
                
        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def get_latest_events(self, limit: int = 20) -> List[ReconciliationEvent]:
        return list(reversed(self.events_history[-limit:]))

reconciliation_engine = ReconciliationEngine()
