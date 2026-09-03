"""
verification/audit_log.py - Append-only store for Audit Snapshots and Decisions.
"""

import os
import json
from typing import List, Optional, Dict, Any
from shared.schemas import AuditSnapshot

AUDIT_FILE = os.path.join(os.path.dirname(__file__), "audit_trail.jsonl")

class AuditStore:
    def __init__(self, file_path: str = AUDIT_FILE):
        self.file_path = file_path
        self._memory_cache: List[AuditSnapshot] = []
        self._load_from_disk()

    def _load_from_disk(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            self._memory_cache.append(AuditSnapshot(**data))
            except Exception as e:
                print(f"Audit log load warning: {e}")

    def append(self, snapshot: AuditSnapshot):
        """Append an audit record to in-memory store and JSONL disk file."""
        self._memory_cache.append(snapshot)
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot.model_dump()) + "\n")
        except Exception as e:
            print(f"Error persisting audit record: {e}")

    def get_all(self) -> List[AuditSnapshot]:
        return list(self._memory_cache)

    def get_by_intent_id(self, intent_id: str) -> Optional[AuditSnapshot]:
        for s in reversed(self._memory_cache):
            if s.intent_id == intent_id:
                return s
        return None

    def get_by_order_id(self, order_id: str) -> Optional[AuditSnapshot]:
        for s in reversed(self._memory_cache):
            if s.order_id == order_id:
                return s
        return None

audit_store = AuditStore()
