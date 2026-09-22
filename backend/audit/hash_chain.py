import hashlib
import json
from typing import Optional

class HashChain:
    """Tamper-evident hash chain for workflow events."""
    
    def __init__(self):
        self._previous_hash: str = "GENESIS"  # sentinel for first event
    
    def append(self, event_data: dict) -> str:
        """Compute hash for this event including previous hash, return the hash."""
        payload = json.dumps(event_data, sort_keys=True, default=str)
        chain_input = f"{self._previous_hash}|{payload}"
        event_hash = hashlib.sha256(chain_input.encode('utf-8')).hexdigest()
        self._previous_hash = event_hash
        return event_hash
    
    @staticmethod
    def verify_chain(events: list[dict]) -> tuple[bool, Optional[str]]:
        """Verify integrity of a chain of events. Returns (valid, error_message)."""
        expected_prev = "GENESIS"
        for i, event in enumerate(events):
            metadata = event.get('metadata') or {}
            stored_hash = metadata.get('event_hash')
            stored_prev = metadata.get('previous_hash')
            
            if not stored_hash or not stored_prev:
                return False, f"Event {i} is missing hash metadata."
                
            if stored_prev != expected_prev:
                return False, f"Chain broken at event {i}: expected previous_hash {expected_prev}, got {stored_prev}."
                
            # Reconstruct the event_data that was hashed
            import copy
            event_copy = copy.deepcopy(event)
            if 'metadata' in event_copy and event_copy['metadata']:
                event_copy['metadata'].pop('event_hash', None)
                event_copy['metadata'].pop('previous_hash', None)
                if not event_copy['metadata']:
                    event_copy.pop('metadata', None)
            
            payload = json.dumps(event_copy, sort_keys=True, default=str)
            chain_input = f"{expected_prev}|{payload}"
            computed_hash = hashlib.sha256(chain_input.encode('utf-8')).hexdigest()
            
            if computed_hash != stored_hash:
                return False, f"Event {i} hash mismatch: computed {computed_hash}, stored {stored_hash}."
                
            expected_prev = computed_hash
            
        return True, None
