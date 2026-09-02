"""The bottom bar: a scrollbar you can see and a button you can click.

It used to be a line of key names. That tells you `End` exists; it does not
tell you where in the conversation you are, which is the question a reader
actually has when they have been scrolling for ten seconds. So the bar now
draws the position and offers the way back — and both have to survive a pane
twenty-four columns wide, which is where every layout in this file has broken
before.

The arithmetic is tested as arithmetic. Going through curses to find out
whether a thumb is in the right place only adds a way for the measurement to be
the thing that is wrong.
"""

from __future__ import annotations

import curses

import pytest

from collab.client.tui import (RAIL, THUMB, UNLOADED, bottom_bar, scroll_track,
                               Row, Tui)

from test_tui_scroll import FakeModel, _tui


# --- the track ---------------------------------------------------------------

def test_it_fills_exactly_the_width_it_is_given():
    for width in range(4, 60):
        assert len(scroll_track(width, offset=3, rows=10, total=40)) == width


def test_the_thumb_starts_at_the_left_and_ends_at_the_right():
    top = scroll_track(20, offset=0, rows=10, total=110)
    bottom = scroll_track(20, offset=100, rows=10, total=110)

    assert top.startswith(THUMB), "at the top of the conversation"
    assert top.endswith(RAIL)
    assert bottom.endswith(THUMB), "and at the bottom"
    assert bottom.startswith(RAIL)


def test_the_thumb_is_the_size_of_what_you_can_see():
    """Half the conversation on screen is half a rail of thumb: the width of it
    is the only thing that says how much of the whole a screen is."""
    half = scroll_track(20, offset=0, rows=50, total=100)
    assert half.count(THUMB) == 10


def test_a_huge_conversation_still_has_a_thumb_to_see():
    """Rounded down honestly it disappears at a thousand messages, and an empty
    rail says «nothing to scroll», which is the opposite of the truth."""
    track = scroll_track(20, offset=500, rows=10, total=5000)
    assert track.count(THUMB) >= 1


def test_everything_visible_means_a_full_rail():
    assert scroll_track(12, offset=0, rows=40, total=12) == THUMB * 12


def test_a_full_rail_still_admits_what_it_has_not_loaded():
    """«All of it is on screen» and «that is all there is» are different
    claims. A single-pane view opens on five messages in a window with room
    for forty, and a rail filled end to end said the second one."""
    track = scroll_track(12, offset=0, rows=40, total=12, more_above=True)
    assert track[0] == UNLOADED
    assert track[1:] == THUMB * 11


def test_nothing_loaded_does_not_divide_by_zero():
    assert len(scroll_track(12, offset=0, rows=0, total=0)) == 12


def test_history_that_is_not_loaded_is_marked_at_the_end_it_is_at():
    """The track measures the LOADED conversation. Saying 0 % at the top of a
    window that has five hundred messages behind it would be a lie the reader
    has no way to catch."""
    back = scroll_track(20, offset=0, rows=10, total=40, more_above=True)
    fwd = scroll_track(20, offset=30, rows=10, total=40, more_below=True)

    assert back.startswith(UNLOADED)
    assert not back.endswith(UNLOADED)
    assert fwd.endswith(UNLOADED)
    assert not fwd.startswith(UNLOADED)


# --- the whole bar -----------------------------------------------------------

def _bar(width=80, **kw):
    kw.setdefault("offset", 20)
    kw.setdefault("rows", 10)
    kw.setdefault("total", 100)
    kw.setdefault("behind", 0)
    kw.setdefault("following", False)
    return bottom_bar(width, **kw)


def test_the_bar_is_never_wider_than_the_pane():
    for width in range(20, 200):
        bar = _bar(width)
        assert len(bar.line) <= width, f"overflowed at {width}"


def test_the_track_survives_a_pane_too_narrow_for_anything_else():
    """Twenty-four columns is a real width — draw() renders panes from there —
    and the scrollbar is the one thing that must not be the casualty."""
    bar = _bar(24)
    assert bar.track[1] - bar.track[0] >= 8
    assert THUMB in bar.line


def test_a_wide_pane_gets_the_keys_back():
    assert "pgup" in _bar(160).line
    assert "pgup" not in _bar(60).line, "and a narrow one does not pretend to"


def test_the_button_is_there_when_you_are_behind():
    bar = _bar(80, following=False, behind=3)
    start, end = bar.button
    assert end > start
    assert "3 new" in bar.line[start:end]


def test_the_button_counts_nothing_when_nothing_is_new():
    """Scrolled back in a quiet session there is still somewhere to jump to —
    just nothing waiting there."""
    assert "newest" in _bar(80, following=False, behind=0).line


def test_following_the_end_there_is_no_button():
    """Nowhere to jump, so nothing to click, and the track gets the room."""
    bar = _bar(80, following=True, offset=90)
    assert bar.button == (0, 0)
    assert "⤓" not in bar.line


def test_the_percentage_says_where_you_are():
    assert "0%" in _bar(80, offset=0).line
    assert "100%" in _bar(80, offset=90).line


def test_the_rail_does_not_resize_as_you_travel():
    """A control you click has to hold still. Unpadded, the percentage runs
    0% → 10% → 100% and every digit it gains is a column the rail loses — so
    the same spot on the track meant a different place in the conversation
    depending on where you already were."""
    spans = {_bar(80, offset=o).track for o in range(0, 91, 5)}
    assert len(spans) == 1, f"the track resized while scrolling: {spans}"


