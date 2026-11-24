"""The server a retail capture runs against: the page, the result, and what the browser sends.

One process, two listeners on 127.0.0.1 (a secure context):

  http   serves captures/pages/host-gate-2.html, stores the result the page POSTs as
         <out>/<label>.json (the label is the query of the page URL, `dpr1`), answers the
         page's subresource requests, and records EVERY request exactly as it arrived: the
         request line and the header lines in order, in the case and spelling they were sent
  tls    a port the page fetches over https: it reads the first TLS record of each connection
         (the ClientHello), keeps its raw bytes, answers a fatal alert and closes. The
         handshake fails on purpose: the page reads nothing from it

What it records is written by `Recorder.dump` as wire.json (format `retail-wire/1`). Nothing of
a browser is assumed: a request is whatever bytes came in. Certificate-related requests
(OCSP, CA issuers, CRLs) are listed apart; a browser that never makes one leaves the list empty.
"""
from __future__ import annotations

import json
import re
import socket
import socketserver
import struct
import sys
import threading
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clienthello  # noqa: E402

WIRE_FORMAT = "retail-wire/1"
MAX_HEAD = 64 * 1024
MAX_BODY = 64 * 1024 * 1024
MAX_TLS_CONNECTIONS = 16
IDLE_SECONDS = 30
#: TLS alert, fatal, handshake_failure: the whole answer of the tls listener.
TLS_FATAL_ALERT = bytes([0x15, 0x03, 0x03, 0x00, 0x02, 0x02, 0x28])


def _png_1x1() -> bytes:
    """A valid 1x1 transparent PNG, built here so no byte of it is typed."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b""))


#: What the page's subresource requests get: a few bytes of the right type, nothing a browser needs.
SUBRESOURCES: Dict[str, Tuple[str, bytes]] = {
    "/sub/style.css": ("text/css", b"@font-face{font-family:wire;src:url(/sub/font.woff2)}\n"),
    "/sub/script.js": ("text/javascript", b"/* wire */\n"),
    "/sub/image.png": ("image/png", _png_1x1()),
    "/sub/font.woff2": ("font/woff2", b"wOF2"),
    "/sub/audio.ogg": ("audio/ogg", b"OggS"),
    "/sub/fetch": ("text/plain", b"ok"),
    "/sub/xhr": ("text/plain", b"ok"),
}
LABEL = re.compile(r"[A-Za-z0-9._-]{1,64}")
CERTIFICATE_PATH = re.compile(r"^/(ocsp|crl|ca|aia|issuer)", re.IGNORECASE)
CERTIFICATE_TYPES = ("application/ocsp-request", "application/ocsp-response", "application/pkix-cert",
                     "application/pkix-crl", "application/x-x509-ca-cert", "application/x-x509-user-cert")


def label_of(target: str) -> str:
    """The file name a result is stored under: the query of the URL when it is a plain name, else `result`."""
    query = target.split("?", 1)[1] if "?" in target else ""
    return query if LABEL.fullmatch(query) and query not in (".", "..") else "result"


def is_certificate_request(request: Dict[str, Any]) -> bool:
    """An OCSP, CA-issuer or CRL fetch: by path, or by the media type it asks for or sends."""
    if CERTIFICATE_PATH.match(request["target"]):
        return True
    for name, value in request["headers"]:
        if name.lower() in ("accept", "content-type") and any(t in value.lower() for t in CERTIFICATE_TYPES):
            return True
    return False


def parse_head(head: bytes) -> Dict[str, Any]:
    """`head` is the request line and header lines up to (not including) the blank line."""
    text = head.decode("latin-1")
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        raise ValueError(f"bad request line {lines[0]!r}")
    headers: List[List[str]] = []
    for line in lines[1:]:
        if ":" not in line:
            raise ValueError(f"bad header line {line!r}")
        name, value = line.split(":", 1)
        headers.append([name, value.strip(" \t")])
    return {"method": parts[0], "target": parts[1], "http_version": parts[2], "headers": headers, "raw_head": text}


class Recorder:
    """What came in, in arrival order. Thread-safe: each listener runs a thread per connection."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.http: List[Dict[str, Any]] = []
        self.tls: List[Dict[str, Any]] = []
        self.results: Dict[str, str] = {}
        self.result_event = threading.Event()

    def add_http(self, request: Dict[str, Any]) -> None:
        with self.lock:
            self.http.append(dict(request, seq=len(self.http) + 1))

    def add_tls(self, raw: bytes, complete: bool) -> None:
        with self.lock:
            if len(self.tls) >= MAX_TLS_CONNECTIONS:
                return
            entry: Dict[str, Any] = {"seq": len(self.tls) + 1, "bytes": len(raw), "complete": complete, "raw_hex": raw.hex()}
            if raw:
                entry["parsed"] = clienthello.parse(raw)
            self.tls.append(entry)

    def dump(self) -> Dict[str, Any]:
        with self.lock:
            http = [dict(r) for r in self.http]
            return {"format": WIRE_FORMAT, "http_requests": http,
                    "certificate_requests": [r["seq"] for r in http if is_certificate_request(r)],
                    "tls_connections": [dict(t) for t in self.tls]}


