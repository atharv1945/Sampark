"""Ed25519 signature verification — pure, no I/O.

verify_signature() never raises: malformed base64, a wrong-length public
key, a wrong-length signature, a cryptographically invalid signature, and
a non-string/None argument all return False, so a caller (e.g.
sampark/registry/scope.py) can treat "bad signature" as one denial reason
among several rather than an exception path it has to wrap.

TypeError is caught alongside the decode/verify errors it already
handles because base64.b64decode(None, ...), VerifyKey(None), and
.verify(None, ...) each raise TypeError rather than ValueError/
binascii.Error — every current call site passes non-nullable Pydantic
str fields, so this is not reachable today, but verify_signature is a
public export sitting on the authorization boundary and must hold its
"never raises" contract for any caller, not only today's.

public_key and signature are both standard base64 text (LOCKED
DECISIONS) over the raw 32-byte Ed25519 public key and 64-byte detached
signature respectively.
"""

from __future__ import annotations

import base64
import binascii

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


def verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        public_key_bytes = base64.b64decode(public_key_b64, validate=True)
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return False

    try:
        VerifyKey(public_key_bytes).verify(message, signature_bytes)
    except (ValueError, BadSignatureError, TypeError):
        return False
    return True
