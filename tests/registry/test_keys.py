"""sampark/registry/keys.py — Ed25519 keypair generation.

Only structural properties (byte lengths, round-trip behavior) are
asserted — key *material* is deliberately random per run (deterministic
key generation would be a security bug), so these tests are deterministic
in outcome, never in the key bytes themselves.
"""

from __future__ import annotations

import base64

from sampark.registry import generate_keypair
from sampark.registry.signing import verify_signature


def test_generated_public_key_is_32_raw_bytes_base64_encoded():
    kp = generate_keypair()
    decoded = base64.b64decode(kp.public_key_b64, validate=True)
    assert len(decoded) == 32


def test_two_generated_keypairs_have_different_public_keys():
    a = generate_keypair()
    b = generate_keypair()
    assert a.public_key_b64 != b.public_key_b64


def test_sign_produces_a_64_byte_base64_signature():
    kp = generate_keypair()
    signature = kp.sign(b"hello")
    decoded = base64.b64decode(signature, validate=True)
    assert len(decoded) == 64


def test_public_private_round_trip_verifies():
    kp = generate_keypair()
    message = b"round trip check"
    signature = kp.sign(message)
    assert verify_signature(kp.public_key_b64, message, signature) is True
