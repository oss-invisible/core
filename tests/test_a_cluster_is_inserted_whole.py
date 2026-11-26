"""A character with no key goes in as its whole grapheme cluster, in one insert.

`Keyboard.type` inserted every keyless character one code point at a time, and
each `Page.insertText` is a composition of its own, so the page saw:

- "🇿🇦" (two regional indicators) as two compositions. Lexical 0.21 and 0.53
  both kept it as "🇿🇦🇦": the second `compositionstart` arrived with the first
  half selected, and the editor duplicated it;
- "🛍️" (U+1F6CD U+FE0F) as the emoji and then a lone VS16 committed by itself;
- "é" written decomposed, or a keycap "1️⃣", as a KEY for the base followed by
  inserted marks, which no keyboard and no picker produces.

A picker, a dead key or an IME commits the whole cluster in one composition.
Text whose clusters are all single characters is typed exactly as before.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler._graphemes import graphemes
from invisible_core.juggler.keyboard import Keyboard

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.sent = []

    def send(self, method, params, **kw):
        self.sent.append((method, params))
        return {}


def _kb():
    c = _Fake()
    return c, Keyboard(c, "S", acts=PageActs(1))


def _what(sent):
    return [("insert", p["text"]) if m == "Page.insertText" else (p["type"], p["key"])
            for m, p in sent]


@pytest.mark.parametrize("text, want", [
    ("🇿🇦", ["🇿🇦"]),
    ("🇿🇦🇺🇸", ["🇿🇦", "🇺🇸"]),
    ("🇿🇦🇦", ["🇿🇦", "🇦"]),
    ("🛍️ a", ["🛍️", " ", "a"]),
    ("é", ["é"]),
    ("1️⃣", ["1️⃣"]),
    ("👍🏽", ["👍🏽"]),
    ("👩‍💻!", ["👩‍💻", "!"]),
    ("🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
     ["🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"]),
    ("a\ń", ["a", "\n", "́"]),
    ("\r\n", ["\r", "\n"]),
    ("Brett's\n\nWhile", list("Brett's\n\nWhile")),
])
def test_text_is_split_where_a_person_enters_it(text, want):
    assert graphemes(text) == want
    assert "".join(graphemes(text)) == text


def test_a_flag_is_one_insert():
    c, kb = _kb()
    kb.type("X 🇿🇦")
    assert _what(c.sent) == [("keydown", "Shift"), ("keydown", "X"), ("keyup", "X"),
                             ("keyup", "Shift"), ("keydown", " "), ("keyup", " "),
                             ("insert", "🇿🇦")]


def test_an_emoji_with_its_variation_selector_is_one_insert_and_the_space_a_key():
    c, kb = _kb()
    kb.type("🛍️ B")
    assert _what(c.sent) == [("insert", "🛍️"), ("keydown", " "), ("keyup", " "),
                             ("keydown", "Shift"), ("keydown", "B"), ("keyup", "B"),
                             ("keyup", "Shift")]


def test_a_base_with_a_combining_mark_is_not_a_key_then_a_mark():
    c, kb = _kb()
    kb.type("é")
    assert _what(c.sent) == [("insert", "é")]


def test_text_without_clusters_is_one_key_per_character():
    c, kb = _kb()
    kb.type("Brett's\n\nWhile")
    keys = [k for kind, k in _what(c.sent) if kind == "keydown" and k != "Shift"]
    assert keys == ["B", "r", "e", "t", "t", "'", "s", "Enter", "Enter",
                    "W", "h", "i", "l", "e"]
    assert not [w for w in _what(c.sent) if w[0] == "insert"]
