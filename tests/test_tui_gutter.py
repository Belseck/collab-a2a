"""The vertical scrollbar down the side of a pane.

Not tmux's. `pane-scrollbars` arrived in tmux 3.6 and measures TMUX'S
scrollback — and the viewer is a curses app, so it runs on the alternate
screen, where tmux records no history at all and the thumb comes out
permanently full. Even working it would be answering a different question: the
conversation is a window of messages over a log on disk, and tmux has seen
neither. So the bar is drawn here, by the only process that knows where the
reader is.
"""

from __future__ import annotations

import curses

import pytest

from collab.client.tui import (V_RAIL, V_THUMB, V_UNLOADED, Gutter, Pane, Row,
                               Tui, scroll_track)

from test_tui_scroll import _tui


def _theme(monkeypatch, **declared):
    """A resolved theme with `declared` on top of the shipped defaults.

    Through the same door the viewer uses, so a test cannot pass by reading a
    setting the renderer never consults.
    """
    from collab import themes
    from collab.client import tui as _t

    resolved = dict(themes.DEFAULTS) | declared
    monkeypatch.setattr(_t, "_current_theme", lambda: resolved)
    return resolved


# --- the strokes -------------------------------------------------------------

def test_the_column_uses_the_vertical_strokes():
    cells = scroll_track(10, offset=0, rows=5, total=50,
                         glyphs=(V_RAIL, V_THUMB, V_UNLOADED))
    assert set(cells) <= {V_RAIL, V_THUMB}
    assert cells.startswith(V_THUMB), "at the top"


def test_the_arithmetic_does_not_care_which_way_it_is_drawn():
    """The strokes are an argument, not a branch. There is only one axis drawn
    today — the bottom row belongs to the status bar — but the glyphs stayed a
    parameter, and this says the maths underneath them is the same cells
    whatever they are. A second copy of it would be a second place for the
    thumb to end up somewhere the reader is not."""
    down = scroll_track(20, offset=40, rows=10, total=100)
    across = scroll_track(20, offset=40, rows=10, total=100,
                          glyphs=("━", "█", "┄"))
    assert [c != V_RAIL for c in down] == [c != "━" for c in across]


# --- when it is there at all -------------------------------------------------

def test_a_pane_whose_content_fits_gets_no_bar_and_keeps_its_column():
    tui = _tui()
    tui.roster.rows, tui.roster.total = 20, 6
    assert tui._gutter_width(tui.roster) == 0


def test_a_pane_with_more_than_fits_gets_one():
    tui = _tui()
    tui.roster.rows, tui.roster.total = 6, 40
    assert tui._gutter_width(tui.roster) == 1


def test_an_unmeasured_pane_gets_none():
    """Before the first frame a pane has no height, and `total > 0` alone would
    put a bar on every pane in the window for one frame."""
    tui = _tui()
    tui.roster.rows, tui.roster.total = 0, 40
    assert tui._gutter_width(tui.roster) == 0


def test_off_takes_the_column_from_a_pane_that_would_have_one(monkeypatch):
    """The reader is allowed not to want it, and nothing else moves: every key
    that scrolls still scrolls, and the status row says what it always said."""
    tui = _tui()
    tui.roster.rows, tui.roster.total = 6, 40
    _theme(monkeypatch, scrollbar_side="off")
    assert tui._gutter_width(tui.roster) == 0


def test_always_keeps_the_column_on_a_pane_that_fits(monkeypatch):
    """A rail with the thumb filling it, saying «this is all of it» — the
    reading `auto` will not spend a column on and this one will."""
    tui = _tui()
    tui.roster.rows, tui.roster.total = 20, 6
    _theme(monkeypatch, scrollbar_side="always")
    assert tui._gutter_width(tui.roster) == 1


def test_always_still_gives_nothing_to_a_pane_with_no_height(monkeypatch):
    """Before the first frame there is no pane to draw beside. «Always» is a
    matter of taste, not a licence to paint into a pane that is not there."""
    tui = _tui()
    tui.roster.rows, tui.roster.total = 0, 40
    _theme(monkeypatch, scrollbar_side="always")
    assert tui._gutter_width(tui.roster) == 0


def test_the_shipped_default_is_what_the_viewer_already_did(monkeypatch):
    """`auto`, deliberately: naming an existing behaviour is not changing it.

    If this fails, someone made every pane in every default install one column
    narrower — which is a decision, and should be made on purpose.
    """
    from collab import themes

    assert themes.DEFAULTS["scrollbar_side"] == "auto"
    tui = _tui()
    _theme(monkeypatch)                        # nothing declared
    tui.roster.rows, tui.roster.total = 6, 40
    assert tui._gutter_width(tui.roster) == 1
    tui.roster.rows, tui.roster.total = 20, 6
    assert tui._gutter_width(tui.roster) == 0


