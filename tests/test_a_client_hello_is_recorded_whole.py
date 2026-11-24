"""A ClientHello is recorded whole and read into the fields a fingerprint is made of.

The capture server keeps the raw bytes of the first TLS record of every connection to its TLS port
(scripts/retail/capture_server.py) and scripts/retail/clienthello.py reads them. Held here:

  * a captured ClientHello (bytes made by a real TLS client, OpenSSL through Python's ssl module, asking for
    the name `localhost` and the protocols h2 and http/1.1) parses into its record, handshake and hello
    fields, its extensions and its JA3 string;
  * what is not a ClientHello, or is cut short, is described, never raised;
  * the server records the bytes of a real TLS client that connects to it, answers a fatal alert (the
    handshake fails on purpose), and records a connection that sends nothing as an empty one.
"""
from __future__ import annotations

import hashlib
import socket
import ssl

import pytest

from _retail_scripts import load

pytestmark = pytest.mark.integration

ch = load("clienthello")
server = load("capture_server")

#: 517 bytes captured from ssl.SSLContext(PROTOCOL_TLS_CLIENT).wrap_bio(..., server_hostname="localhost") with
#: set_alpn_protocols(["h2", "http/1.1"]); the 0x15 extension at the end is OpenSSL's padding to 512 bytes.
CLIENT_HELLO = bytes.fromhex(
    "1603010200010001fc0303ffc1fca73e275fb067c315a1d4b2b25d2ea5526315817e6c443bc5"
    "3ff5f196c9205a904376448f2a5a52731569257bd624ad61484ca713dbae9e29a11234f017f8"
    "0024130213031301c02cc030c02bc02fcca9cca8c024c028c023c027009f009e006b006700ff"
    "0100018f0000000e000c0000096c6f63616c686f7374000b000403000102000a00160014001d"
    "0017001e0019001801000101010201030104002300000010000e000c02683208687474702f31"
    "2e310016000000170000000d002a0028040305030603080708080809080a080b080408050806"
    "040105010601030303010302040205020602002b00050403040303002d000201010033002600"
    "24001d0020d016a6407769d54fbad03945dc2c5e64b719d2fdbefdb0731f4cb7845c9abf2200"
    "1500d20000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000"
)


def test_the_fixture_is_the_bytes_it_says():
    assert len(CLIENT_HELLO) == 517
    assert CLIENT_HELLO[:3] == bytes([0x16, 0x03, 0x01])  # a handshake record, legacy version TLS 1.0


def test_a_client_hello_parses_into_record_and_handshake_fields():
    got = ch.parse(CLIENT_HELLO)
    assert got["error"] is None and got["bytes"] == 517
    assert got["record"] == {"type": 22, "type_name": "handshake", "version": 0x0301, "length": 512, "complete": True}
    assert got["handshake"] == {"type": 1, "type_name": "client_hello", "length": 508}
    hello = got["client_hello"]
    assert hello["client_version"] == 0x0303
    assert hello["random"] == CLIENT_HELLO[11:43].hex()  # record 5 + handshake header 4 + version 2
    assert hello["session_id"] == CLIENT_HELLO[44:76].hex() and CLIENT_HELLO[43] == 32
    assert hello["cipher_suites"][:3] == [0x1302, 0x1303, 0x1301]  # the TLS 1.3 suites, in the order sent
    assert len(hello["cipher_suites"]) == 18 and hello["compression_methods"] == [0]


def test_the_extensions_are_read_in_the_order_they_were_sent():
    hello = ch.parse(CLIENT_HELLO)["client_hello"]
    names = [e["name"] for e in hello["extensions"]]
    assert names[:5] == ["server_name", "ec_point_formats", "supported_groups", "session_ticket",
                         "application_layer_protocol_negotiation"]
    by = {e["name"]: e for e in hello["extensions"]}
    assert by["server_name"]["server_names"] == ["localhost"]
    assert by["application_layer_protocol_negotiation"]["protocols"] == ["h2", "http/1.1"]
    assert by["supported_versions"]["values"] == [0x0304, 0x0303]
    assert by["supported_groups"]["values"][:2] == [29, 23]  # x25519, secp256r1
    assert by["key_share"]["shares"] == [[29, 32]]
    assert by["psk_key_exchange_modes"]["values"] == [1]
    assert names[-1] == "padding"


