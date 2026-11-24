"""Parse the first TLS record of a connection, which for a client is its ClientHello.

    python scripts/retail/clienthello.py HEX

The capture server keeps the raw bytes of that record (everything a fingerprint of
the handshake is made of); this module reads them into record fields, handshake
fields and the lists a fingerprint is built from. It reads bytes, it holds no value of
a browser, and it never raises on a short or odd input: `parse` returns what it
could read and says why it stopped (`error`).
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from typing import Any, Dict, List

RECORD_TYPES = {20: "change_cipher_spec", 21: "alert", 22: "handshake", 23: "application_data"}
HANDSHAKE_TYPES = {1: "client_hello", 2: "server_hello"}
EXTENSIONS = {
    0: "server_name", 5: "status_request", 10: "supported_groups", 11: "ec_point_formats", 13: "signature_algorithms",
    16: "application_layer_protocol_negotiation", 18: "signed_certificate_timestamp", 21: "padding",
    22: "encrypt_then_mac", 23: "extended_master_secret", 27: "compress_certificate", 28: "record_size_limit",
    34: "delegated_credentials", 35: "session_ticket", 41: "pre_shared_key", 43: "supported_versions",
    45: "psk_key_exchange_modes", 51: "key_share", 65037: "encrypted_client_hello", 65281: "renegotiation_info",
}


def is_grease(value: int) -> bool:
    """RFC 8701 reserved values: 0x0a0a, 0x1a1a, ... 0xfafa."""
    return (value & 0x0F0F) == 0x0A0A and (value >> 8) == (value & 0xFF)


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def left(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or n > self.left():
            raise ValueError(f"need {n} bytes at offset {self.pos}, {self.left()} left")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def uint(self, n: int) -> int:
        return int.from_bytes(self.take(n), "big")


def _u16_list(raw: bytes) -> List[int]:
    return [struct.unpack_from(">H", raw, i)[0] for i in range(0, len(raw) - 1, 2)]


def _extension(ext_type: int, body: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": ext_type, "name": EXTENSIONS.get(ext_type, "unknown"), "length": len(body)}
    try:
        r = _Reader(body)
        if ext_type == 0 and body:  # server_name: list of (type, name)
            r.take(2)
            names = []
            while r.left():
                kind, size = r.uint(1), r.uint(2)
                names.append(r.take(size).decode("ascii", "replace") if kind == 0 else f"type{kind}")
            out["server_names"] = names
        elif ext_type in (10, 13) and body:  # supported_groups, signature_algorithms
            out["values"] = _u16_list(r.take(r.uint(2)))
        elif ext_type == 11 and body:
            out["values"] = list(r.take(r.uint(1)))
        elif ext_type == 16 and body:  # ALPN
            inner, protocols = _Reader(r.take(r.uint(2))), []
            while inner.left():
                protocols.append(inner.take(inner.uint(1)).decode("ascii", "replace"))
            out["protocols"] = protocols
        elif ext_type == 43 and body:  # supported_versions (client form)
            out["values"] = _u16_list(r.take(r.uint(1)))
        elif ext_type == 45 and body:
            out["values"] = list(r.take(r.uint(1)))
        elif ext_type == 51 and body:  # key_share (client form): group and size of each share
            inner, shares = _Reader(r.take(r.uint(2))), []
            while inner.left():
                group = inner.uint(2)
                shares.append([group, len(inner.take(inner.uint(2)))])
            out["shares"] = shares
        elif ext_type == 28 and len(body) == 2:
            out["value"] = struct.unpack(">H", body)[0]
        elif ext_type == 27 and body:
            out["values"] = _u16_list(r.take(r.uint(1)))
    except ValueError as exc:
        out["error"] = str(exc)
    return out


def ja3(hello: Dict[str, Any]) -> str:
    """The JA3 string of a parsed hello: version, ciphers, extensions, groups, point formats; GREASE left out."""
    def keep(values):
        return "-".join(str(v) for v in values if not is_grease(v))
    ext_types = [e["type"] for e in hello["extensions"]]
    groups = next((e.get("values", []) for e in hello["extensions"] if e["type"] == 10), [])
    points = next((e.get("values", []) for e in hello["extensions"] if e["type"] == 11), [])
    return ",".join([str(hello["client_version"]), keep(hello["cipher_suites"]), keep(ext_types), keep(groups),
                     "-".join(str(p) for p in points)])


def parse(raw: bytes) -> Dict[str, Any]:
    """The record, the handshake and the lists of one TLS record read from `raw`."""
    out: Dict[str, Any] = {"bytes": len(raw), "error": None}
    try:
        r = _Reader(raw)
        rec_type, rec_version, rec_length = r.uint(1), r.uint(2), r.uint(2)
        out["record"] = {"type": rec_type, "type_name": RECORD_TYPES.get(rec_type, "unknown"),
                         "version": rec_version, "length": rec_length, "complete": r.left() >= rec_length}
        body = _Reader(r.take(min(rec_length, r.left())))
        if rec_type != 22:
            out["error"] = f"the record is {RECORD_TYPES.get(rec_type, rec_type)}, not a handshake"
            return out
        hs_type, hs_length = body.uint(1), body.uint(3)
        out["handshake"] = {"type": hs_type, "type_name": HANDSHAKE_TYPES.get(hs_type, "unknown"), "length": hs_length}
        if hs_type != 1:
            out["error"] = "the handshake message is not a client hello"
            return out
        hello: Dict[str, Any] = {"client_version": body.uint(2), "random": body.take(32).hex()}
        hello["session_id"] = body.take(body.uint(1)).hex()
        hello["cipher_suites"] = _u16_list(body.take(body.uint(2)))
        hello["compression_methods"] = list(body.take(body.uint(1)))
        hello["extensions"] = []
        if body.left():
            ext_block = _Reader(body.take(body.uint(2)))
            while ext_block.left():
                ext_type = ext_block.uint(2)
                hello["extensions"].append(_extension(ext_type, ext_block.take(ext_block.uint(2))))
        out["client_hello"] = hello
        out["ja3"] = ja3(hello)
        out["ja3_md5"] = hashlib.md5(out["ja3"].encode("ascii"), usedforsecurity=False).hexdigest()  # JA3 is defined on md5
    except ValueError as exc:
        out["error"] = str(exc)
    return out


if __name__ == "__main__":
    print(json.dumps(parse(bytes.fromhex(sys.argv[1])), indent=2))