def test_taking_the_column_away_cannot_flap():
    """The width the bar costs makes text wrap into MORE rows, never fewer, so
    a pane that needs the bar still needs it once it has it. If that were not
    true the bar would appear and vanish on alternate frames for ever."""
    tui = _tui()
    tui.chat.rows, tui.chat.total = 10, 11
    assert tui._gutter_width(tui.chat) == 1

    tui.chat.total = 13                    # narrower ⇒ more rows
    assert tui._gutter_width(tui.chat) == 1


# --- clicking it -------------------------------------------------------------

def _click(monkeypatch, tui, *, x, y, state=curses.BUTTON1_PRESSED):
    monkeypatch.setattr(curses, "getmouse", lambda: (0, x, y, 0, state))
    return tui.handle(curses.KEY_MOUSE)


def _with_gutters(chat_top=14):
    """A Tui laid out as draw() leaves it, with a bar on each pane."""
    tui = _tui(chat_top=chat_top)
    tui.roster.rows, tui.roster.total, tui.roster.offset = 6, 40, 0
    tui.chat.rows, tui.chat.total, tui.chat.offset = 20, 200, 0
    tui.chat.follow = False
    tui._chat_rows = [Row(f"line {i}", seq=i + 1) for i in range(200)]
    tui._gutters = [Gutter(x=79, top=3, rows=6, pane=tui.roster),
                    Gutter(x=79, top=chat_top, rows=20, pane=tui.chat)]
    tui._jump_y = 39
    return tui


def test_clicking_a_gutter_moves_that_pane(monkeypatch):
    tui = _with_gutters()

    _click(monkeypatch, tui, x=79, y=3 + 5)          # the bottom of the roster's
    assert tui.roster.offset == max(tui.roster.total - tui.roster.rows, 0)
    assert tui.chat.offset == 0, "and only that pane"


def test_the_top_of_a_gutter_is_the_top_of_the_pane(monkeypatch):
    tui = _with_gutters()
    tui.chat.offset = 100

    _click(monkeypatch, tui, x=79, y=14)
    assert tui.chat.offset == 0


def test_clicking_a_gutter_takes_the_focus(monkeypatch):
    """Aiming at a pane's scrollbar says which pane you care about, the same
    way turning the wheel over it does."""
    tui = _with_gutters()
    assert tui.focus == "chat"

    _click(monkeypatch, tui, x=79, y=4)
    assert tui.focus == "roster"


def test_the_roster_never_starts_following(monkeypatch):
    """Only the conversation tails. A roster that followed would jump to
    whoever joined last, under the eyes of somebody reading it."""
    tui = _with_gutters()

    _click(monkeypatch, tui, x=79, y=3 + 5)          # its far bottom
    assert not tui.roster.follow


def test_the_conversation_resumes_following_at_the_bottom(monkeypatch):
    tui = _with_gutters()

    _click(monkeypatch, tui, x=79, y=14 + 19)
    assert tui.chat.follow


def test_a_click_beside_the_gutter_is_not_a_click_on_it(monkeypatch):
    """One column wide, and it has to stay one column: the text next to it is
    a message somebody is reading, not a control."""
    tui = _with_gutters()

    _click(monkeypatch, tui, x=78, y=14 + 5)
    assert tui.chat.offset == 0
    assert tui.roster.offset == 0


def test_a_click_in_the_gap_between_two_gutters_hits_neither(monkeypatch):
    tui = _with_gutters()

    _click(monkeypatch, tui, x=79, y=11)             # below the roster's, above the chat's
    assert tui.chat.offset == 0 and tui.roster.offset == 0


def test_the_gutter_wins_over_folding(monkeypatch):
    """They overlap: the bar is painted on a row that also carries a message,
    and resolving the click as a fold would unfold whatever it happened to
    land on instead of scrolling."""
    tui = _with_gutters()
    tui._chat_rows[5] = Row("▸ show more", seq=6, button=True)

    _click(monkeypatch, tui, x=79, y=14 + 5)
    assert tui.expanded == set(), "not folded"
    assert tui.chat.offset > 0, "scrolled"


def test_a_stale_gutter_is_not_a_click_target(monkeypatch):
    """They are forgotten with the frame they were painted in. Left behind from
    a pane that no longer has one, the column is live text."""
    tui = _with_gutters()
    tui._gutters = []

    _click(monkeypatch, tui, x=79, y=14 + 5)
    assert tui.chat.offset == 0


# --- the record itself -------------------------------------------------------

@pytest.mark.parametrize("y,expected", [(10, 0.0), (15, 0.5), (20, 1.0)])
def test_the_fraction_runs_end_to_end(y, expected):
    g = Gutter(x=5, top=10, rows=11, pane=Pane())
    assert g.fraction(y) == pytest.approx(expected)


def test_a_single_row_gutter_does_not_divide_by_zero():
    g = Gutter(x=5, top=10, rows=1, pane=Pane())
    assert g.fraction(10) == 0.0