def test_the_ja3_string_and_hash_follow_the_definition():
    got = ch.parse(CLIENT_HELLO)
    fields = got["ja3"].split(",")
    assert len(fields) == 5 and fields[0] == "771"
    assert fields[1].startswith("4866-4867-4865-49196")
    assert fields[2].split("-")[:3] == ["0", "11", "10"]  # extension types in the order sent
    assert fields[4] == "0-1-2"
    assert got["ja3_md5"] == hashlib.md5(got["ja3"].encode("ascii"), usedforsecurity=False).hexdigest()


def test_grease_values_are_left_out_of_ja3():
    assert ch.is_grease(0x0A0A) and ch.is_grease(0xFAFA) and not ch.is_grease(0x1301) and not ch.is_grease(0x0A1A)
    hello = {"client_version": 771, "cipher_suites": [0x0A0A, 0x1301], "extensions": [
        {"type": 0x1A1A}, {"type": 10, "values": [0x2A2A, 29]}, {"type": 11, "values": [0]}]}
    assert ch.ja3(hello) == "771,4865,10-11,29,0"


def test_what_is_not_a_client_hello_is_described_not_raised():
    alert = ch.parse(bytes([0x15, 0x03, 0x03, 0x00, 0x02, 0x02, 0x28]))
    assert alert["record"]["type_name"] == "alert" and "not a handshake" in alert["error"]
    assert "client_hello" not in alert
    cut = ch.parse(CLIENT_HELLO[:100])
    assert cut["record"]["complete"] is False and cut["error"] and "client_hello" not in cut
    assert ch.parse(b"")["error"] and ch.parse(b"")["error"]
    server_hello = bytearray(CLIENT_HELLO)
    server_hello[5] = 2  # handshake type: server_hello
    assert "not a client hello" in ch.parse(bytes(server_hello))["error"]


def _hello_from_a_real_client(port: int, name: str = "localhost") -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    context.set_alpn_protocols(["h2", "http/1.1"])
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        with pytest.raises((ssl.SSLError, ConnectionError)):  # the server answers a fatal alert and closes: the handshake fails
            context.wrap_socket(raw, server_hostname=name)


@pytest.fixture
def tls(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("x", encoding="utf-8")
    http, tls_server, recorder = server.start(page, tmp_path / "out")
    yield tls_server, recorder
    server.stop(http, tls_server)


def _wait(recorder, count: int) -> None:
    import time
    deadline = time.monotonic() + 10
    while len(recorder.tls) < count and time.monotonic() < deadline:
        time.sleep(0.02)


def test_the_server_records_the_hello_of_a_real_client_and_answers_an_alert(tls):
    tls_server, recorder = tls
    _hello_from_a_real_client(tls_server.server_address[1])
    _wait(recorder, 1)
    (entry,) = recorder.dump()["tls_connections"]
    raw = bytes.fromhex(entry["raw_hex"])
    assert entry["complete"] is True and entry["bytes"] == len(raw) == 5 + int.from_bytes(raw[3:5], "big")
    parsed = entry["parsed"]
    assert parsed["error"] is None and parsed["record"]["type_name"] == "handshake"
    by = {e["name"]: e for e in parsed["client_hello"]["extensions"]}
    assert by["server_name"]["server_names"] == ["localhost"]
    assert by["application_layer_protocol_negotiation"]["protocols"] == ["h2", "http/1.1"]


def test_a_connection_that_sends_nothing_is_recorded_empty(tls):
    tls_server, recorder = tls
    socket.create_connection(("127.0.0.1", tls_server.server_address[1]), timeout=5).close()
    _wait(recorder, 1)
    (entry,) = recorder.dump()["tls_connections"]
    assert entry["bytes"] == 0 and entry["raw_hex"] == "" and entry["complete"] is False and "parsed" not in entry


def test_the_record_of_connections_is_bounded(tls):
    tls_server, recorder = tls
    for i in range(server.MAX_TLS_CONNECTIONS + 4):
        recorder.add_tls(b"", False)
    assert len(recorder.dump()["tls_connections"]) == server.MAX_TLS_CONNECTIONS