def test_the_spans_point_at_what_they_say_they_do():
    """They are what a click is resolved against, so if they drift the button
    stops working and nothing else notices."""
    bar = _bar(80, behind=2)
    assert set(bar.line[bar.track[0]:bar.track[1]]) <= {RAIL, THUMB, UNLOADED}
    assert bar.line[bar.button[0]:bar.button[1]].startswith("[")
    assert bar.line[bar.button[0]:bar.button[1]].endswith("]")


# --- clicking it -------------------------------------------------------------

def _click(monkeypatch, tui, *, x, y, state=curses.BUTTON1_PRESSED):
    monkeypatch.setattr(curses, "getmouse", lambda: (0, x, y, 0, state))
    return tui.handle(curses.KEY_MOUSE)


def _drawn(view="both", height=24):
    """A Tui with the bar laid out, as draw() leaves it."""
    tui = _tui(view=view)
    tui.model.newer = 40
    tui.chat.offset, tui.chat.follow = 20, False
    tui._chat_rows = [Row(f"line {i}", seq=i + 1) for i in range(200)]
    tui.chat.total = len(tui._chat_rows)
    tui._lay_out_bar(height, 80)
    return tui


def test_clicking_the_button_goes_to_the_newest(monkeypatch):
    tui = _drawn()
    x = sum(tui._bar.button) // 2

    _click(monkeypatch, tui, x=x, y=tui._bar_y)
    assert tui.model.jumps == ["tail"], "the live end, not the end of the window"
    assert tui.chat.follow


def test_clicking_the_rail_goes_to_that_part_of_the_conversation(monkeypatch):
    tui = _drawn()

    _click(monkeypatch, tui, x=tui._bar.track[0], y=tui._bar_y)
    assert tui.chat.offset == 0, "the far left is the top"

    # The span is read again: seeking to the top made the button appear, and
    # the button takes its room out of the rail.
    _click(monkeypatch, tui, x=tui._bar.track[1] - 1, y=tui._bar_y)
    assert tui.chat.offset == max(tui.chat.total - tui.chat.rows, 0)
    assert tui.chat.follow, "and the far right is the live end"


def test_a_double_click_on_the_button_still_jumps(monkeypatch):
    """The impatient case, and the one ncurses renames out from under you."""
    state = getattr(curses, "BUTTON1_DOUBLE_CLICKED", None)
    if state is None:
        pytest.skip("this ncurses has no BUTTON1_DOUBLE_CLICKED")
    tui = _drawn()

    _click(monkeypatch, tui, x=sum(tui._bar.button) // 2, y=tui._bar_y,
           state=state)
    assert tui.model.jumps == ["tail"]


def test_a_click_in_the_same_breath_as_a_scroll_still_lands(monkeypatch):
    """The loop drains everything waiting and redraws ONCE, so the wheel notch
    and the click that follows it are handled before any repaint. Resolved
    against the previous frame the click misses: that layout was drawn while
    the pane was still following the live end, and there was no button on it.

    Measured on a real terminal before this: scroll back, click the button that
    appears, nothing happens.
    """
    tui = _drawn()
    tui.chat.follow, tui.chat.offset = True, tui.chat.total - tui.chat.rows
    tui._lay_out_bar(24, 80)
    assert tui._bar.button == (0, 0), "no button while following"

    # The wheel notch and the click, with no draw in between.
    _click(monkeypatch, tui, x=2, y=10, state=curses.BUTTON4_PRESSED)
    _click(monkeypatch, tui, x=76, y=tui._bar_y)

    assert tui.model.jumps == ["tail"], "the button that had just appeared"


def test_clicking_the_key_names_does_nothing(monkeypatch):
    tui = _drawn()
    before = tui.chat.offset
    x = tui._bar.track[1] + 1                 # just past the rail

    _click(monkeypatch, tui, x=x, y=tui._bar_y)
    assert tui.chat.offset == before
    assert tui.model.jumps == []


def test_a_click_on_the_bar_never_folds_a_message(monkeypatch):
    """The bar is not the conversation, and the row arithmetic would happily
    resolve it to one."""
    tui = _drawn()
    tui._chat_rows[30] = Row("▸ show more", seq=31, button=True)

    _click(monkeypatch, tui, x=tui._bar.track[0], y=tui._bar_y)
    assert tui.expanded == set()


def test_the_wheel_over_the_bar_still_scrolls(monkeypatch):
    """The bar is one row at the bottom of the conversation; a notch there is
    not a click and should not be swallowed by it."""
    tui = _drawn()
    before = tui.chat.offset

    _click(monkeypatch, tui, x=2, y=tui._bar_y, state=curses.BUTTON5_PRESSED)
    assert tui.chat.offset > before


def test_the_button_cannot_be_clicked_when_it_is_not_there(monkeypatch):
    tui = _drawn()
    tui.chat.follow = True
    tui._lay_out_bar(24, 80)

    assert tui._bar.button == (0, 0)
    _click(monkeypatch, tui, x=78, y=tui._bar_y)
    assert tui.model.jumps == []


@pytest.mark.parametrize("key", [ord("G"), curses.KEY_END])
def test_the_key_and_the_button_do_the_same_thing(key, monkeypatch):
    """One implementation, so they cannot drift apart."""
    by_key = _drawn()
    by_key.handle(key)

    by_click = _drawn()
    _click(monkeypatch, by_click, x=sum(by_click._bar.button) // 2,
           y=by_click._bar_y)

    assert by_key.model.jumps == by_click.model.jumps == ["tail"]
    assert by_key.chat.offset == by_click.chat.offset
    assert by_key.chat.follow == by_click.chat.follow


def test_a_single_pane_view_has_a_bar_too():
    tui = _drawn(view="chat")
    assert tui._bar.track[1] > tui._bar.track[0]
