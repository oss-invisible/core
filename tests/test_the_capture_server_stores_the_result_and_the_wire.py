"""The capture server stores the result the page POSTs and records every request as it arrived.

scripts/retail/capture_server.py serves captures/pages/host-gate-2.html on 127.0.0.1. Held here, over a
real socket:

  * the page is served byte for byte, and a POST to /result?dpr1 is stored as <out>/dpr1.json (a body of
    several megabytes too) and ends the wait of the run;
  * a label that is not a plain name cannot leave the output directory;
  * every request is recorded in arrival order with its request line and its header lines in the case and
    the order they were sent, several requests on one kept-alive connection included;
  * the subresources the page asks for are answered, and a request for a certificate (OCSP, CA issuers, CRL) is
    listed apart;
  * what is written to wire.json has the format `retail-wire/1`.
"""
from __future__ import annotations

import json
import re
import socket

import pytest

from _retail_scripts import ROOT, load

pytestmark = pytest.mark.integration

srv = load("capture_server")
PAGE = ROOT / "captures" / "pages" / "host-gate-2.html"


@pytest.fixture
def running(tmp_path):
    http, tls, recorder = srv.start(PAGE, tmp_path / "out")
    yield http, recorder, tmp_path / "out"
    srv.stop(http, tls)


def _talk(port: int, raw: bytes, expect_responses: int = 1) -> bytes:
    """Send raw bytes and return the bytes of `expect_responses` COMPLETE responses (head and the
    Content-Length bytes of the body of each), or everything that came before the server closed.
    The reply is framed, never guessed from what one read happened to return: a TCP stream
    hands a 33 KB body over in as many pieces as the kernel likes."""
    got = b""
    end = 0  # where the response being read starts
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(raw)

        def more() -> bool:
            nonlocal got
            chunk = s.recv(1 << 20)
            got += chunk
            return bool(chunk)

        for _ in range(expect_responses):
            while b"\r\n\r\n" not in got[end:]:
                if not more():
                    return got
            head_end = got.index(b"\r\n\r\n", end) + 4
            match = re.search(rb"(?i)^content-length: *(\d+)", got[end:head_end], re.MULTILINE)
            end = head_end + (int(match.group(1)) if match else 0)
            while len(got) < end:
                if not more():
                    return got
    return got


def _get(path: str, *headers: str) -> bytes:
    return (f"GET {path} HTTP/1.1\r\n" + "".join(h + "\r\n" for h in headers) + "\r\n").encode("latin-1")


def test_the_page_is_served_as_it_is_in_the_repository(running):
    http, recorder, _out = running
    reply = _talk(http.server_address[1], _get("/?dpr1", "Host: 127.0.0.1"))
    assert reply.startswith(b"HTTP/1.1 200 OK\r\n") and b"Content-Type: text/html; charset=utf-8" in reply
    assert reply.endswith(PAGE.read_bytes())


def test_a_posted_result_is_stored_under_its_label_and_ends_the_wait(running):
    http, recorder, out = running
    body = json.dumps({"format": "host-gate/1", "items": {"a": 1}}).encode()
    request = (b"POST /result?dpr1 HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: %d\r\n\r\n" % len(body)) + body
    assert not recorder.result_event.is_set()
    reply = _talk(http.server_address[1], request)
    assert reply.startswith(b"HTTP/1.1 204 No Content")
    assert (out / "dpr1.json").read_bytes() == body
    assert recorder.result_event.wait(5)


def test_a_result_of_several_megabytes_is_stored_whole(running):
    http, recorder, out = running
    body = b'{"x":"' + b"a" * (5 << 20) + b'"}'
    request = (b"POST /result?dpr1 HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: %d\r\n\r\n" % len(body)) + body
    reply = _talk(http.server_address[1], request)
    assert reply.startswith(b"HTTP/1.1 204")
    assert (out / "dpr1.json").read_bytes() == body


@pytest.mark.parametrize("target, stored", [("/result?dpr1.25", "dpr1.25.json"), ("/result", "result.json"),
                                            ("/result?..%2f..%2fx", "result.json"), ("/result?../../x", "result.json"),
                                            ("/result?a/b", "result.json"), ("/result?..", "result.json")])
def test_a_label_cannot_leave_the_output_directory(running, target, stored):
    http, recorder, out = running
    request = b"POST %s HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}" % target.encode()
    assert _talk(http.server_address[1], request).startswith(b"HTTP/1.1 204")
    assert [p.name for p in out.iterdir()] == [stored]
    assert not (out.parent / "x.json").exists() and not (out.parent.parent / "x.json").exists()


