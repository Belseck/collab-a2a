"""The date beside a message and the time beside it are the same instant.

The wire carries UTC. The reader is not in UTC. When the clock was converted
and the date was not, a message sent at 21:30 on the 1st in Bogota was headed
«today» — because 02:30 UTC on the 2nd is today in UTC. The two halves of one
stamp disagreed about which day they were describing.

The zone both halves are read in is the computer's own, unless
`collab config timezone` pins one — which is the case a laptop that travels,
or an agent on a server in another country, actually needs.
"""

from __future__ import annotations

import datetime as _dt
import os
import time

import pytest

from collab import config
from collab.client import tui
from collab.protocol import local_clock, local_datetime, local_today


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A config of this test's own, and the machine's zone back afterwards."""
    monkeypatch.setenv("COLLAB_CONFIG", str(tmp_path / "config.json"))
    config._CACHE.clear()
    yield
    config._CACHE.clear()
    os.environ.pop("TZ", None)
    time.tzset()


@pytest.fixture
def machine_zone(monkeypatch):
    """Point the computer itself at a zone, as `TZ` does for any process."""

    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    return use


def _wire(when: _dt.datetime) -> str:
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the two halves of a stamp agree -----------------------------------------

def test_the_date_belongs_to_the_same_day_as_the_time(machine_zone):
    # 02:30 UTC on the 2nd is 21:30 on the 1st in Bogota.
    machine_zone("America/Bogota")
    ts = "2026-09-02T02:30:00Z"
    assert local_clock(ts) == "21:30"
    assert tui._day(ts) == "2026-09-01"


def test_the_date_belongs_to_the_same_day_ahead_of_utc(machine_zone):
    # 22:00 UTC on the 1st is 07:00 on the 2nd in Tokyo.
    machine_zone("Asia/Tokyo")
    ts = "2026-09-01T22:00:00Z"
    assert local_clock(ts) == "07:00"
    assert tui._day(ts) == "2026-09-02"


def test_last_night_is_dated_even_where_utc_calls_it_today(machine_zone):
    """The bug as it was reported: yesterday evening, west of UTC."""
    machine_zone("America/Bogota")
    yesterday = _dt.datetime.now().astimezone() - _dt.timedelta(days=1)
    evening = yesterday.replace(hour=21, minute=30, second=0, microsecond=0)
    ts = _wire(evening)
    assert tui._stamp(ts) == f"{evening.day} {tui.MONTHS[evening.month - 1]} 21:30"
    assert tui._day_label(ts) == "yesterday"


def test_todays_message_carries_no_date(machine_zone):
    machine_zone("America/Bogota")
    ts = _wire(_dt.datetime.now(_dt.timezone.utc))
    assert tui._stamp(ts) == local_clock(ts)
    assert tui._day_label(ts) == "today"


def test_a_stamp_we_cannot_read_degrades_instead_of_crashing():
    assert local_datetime("") is None
    assert local_datetime("not a stamp") is None
    assert tui._day("not a stamp") == "not a stam"
    assert tui._stamp("not a stamp") == local_clock("not a stamp")


# --- the configured zone -----------------------------------------------------

def test_without_a_setting_it_is_the_computers_own_zone(machine_zone):
    machine_zone("Asia/Tokyo")
    assert config.timezone_name() == config.TIMEZONE_AUTO
    assert config.reading_timezone() is None
    assert local_clock("2026-09-01T22:00:00Z") == "07:00"


def test_a_configured_zone_wins_over_the_computers(machine_zone):
    machine_zone("Asia/Tokyo")
    config.set_timezone("America/Bogota")
    ts = "2026-09-02T02:30:00Z"
    assert local_clock(ts) == "21:30"
    assert tui._day(ts) == "2026-09-01"


def test_the_configured_zone_moves_both_halves_together(machine_zone):
    """Not the clock alone: the day the message belongs to moves with it."""
    machine_zone("UTC")
    ts = "2026-09-01T22:00:00Z"
    config.set_timezone("Asia/Tokyo")
    assert (local_clock(ts), tui._day(ts)) == ("07:00", "2026-09-02")
    config.set_timezone("America/Bogota")
    assert (local_clock(ts), tui._day(ts)) == ("17:00", "2026-09-01")


def test_today_is_today_in_the_configured_zone(machine_zone):
    machine_zone("UTC")
    config.set_timezone("Pacific/Kiritimati")  # UTC+14, ahead of everyone
    assert local_today() == _dt.datetime.now(
        config.reading_timezone()).date()


def test_a_message_from_now_reads_as_today_in_any_configured_zone(machine_zone):
    """The pair that has to hold everywhere: now is today, wherever you read."""
    machine_zone("UTC")
    for zone in ("Pacific/Kiritimati", "Pacific/Midway", "Asia/Kolkata",
                 "America/Bogota", "UTC"):
        config.set_timezone(zone)
        ts = _wire(_dt.datetime.now(_dt.timezone.utc))
        assert tui._day_label(ts) == "today", zone
        assert tui._stamp(ts) == local_clock(ts), zone


def test_auto_puts_the_computer_back_in_charge(machine_zone):
    machine_zone("Asia/Tokyo")
    config.set_timezone("America/Bogota")
    assert config.set_timezone("auto") == config.TIMEZONE_AUTO
    assert config.timezone_name() == config.TIMEZONE_AUTO
    assert local_clock("2026-09-01T22:00:00Z") == "07:00"


def test_clearing_a_zone_that_was_never_set_is_not_an_error():
    assert config.set_timezone("") == config.TIMEZONE_AUTO
    assert config.timezone_name() == config.TIMEZONE_AUTO


def test_a_zone_nobody_can_resolve_is_refused_rather_than_stored():
    with pytest.raises(ValueError):
        config.set_timezone("Mars/Olympus_Mons")
    assert config.timezone_name() == config.TIMEZONE_AUTO


def test_a_stored_zone_that_stops_resolving_falls_back_to_the_computer(
        machine_zone, monkeypatch):
    """Nobody is watching at render time, so it degrades instead of raising."""
    machine_zone("Asia/Tokyo")
    config.set_timezone("America/Bogota")
    monkeypatch.setattr(config, "_zone", lambda name: None)
    assert config.reading_timezone() is None
    assert local_clock("2026-09-01T22:00:00Z") == "07:00"


def test_the_setting_is_reachable_from_collab_config():
    item = config.setting("timezone")
    assert item is not None
    assert item.default == config.TIMEZONE_AUTO
    item.write(item.parse("Europe/Madrid"))
    assert item.read() == "Europe/Madrid"
