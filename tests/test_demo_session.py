"""The simulated session: what it has to contain to be worth looking at.

A demo whose conversation is three lines of «hello» proves nothing — the things
that go wrong in this viewer go wrong on a message long enough to fold, on a
line the tone rules paint, on a name in Japanese, on a history longer than the
window. So these tests are about the SHAPES the script has to contain, not
about the prose in it: change the wording freely, lose one of these and the
demo stops being able to show the thing it exists to show.
"""

from __future__ import annotations

import pytest

from collab import demo, themes
from collab.client import tui
from collab.client.tui import conversation_rows, roster_rows
from collab.protocol import (KIND_CHAT, KIND_FILE, KIND_HELLO, KIND_PRESENCE,
                             KIND_TASK)


@pytest.fixture()
def built_in(folder):
    """Only the themes that ship, in a home with no user themes in it."""
    return sorted(themes.all_themes(folder=folder))


# --- it runs on nothing ------------------------------------------------------

def test_it_needs_no_session_and_no_files(tmp_path, monkeypatch):
    """The whole point: no hub, no daemon, no state directory."""
    missing = tmp_path / "nowhere"
    monkeypatch.setenv("COLLAB_HOME", str(missing))

    model = demo.model()
    model.load_initial(limit=5)

    assert model.events, "the viewer opened on something"
    assert not missing.exists(), "and wrote nothing to disk"


def test_the_viewer_believes_it_is_live(monkeypatch, tmp_path):
    """Otherwise the badge reads «offline» and the roster refuses to say who is
    here — the two things a demo of the roster most needs to show."""
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "nowhere"))
    model = demo.model()
    model.refresh_side()

    assert model.state() == "live"
    assert model.roster_is_current()


# --- what the script has to contain -----------------------------------------

def test_it_is_longer_than_the_window():
    """So scrolling back has somewhere to go and the paging code runs."""
    assert len(demo.events()) > tui.WINDOW


@pytest.mark.parametrize("kind", [KIND_CHAT, KIND_HELLO, KIND_PRESENCE,
                                  KIND_TASK, KIND_FILE])
def test_every_kind_the_renderer_special_cases_is_in_it(kind):
    """`_body_lines` has a branch per kind; a demo that never reaches one is a
    demo that cannot show it is broken."""
    assert any(e.kind == kind for e in demo.events())


def test_something_in_it_is_long_enough_to_fold():
    rows = conversation_rows(demo.events(), 80, demo.YOU)
    assert any(r.button for r in rows), "no «show more» to click"


@pytest.mark.parametrize("hour,minute", [(0, 1), (0, 30), (7, 0), (13, 45),
                                         (23, 59)])
def test_the_day_changes_partway_through_at_any_hour(hour, minute):
    """The day separator only draws on a boundary, so the script has to cross
    one — AT WHATEVER HOUR the demo is opened. At 00:01 UTC every one of
    today's beats is still on yesterday's date, and a backlog anchored to «a
    day ago» lands on that same date: no boundary, no separator, and nothing to
    tell you the feature is gone."""
    import datetime as dt

    now = dt.datetime(2026, 9, 2, hour, minute, tzinfo=dt.timezone.utc)
    days = {e.ts[:10] for e in demo.events(now=now)}
    assert len(days) >= 2, f"{sorted(days)} at {hour:02d}:{minute:02d}"


def test_the_tone_rules_have_something_to_paint():
    """Good, bad, warning and information lines: four colours that are only
    ever seen when a line happens to match, and never on request."""
    tones = {tui.line_pair(line)
             for e in demo.events()
             for line in (e.text or "").splitlines()}
    for tone in (tui.C_GOOD, tui.C_BAD, tui.C_WARNLINE, tui.C_INFO):
        assert tone in tones, f"nothing in the script paints {tone}"


def test_more_than_one_person_speaks():
    """The speaker colours and the «own side» of the bubble need someone to
    tell apart from you."""
    senders = {e.sender for e in demo.events() if e.sender}
    assert demo.YOU in senders
    assert len(senders) >= 3


def test_a_wide_alphabet_is_in_it():
    """CJK and emoji take two columns each, which is where the bubble maths
    breaks. Measured, not eyeballed: something in the script must be wider
    than its own character count."""
    assert any(tui._w(e.text) > len(e.text) for e in demo.events() if e.text)


# --- it survives every theme -------------------------------------------------

def test_it_renders_under_every_built_in_theme(monkeypatch, built_in):
    for name in built_in:
        monkeypatch.setattr(tui, "theme", lambda name=name: name)
        rows = conversation_rows(demo.events(), 80, demo.YOU)
        assert rows, f"the {name} theme rendered nothing"
        assert all(tui._w(r.text) <= 80 for r in rows), \
            f"the {name} theme drew wider than the pane"


@pytest.mark.parametrize("width", [24, 40, 56, 80, 200])
def test_it_renders_at_every_width(width):
    rows = conversation_rows(demo.events(), width, demo.YOU)
    assert all(tui._w(r.text) <= width for r in rows)


# --- the roster --------------------------------------------------------------

def test_the_roster_says_who_is_here(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "nowhere"))
    model = demo.model()
    model.refresh_side()

    text = "\n".join(r.text for r in roster_rows(model, 80))
    for person in model.participants():
        assert person["name"] in text
    assert "(you)" in text, "and which one is the reader"


# --- the real paging code runs on it ----------------------------------------

def test_reading_back_and_returning_to_the_live_end(monkeypatch, tmp_path):
    """Not a stand-in for the Model: the demo swaps the LOG underneath the real
    one, so windowing, trimming and paging are the shipped code."""
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "nowhere"))
    model = demo.model()
    model.load_initial(limit=5)

    assert model.more_above(), "there is history behind the opening screen"
    assert model.load_older() > 0

    model.load_start()
    assert not model.more_above()
    assert model.pending() > 0, "and the rest is still ahead"

    model.load_tail()
    assert model.pending() == 0
