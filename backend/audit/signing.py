from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
import base64
import json
import os
from pathlib import Path

class VerdictSigner:
    def __init__(self, key_path: str = None):
        """Load or generate Ed25519 keypair."""
        if key_path and os.path.exists(key_path):
            with open(key_path, "rb") as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None
                )
        else:
            self.private_key = Ed25519PrivateKey.generate()
            if key_path:
                Path(key_path).parent.mkdir(parents=True, exist_ok=True)
                with open(key_path, "wb") as f:
                    f.write(self.private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption()
                    ))
        
        self.public_key = self.private_key.public_key()
    
    def sign(self, verdict_data: dict) -> str:
        """Sign verdict, return base64-encoded signature."""
        payload = json.dumps(verdict_data, sort_keys=True, default=str).encode('utf-8')
        signature = self.private_key.sign(payload)
        return base64.b64encode(signature).decode('utf-8')
    
    def verify(self, verdict_data: dict, signature: str) -> bool:
        """Verify a signed verdict."""
        try:
            sig_bytes = base64.b64decode(signature)
            payload = json.dumps(verdict_data, sort_keys=True, default=str).encode('utf-8')
            self.public_key.verify(sig_bytes, payload)
            return True
        except Exception:
            return False