def test_a_request_is_recorded_with_its_headers_in_the_order_and_case_sent(running):
    http, recorder, _out = running
    sent = ["Host: 127.0.0.1:1", "User-Agent: probe/1", "accept: */*", "Accept-Language: en-US,en;q=0.5",
            "Accept-Encoding: gzip, deflate", "Referer: http://127.0.0.1:1/", "Connection: close", "Sec-Fetch-Dest: image"]
    _talk(http.server_address[1], _get("/sub/image.png", *sent))
    (request,) = recorder.dump()["http_requests"]
    assert (request["method"], request["target"], request["http_version"]) == ("GET", "/sub/image.png", "HTTP/1.1")
    assert [f"{n}: {v}" for n, v in request["headers"]] == sent
    assert request["raw_head"] == "GET /sub/image.png HTTP/1.1\r\n" + "\r\n".join(sent)
    assert request["seq"] == 1


def test_requests_on_one_kept_alive_connection_are_recorded_in_arrival_order(running):
    http, recorder, _out = running
    paths = ["/sub/style.css", "/sub/script.js", "/sub/image.png", "/favicon.ico"]
    reply = _talk(http.server_address[1], b"".join(_get(p, "Host: x") for p in paths), expect_responses=4)
    assert reply.count(b"HTTP/1.1 200 OK") == 3 and reply.count(b"HTTP/1.1 404 Not Found") == 1
    got = recorder.dump()["http_requests"]
    assert [r["target"] for r in got] == paths and [r["seq"] for r in got] == [1, 2, 3, 4]


@pytest.mark.parametrize("path, ctype", [("/sub/style.css", "text/css"), ("/sub/script.js", "text/javascript"),
                                         ("/sub/image.png", "image/png"), ("/sub/font.woff2", "font/woff2"),
                                         ("/sub/audio.ogg", "audio/ogg"), ("/sub/fetch", "text/plain"),
                                         ("/sub/xhr", "text/plain")])
def test_the_subresources_of_the_page_are_answered(running, path, ctype):
    http, _recorder, _out = running
    reply = _talk(http.server_address[1], _get(path, "Host: x"))
    assert reply.startswith(b"HTTP/1.1 200 OK") and f"Content-Type: {ctype}".encode() in reply


def test_the_page_learns_the_tls_port_from_the_server(running):
    http, recorder, _out = running
    reply = _talk(http.server_address[1], _get("/sub/config.json", "Host: x"))
    assert json.loads(reply.split(b"\r\n\r\n", 1)[1]) == {"tls_port": http.tls_port} and http.tls_port


def test_a_post_of_the_page_is_answered_without_a_body(running):
    http, recorder, _out = running
    request = b"POST /sub/post HTTP/1.1\r\nHost: x\r\nContent-Type: text/plain;charset=UTF-8\r\nContent-Length: 4\r\n\r\nwire"
    assert _talk(http.server_address[1], request).startswith(b"HTTP/1.1 204")
    (got,) = recorder.dump()["http_requests"]
    assert got["method"] == "POST" and ["Content-Length", "4"] in got["headers"]


def test_a_request_for_a_certificate_is_listed_apart(running):
    http, recorder, _out = running
    port = http.server_address[1]
    _talk(port, _get("/sub/fetch", "Host: x"))
    _talk(port, _get("/ocsp/abc", "Host: x"))
    _talk(port, _get("/x", "Host: x", "Accept: application/pkix-cert"))
    _talk(port, b"POST /status HTTP/1.1\r\nHost: x\r\nContent-Type: application/ocsp-request\r\nContent-Length: 0\r\n\r\n")
    wire = recorder.dump()
    assert wire["certificate_requests"] == [2, 3, 4]
    assert [r["seq"] for r in wire["http_requests"]] == [1, 2, 3, 4]


def test_the_wire_file_has_its_format_and_both_lists(running):
    http, recorder, _out = running
    wire = recorder.dump()
    assert wire == {"format": "retail-wire/1", "http_requests": [], "certificate_requests": [], "tls_connections": []}
    json.dumps(wire)


def test_a_malformed_request_is_dropped_without_stopping_the_server(running):
    http, recorder, _out = running
    port = http.server_address[1]
    _talk(port, b"this is not http\r\n\r\n")
    assert _talk(port, _get("/sub/fetch", "Host: x")).startswith(b"HTTP/1.1 200 OK")


def test_the_page_carries_the_wire_block_and_no_extra_item():
    """host-gate-2 is host-gate-1 plus the block that makes the requests: the result's format and items are the same."""
    one = (ROOT / "captures" / "pages" / "host-gate-1.html").read_text(encoding="utf-8")
    two = PAGE.read_text(encoding="utf-8")
    block_start = two.index("// ================================================================= WIRE")
    block_end = two.index("window.__hostGate = G;")
    assert "format: 'host-gate/1'" in two
    added = ("// host-gate-2 = ", "// other, so the capture", "// changes none: the result")
    rest = [ln for ln in (two[:block_start] + two[block_end:]).split(chr(10)) if not ln.startswith(added)]
    assert chr(10).join(rest) == one, "host-gate-2 differs from host-gate-1 outside the wire block and its header"
    # nothing in the block stores an item or a noise key
    block = two[block_start:block_end]
    assert "put(" not in block and "noise(" not in block and "G." not in block