def _read_head(sock: socket.socket, buf: bytes) -> Tuple[Optional[bytes], bytes]:
    """The next request head and what follows it in the buffer; None when the peer closed."""
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEAD:
            raise ValueError("request head too long")
        chunk = sock.recv(65536)
        if not chunk:
            return None, buf
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    return head, rest


def _read_body(sock: socket.socket, buf: bytes, length: int) -> Tuple[bytes, bytes]:
    if length > MAX_BODY:
        raise ValueError("request body too long")
    while len(buf) < length:
        chunk = sock.recv(min(1 << 20, length - len(buf)))
        if not chunk:
            raise ValueError("peer closed inside a body")
        buf += chunk
    return buf[:length], buf[length:]


class _HttpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: "CaptureHttpServer" = self.server  # type: ignore[assignment]
        sock = self.request
        sock.settimeout(IDLE_SECONDS)
        buf = b""
        try:
            while True:
                head, buf = _read_head(sock, buf)
                if head is None:
                    return
                request = parse_head(head)
                server.recorder.add_http(request)
                length = 0
                for name, value in request["headers"]:
                    if name.lower() == "content-length":
                        length = int(value)
                body, buf = _read_body(sock, buf, length)
                status, ctype, payload = server.route(request, body)
                close = any(n.lower() == "connection" and v.lower() == "close" for n, v in request["headers"])
                sock.sendall(self._response(status, ctype, payload, close))
                if close:
                    return
        except (OSError, ValueError):
            return

    @staticmethod
    def _response(status: int, ctype: str, payload: bytes, close: bool) -> bytes:
        reason = {200: "OK", 204: "No Content", 404: "Not Found"}[status]
        lines = [f"HTTP/1.1 {status} {reason}", f"Content-Length: {len(payload)}", "Cache-Control: no-store"]
        if ctype:
            lines.append(f"Content-Type: {ctype}")
        lines.append("Connection: close" if close else "Connection: keep-alive")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + payload


class CaptureHttpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, page: Path, out_dir: Path, recorder: Recorder, tls_port: Optional[int]):
        self.page_bytes = Path(page).read_bytes()
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.recorder = recorder
        self.tls_port = tls_port
        super().__init__(("127.0.0.1", port), _HttpHandler)

    def route(self, request: Dict[str, Any], body: bytes) -> Tuple[int, str, bytes]:
        method, target = request["method"], request["target"]
        path = target.split("?", 1)[0]
        if method == "GET" and path == "/":
            return 200, "text/html; charset=utf-8", self.page_bytes
        if method == "GET" and path == "/sub/config.json":
            return 200, "application/json", json.dumps({"tls_port": self.tls_port}).encode("ascii")
        if method == "GET" and path in SUBRESOURCES:
            ctype, payload = SUBRESOURCES[path]
            return 200, ctype, payload
        if method == "POST" and path == "/sub/post":
            return 204, "", b""
        if method == "POST" and path == "/result":
            label = label_of(target)
            (self.out_dir / f"{label}.json").write_bytes(body)
            self.recorder.results[label] = label
            self.recorder.result_event.set()
            return 204, "", b""
        return 404, "text/plain", b"not found"


class _TlsHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: "CaptureTlsServer" = self.server  # type: ignore[assignment]
        sock = self.request
        sock.settimeout(5)
        raw, complete = b"", False
        try:
            while len(raw) < 5:
                chunk = sock.recv(5 - len(raw))
                if not chunk:
                    break
                raw += chunk
            if len(raw) == 5:
                size = int.from_bytes(raw[3:5], "big")
                while len(raw) < 5 + size:
                    chunk = sock.recv(5 + size - len(raw))
                    if not chunk:
                        break
                    raw += chunk
                complete = len(raw) == 5 + size
        except OSError:
            pass
        server.recorder.add_tls(raw, complete)
        try:
            if raw:
                sock.sendall(TLS_FATAL_ALERT)
        except OSError:
            pass


class CaptureTlsServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, recorder: Recorder):
        self.recorder = recorder
        super().__init__(("127.0.0.1", port), _TlsHandler)


def start(page: Path, out_dir: Path, http_port: int = 0, tls_port: int = 0) -> Tuple[CaptureHttpServer, CaptureTlsServer, Recorder]:
    """Both listeners, each serving in its own daemon thread. Ports 0 take a free one."""
    recorder = Recorder()
    tls = CaptureTlsServer(tls_port, recorder)
    http = CaptureHttpServer(http_port, page, out_dir, recorder, tls.server_address[1])
    for server in (tls, http):
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return http, tls, recorder


def stop(*servers: socketserver.BaseServer) -> None:
    for server in servers:
        server.shutdown()
        server.server_close()
