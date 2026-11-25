"""The Marionette client of the window-geometry capture frames and cuts messages the way protocol level 3 does.

A message is `<length>:<json>`, the length in bytes of the JSON; a command is `[0, id, name, params]` and its reply is
`[1, id, error, result]`. The pure functions are held first, then the client against a fake server on a local socket (no
browser): the command it sends, the reply it waits for among others, and the error it raises.
"""
from __future__ import annotations

import json
import socket
import threading

import pytest

from _retail_scripts import load_window_geometry

pytestmark = pytest.mark.unit

m = load_window_geometry("marionette")


def test_a_message_is_its_byte_length_a_colon_and_its_json():
    assert m.encode([0, 1, "WebDriver:NewSession", {}]) == b'31:[0,1,"WebDriver:NewSession",{}]'


def test_the_length_counts_bytes_not_characters():
    wire = m.encode({"v": "é"})
    head, _, body = wire.partition(b":")
    assert int(head) == len(body) == len('{"v":"é"}'.encode("utf-8"))
    assert m.Decoder().feed(wire) == [{"v": "é"}]


@pytest.mark.parametrize("message", [[1, 7, None, {"value": 5}], {"applicationType": "gecko", "marionetteProtocol": 3}, [], {}, [0, 2, "x", {"a": [1, 2.5, None, True]}]])
def test_encode_then_decode_is_the_message(message):
    assert m.Decoder().feed(m.encode(message)) == [message]


def test_several_messages_in_one_read_are_all_returned_in_order():
    wire = b"".join(m.encode(x) for x in ([1, 1, None, {}], [1, 2, None, {}], [1, 3, None, {}]))
    assert [x[1] for x in m.Decoder().feed(wire)] == [1, 2, 3]


def test_a_message_cut_at_any_byte_is_returned_when_its_last_byte_arrives():
    message = [1, 9, None, {"value": "x" * 40}]
    wire = m.encode(message)
    for cut in range(1, len(wire)):
        decoder = m.Decoder()
        assert decoder.feed(wire[:cut]) == []
        assert decoder.feed(wire[cut:]) == [message]


def test_a_header_that_is_not_a_number_is_refused():
    with pytest.raises(m.MarionetteError, match="header"):
        m.Decoder().feed(b"x:[1]")


def test_command_and_reply_have_the_shape_of_level_3():
    assert m.command(4, "Marionette:SetContext", {"value": "chrome"}) == [0, 4, "Marionette:SetContext", {"value": "chrome"}]
    assert m.command(5, "WebDriver:DeleteSession") == [0, 5, "WebDriver:DeleteSession", {}]
    assert m.reply([1, 4, None, {"ok": 1}]) == (4, None, {"ok": 1})
    with pytest.raises(m.MarionetteError, match="not a reply"):
        m.reply([0, 4, "x", {}])


class FakeServer:
    """Sends the hello, then answers each command with `answer(command)` (a list of messages to send back)."""

    def __init__(self, answer):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.answer, self.received = answer, []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.listener.accept()
        with conn:
            conn.sendall(m.encode({"applicationType": "gecko", "marionetteProtocol": 3}))
            decoder = m.Decoder()
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                for message in decoder.feed(data):
                    self.received.append(message)
                    for out in self.answer(message):
                        conn.sendall(m.encode(out))

    def close(self):
        self.listener.close()
        self.thread.join(5)


def test_the_client_reads_the_hello_sends_commands_and_returns_the_value():
    def answer(cmd):
        if cmd[2] == "WebDriver:ExecuteScript":
            return [[1, cmd[1] + 100, None, {"value": "someone else's"}], [1, cmd[1], None, {"value": cmd[3]["args"]}]]
        return [[1, cmd[1], None, {}]]

    server = FakeServer(answer)
    client = m.Client(server.port)
    try:
        assert client.connect(timeout=10)["marionetteProtocol"] == 3
        client.new_session()
        assert client.execute_script("return arguments[0];", [390], context="chrome") == [390]
        client.execute_script("return 1;", context="chrome")
        client.execute_script("return 2;", context="content")
        client.delete_session()
    finally:
        client.close()
        server.close()
    names = [c[2] for c in server.received]
    assert names == ["WebDriver:NewSession", "Marionette:SetContext", "WebDriver:ExecuteScript", "WebDriver:ExecuteScript",
                     "Marionette:SetContext", "WebDriver:ExecuteScript", "WebDriver:DeleteSession"]
    assert [c[1] for c in server.received] == list(range(1, len(names) + 1))
    assert server.received[1][3] == {"value": "chrome"}
    assert server.received[0][3] == {"capabilities": {}}


def test_a_reply_with_an_error_is_raised_with_the_command_name():
    server = FakeServer(lambda cmd: [[1, cmd[1], {"error": "javascript error", "message": "boom"}, None]])
    client = m.Client(server.port)
    try:
        client.connect(timeout=10)
        with pytest.raises(m.MarionetteError, match="WebDriver:Navigate.*javascript error"):
            client.navigate("http://127.0.0.1/")
    finally:
        client.close()
        server.close()


def test_a_connection_that_closes_is_an_error_not_a_hang():
    server = FakeServer(lambda cmd: [])
    client = m.Client(server.port)
    try:
        client.connect(timeout=10)
        server.listener.close()
        client.sock.shutdown(socket.SHUT_WR)
        with pytest.raises((m.MarionetteError, OSError)):
            client.call("WebDriver:Navigate", {"url": "x"}, timeout=5)
    finally:
        client.close()


def test_the_script_commands_carry_their_arguments():
    sent = []
    client = m.Client(0)
    client.call = lambda name, params=None, timeout=60.0: sent.append((name, params)) or {"value": 1}
    client.context = "chrome"
    client.execute_async_script("done(1)", [2], context="chrome")
    assert sent == [("WebDriver:ExecuteAsyncScript", {"script": "done(1)", "args": [2]})]
    assert json.dumps(sent)
