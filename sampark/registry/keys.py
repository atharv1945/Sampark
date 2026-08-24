"""Ed25519 keypair generation — agent/test-side only.

The registry (sampark/registry/store.py) never receives, stores, or
logs a private key — only AgentKeypair.public_key_b64, a plain base64
string, ever crosses into a repository call. This module exists so
agents (and this phase's tests, standing in for an agent process) can
generate a keypair and sign a request locally; it is never imported by
sampark/registry/scope.py.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from nacl.signing import SigningKey


@dataclass(frozen=True)
class AgentKeypair:
    """Held by the agent process. The registry sees only public_key_b64."""

    signing_key: SigningKey

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(bytes(self.signing_key.verify_key)).decode("ascii")

    def sign(self, message: bytes) -> str:
        """Detached signature over `message`, standard-base64 encoded."""
        signature = self.signing_key.sign(message).signature
        return base64.b64encode(signature).decode("ascii")


def generate_keypair() -> AgentKeypair:
    return AgentKeypair(signing_key=SigningKey.generate())
