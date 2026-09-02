"""The blue of an information line.

It used to be `curses.COLOR_BLUE`, which is not a colour but a slot: whatever
the terminal's palette put there, and on the usual dark ground that is navy —
the commands and figures the line is made of came out darker than the frame
around them. It is a hex now, and the fallbacks matter as much as the colour:
a terminal that cannot redefine one must still get a readable blue, and one
with eight colours must not lose the viewer over the attempt.
"""
from __future__ import annotations

import curses

import pytest

from collab.client import tui
from collab.config import hex_to_rgb, rgb_to_256


def test_the_hex_is_the_one_that_was_asked_for():
    assert tui.INFO_HEX == "#4888db"


def test_it_is_a_blue_the_eye_can_read_on_black():
    """Not a test of taste: of contrast. Navy on black is the thing being
    fixed, so the colour has to be well clear of it."""
    r, g, b = hex_to_rgb(tui.INFO_HEX)
    assert b > r and b > g, "still a blue"
    # Rec. 709 luminance. COLOR_BLUE as most terminals ship it (#0000ee)
    # comes to 15; below about 60 the text reads as a shadow.
    assert 0.2126 * r + 0.7152 * g + 0.0722 * b > 60


def test_a_terminal_that_cannot_redefine_colours_gets_the_nearest_of_256(
        monkeypatch):
    monkeypatch.setattr(curses, "can_change_color", lambda: False)
    monkeypatch.setattr(curses, "COLORS", 256, raising=False)
    assert tui._colour_index(tui.INFO_HEX) == rgb_to_256(*hex_to_rgb(tui.INFO_HEX))


def test_a_colour_the_terminal_refuses_falls_back_instead_of_dying(monkeypatch):
    """Eight colours and an index of 68: `init_pair` raises, and before the
    fallback existed that took the whole viewer down over a shade of blue."""
    asked: list[tuple[int, int]] = []

    def init_pair(pair, colour, _bg):
        if colour > 7:
            raise curses.error("color out of range")
        asked.append((pair, colour))

    monkeypatch.setattr(curses, "init_pair", init_pair)
    tui._pair_or(tui.C_INFO, 68, curses.COLOR_BLUE)
    assert asked == [(tui.C_INFO, curses.COLOR_BLUE)]


def test_the_pair_is_built_from_the_hex_and_not_from_the_slot(monkeypatch):
    """The regression this guards: `init_pair(C_INFO, curses.COLOR_BLUE, -1)`
    is one word away and looks perfectly reasonable."""
    monkeypatch.setattr(tui, "_colour_index", lambda value: 68)
    seen: dict[int, int] = {}
    monkeypatch.setattr(curses, "init_pair",
                        lambda pair, colour, _bg: seen.__setitem__(pair, colour))
    monkeypatch.setattr(curses, "start_color", lambda: None)
    monkeypatch.setattr(curses, "use_default_colors", lambda: None)
    monkeypatch.setattr(curses, "COLORS", 256, raising=False)

    tui._init_colors()
    assert seen[tui.C_INFO] == 68
