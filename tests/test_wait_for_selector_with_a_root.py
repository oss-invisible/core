"""`wait_for_selector` looks INSIDE a root element when it is given one.

Playwright's `ElementHandle.waitForSelector` is `Frame.waitForSelector` with the
element as `scope` (`coreBundle.js`, `waitForSelector(progress, selector,
performActionPreChecksAndLog, options, scope)`): the selector is resolved
against the element and not against the document, and an element that is no
longer connected fails with "Element is not attached to the DOM" on every state,
`hidden` and `detached` included.

⛔ THE KNOWN-BAD INPUT: `query_selector` was hard-wired to `document`, so a
`wait_for_selector("li", state="visible")` meant for the list `<ul>` resolved
the first `li` of the whole page.

Without a root nothing changes: the declaration sent and the call the actions
make are the ones made before.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler import PageActs
from invisible_core.juggler.actions import Actions, ElementNotActionable
from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import (
    UTILITY_WORLD, EvaluationError, InjectedScript)

pytestmark = pytest.mark.unit

S = "S1"
F = "frame-1"
NOT_ATTACHED = "Element is not attached to the DOM"


# ── the protocol layer ──────────────────────────────────────────────────────
class _Engine(EventListeners):
    """Runtime commands recorded; a call carrying a `disconnected` root answers
    as a page that threw."""

    def __init__(self, disconnected=()):
        super().__init__()
        self.disconnected = set(disconnected)
        self.calls = []

    def world(self, context):
        self.dispatch_event("Runtime.executionContextCreated",
                            {"executionContextId": context,
                             "auxData": {"frameId": F, "name": UTILITY_WORLD}}, S)

    def send(self, method, params=None, session=None, timeout=None):
        params = params or {}
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "script"}}
        if method == "Runtime.callFunction":
            self.calls.append(params)
            roots = [a["objectId"] for a in params["args"][1:] if "objectId" in a]
            if any(r in self.disconnected for r in roots):
                return {"exceptionDetails": {"text": "Error: " + NOT_ATTACHED}}
            return {"result": {"objectId": "found", "value": 2}}
        return {}


def _injected(**kw):
    engine = _Engine(**kw)
    inj = InjectedScript(engine, S)
    engine.world("c1")
    return engine, inj


def test_without_a_root_the_call_is_the_one_made_before():
    engine, inj = _injected()
    assert inj.query_selector(F, "li", strict=True) == "found"
    (call,) = engine.calls
    assert call["functionDeclaration"] == (
        "(injected, sel, strict) => {"
        "  const p = injected.parseSelector(sel);"
        "  return injected.querySelector(p, document, strict) || null; }")
    assert call["args"] == [{"objectId": "script"}, {"value": "li"},
                            {"value": True}]


def test_a_root_reaches_the_injected_call_as_the_scope():
    engine, inj = _injected()
    assert inj.query_selector(F, "li", strict=False, root="ul@c1") == "found"
    (call,) = engine.calls
    declaration = call["functionDeclaration"]
    assert "injected.querySelector(p, root, strict)" in declaration
    assert "document" not in declaration
    assert call["args"] == [{"objectId": "script"}, {"value": "li"},
                            {"value": False}, {"objectId": "ul@c1"}]


def test_a_disconnected_root_is_stocks_text_and_nothing_else():
    engine, inj = _injected(disconnected={"ul@c1"})
    with pytest.raises(EvaluationError) as raised:
        inj.query_selector(F, "li", root="ul@c1")
    assert str(raised.value) == NOT_ATTACHED


def test_the_root_is_checked_before_anything_is_resolved():
    engine, inj = _injected()
    inj.query_selector(F, "li", root="ul@c1")
    declaration = engine.calls[0]["functionDeclaration"]
    assert NOT_ATTACHED in declaration
    assert declaration.index("isConnected") < declaration.index("querySelector(")


def test_query_selector_all_without_a_root_counts_the_document():
    engine, inj = _injected()
    assert inj.query_selector_all(F, "li") == ["found", "found"]
    declarations = [c["functionDeclaration"] for c in engine.calls]
    assert all("document" in d and "root" not in d for d in declarations)
    assert [c["args"][1:] for c in engine.calls] == [
        [{"value": "li"}],
        [{"value": "li"}, {"value": 0}],
        [{"value": "li"}, {"value": 1}]]


def test_query_selector_all_with_a_root_looks_inside_it():
    engine, inj = _injected()
    assert inj.query_selector_all(F, "li", root="ul@c1") == ["found", "found"]
    assert len(engine.calls) == 3
    for call in engine.calls:
        assert "document" not in call["functionDeclaration"]
        assert {"objectId": "ul@c1"} in call["args"]


def test_query_selector_all_on_a_disconnected_root_is_stocks_text():
    engine, inj = _injected(disconnected={"ul@c1"})
    with pytest.raises(EvaluationError) as raised:
        inj.query_selector_all(F, "li", root="ul@c1")
    assert str(raised.value) == NOT_ATTACHED


# ── the wait ────────────────────────────────────────────────────────────────
class _Lifecycle:
    main_frame = F


class _Injected:
    """The injected half of a wait: `found` and `visible` are the answers of
    each turn in order (the last one repeats); every query is recorded as it
    was made."""

    def __init__(self, found, visible=(True,), disconnected=False):
        self.found = list(found)
        self.visible = list(visible)
        self.disconnected = disconnected
        self.queries = []
        self.disposed = []

    def query_selector(self, frame, selector, strict=False, **kw):
        self.queries.append((frame, selector, strict, kw))
        if kw.get("root") and self.disconnected:
            raise EvaluationError(NOT_ATTACHED)
        return self.found.pop(0) if len(self.found) > 1 else self.found[0]

    def element_state(self, frame, element, state):
        return self.visible.pop(0) if len(self.visible) > 1 else self.visible[0]

    def dispose(self, frame, element):
        self.disposed.append(element)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("invisible_core.juggler.actions.time.sleep",
                        lambda s: None)


def _actions(injected):
    return Actions(object(), "s", _Lifecycle(), injected, acts=PageActs(1))


def test_the_root_reaches_the_query_of_every_turn():
    injected = _Injected([None, None, "li"])
    got = _actions(injected).wait_for_selector(
        "li", state="attached", timeout=5.0, strict=True, root="ul@c1")
    assert got == "li"
    assert injected.queries == [(F, "li", True, {"root": "ul@c1"})] * 3


def test_without_a_root_the_query_is_the_call_made_before():
    injected = _Injected(["li"])
    assert _actions(injected).wait_for_selector(
        "li", state="attached", timeout=5.0) == "li"
    assert injected.queries == [(F, "li", False, {})]


def test_a_visible_wait_in_a_root_waits_for_the_element_to_show():
    injected = _Injected(["a", "b"], visible=[False, True])
    got = _actions(injected).wait_for_selector("li", timeout=5.0, root="ul@c1")
    assert got == "b"
    assert injected.disposed == ["a"]
    assert all(q[3] == {"root": "ul@c1"} for q in injected.queries)


def test_a_detached_wait_in_a_root_ends_when_nothing_matches_inside():
    injected = _Injected(["li", "li", None])
    assert _actions(injected).wait_for_selector(
        "li", state="detached", timeout=5.0, root="ul@c1") is None
    assert injected.disposed == ["li", "li"]
    assert len(injected.queries) == 3
    assert all(q[3] == {"root": "ul@c1"} for q in injected.queries)


@pytest.mark.parametrize("state", ["visible", "hidden", "attached", "detached"])
def test_a_disconnected_root_fails_at_once_with_stocks_text(state):
    injected = _Injected([None], disconnected=True)
    with pytest.raises(EvaluationError) as raised:
        _actions(injected).wait_for_selector(
            "li", state=state, timeout=30.0, root="ul@c1")
    assert str(raised.value) == NOT_ATTACHED
    assert len(injected.queries) == 1


def test_a_wait_in_a_root_still_gives_up_at_its_time(monkeypatch):
    clock = iter(range(0, 10000, 20))
    monkeypatch.setattr("invisible_core.juggler.deadline.time.monotonic",
                        lambda: next(clock))
    with pytest.raises(ElementNotActionable):
        _actions(_Injected([None])).wait_for_selector(
            "li", state="attached", timeout=30.0, root="ul@c1")
