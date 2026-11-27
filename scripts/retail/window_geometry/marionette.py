"""A minimal Marionette client (protocol level 3) over TCP, stdlib only.

Framing is `<length>:<json>`; a command is `[0, id, name, params]` and its reply `[1, id, error, result]`.
`encode` and `Decoder` are pure so a test can run them without a browser.
"""
from __future__ import annotations

import json
import socket
import time
from typing import Any, Dict, List, Optional, Tuple


class MarionetteError(Exception):
    """The browser answered a command with an error."""


class ConnectionClosed(MarionetteError):
    """The browser closed the connection: nothing more can be asked of it (unlike an error reply to one command)."""


def encode(message: Any) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return str(len(payload)).encode("ascii") + b":" + payload


class Decoder:
    """Cuts a byte stream into messages: feed what arrives, get the whole messages it completes."""

    def __init__(self) -> None:
        self.buffer = b""

    def feed(self, data: bytes) -> List[Any]:
        self.buffer += data
        out: List[Any] = []
        while True:
            head, colon, rest = self.buffer.partition(b":")
            if not colon:
                return out
            if not head.isdigit():
                raise MarionetteError(f"bad frame header {head[:20]!r}")
            size = int(head)
            if len(rest) < size:
                return out
            out.append(json.loads(rest[:size].decode("utf-8")))
            self.buffer = rest[size:]


def command(message_id: int, name: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
    return [0, message_id, name, params or {}]


def reply(message: Any) -> Tuple[int, Any, Any]:
    """(id, error, result) of a `[1, id, error, result]` message."""
    if not (isinstance(message, list) and len(message) == 4 and message[0] == 1):
        raise MarionetteError(f"not a reply: {message!r}")
    return message[1], message[2], message[3]


class Client:
    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.port, self.host = port, host
        self.sock: Optional[socket.socket] = None
        self.decoder, self.pending, self.next_id, self.context = Decoder(), [], 0, None
        self.hello: Any = None

    def connect(self, timeout: float = 90.0) -> Any:
        """Connect (the browser opens the port late) and read the server's hello."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=5)
                self.sock.settimeout(max(1.0, deadline - time.monotonic()))
                self.decoder, self.pending = Decoder(), []
                self.hello = self._read()
                return self.hello
            except (OSError, MarionetteError):
                self.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def _read(self) -> Any:
        while not self.pending:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionClosed("the connection closed")
            self.pending.extend(self.decoder.feed(data))
        return self.pending.pop(0)

    def call(self, name: str, params: Optional[Dict[str, Any]] = None, timeout: float = 60.0) -> Any:
        self.next_id += 1
        self.sock.settimeout(timeout)
        self.sock.sendall(encode(command(self.next_id, name, params)))
        while True:
            message = self._read()
            if not (isinstance(message, list) and message and message[0] == 1):
                continue  # not a reply (the server sends nothing else today)
            message_id, error, result = reply(message)
            if message_id != self.next_id:
                continue
            if error:
                raise MarionetteError(f"{name}: {error}")
            return result

    def new_session(self) -> Any:
        return self.call("WebDriver:NewSession", {"capabilities": {}})

    def navigate(self, url: str) -> Any:
        return self.call("WebDriver:Navigate", {"url": url})

    def set_context(self, context: str) -> None:
        if context != self.context:
            self.call("Marionette:SetContext", {"value": context})
            self.context = context

    def execute_script(self, script: str, args: Optional[List[Any]] = None, context: Optional[str] = None) -> Any:
        if context:
            self.set_context(context)
        return self.call("WebDriver:ExecuteScript", {"script": script, "args": args or []})["value"]

    def execute_async_script(self, script: str, args: Optional[List[Any]] = None, context: Optional[str] = None) -> Any:
        """`script` ends by calling the last of `arguments`."""
        if context:
            self.set_context(context)
        return self.call("WebDriver:ExecuteAsyncScript", {"script": script, "args": args or []})["value"]

    def delete_session(self) -> Any:
        return self.call("WebDriver:DeleteSession")

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
