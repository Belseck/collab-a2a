"""The password from the outside: the flag, the prompt, and the whole round trip.

The hub's half is covered in `test_password_join.py`. What is checked here is
everything a person actually touches — that a bare URL plus `--password` is a
complete way in, that the secret is asked for rather than typed into a command
line, and that nothing anywhere prints it back.
"""

from __future__ import annotations

import getpass
import sys

import pytest

from collab import cli
from collab.client import onboard
from collab.client.hub_client import HubClient, HubError
from collab.password import MIN_ITERATIONS, PasswordError, new_record, verify_proof
from collab.server.session import (create_session, join_line, password_join_line,
                                   resume_session)
from collab.server.store import Store

PASSWORD = "correct horse battery staple"
FAST = MIN_ITERATIONS  # see tests/test_session_password.py


@pytest.fixture()
def guest_home(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "guest-home"))
    monkeypatch.setenv("COLLAB_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    return tmp_path


class _Terminal:
    """Somewhere to be asked. Only `isatty` is ever consulted."""

    def isatty(self) -> bool:
        return True


# --- the round trip ----------------------------------------------------------

def test_a_bare_url_and_a_password_are_a_complete_way_in(live_server, guest_home):
    """No invite code anywhere: the link is an address, the secret travels apart."""
    live_server["store"].set_password(new_record(PASSWORD, iterations=FAST))

    profile, snapshot, _ = onboard.join_session(
        live_server["base"], name="bob", start_daemon=False, password=PASSWORD)

    assert profile.token
    assert profile.name == "bob"
    assert profile.url == live_server["base"]
    assert snapshot["participants"]


def test_the_wrong_password_is_refused_in_words(live_server, guest_home):
    live_server["store"].set_password(new_record(PASSWORD, iterations=FAST))
    with pytest.raises(HubError) as exc:
        onboard.join_session(live_server["base"], name="bob",
                             start_daemon=False, password="not it at all")
    assert "wrong password" in str(exc.value)


def test_a_password_offered_where_none_was_set_says_so(live_server, guest_home):
    """The joiner was given a password the host never configured. Say that.

    In those words: a 404 with a route name on the front of it is not an answer
    to «I was given a password», and the thing to do — ask for the link — is
    not something the status code says.
    """
    with pytest.raises(HubError) as exc:
        onboard.join_session(live_server["base"], name="bob",
                             start_daemon=False, password=PASSWORD)
    assert str(exc.value) == ("this session has no password — ask the host for "
                              "the join link, which carries an invite code")


def test_being_rate_limited_reads_as_a_sentence(live_server, guest_home):
    """The limit is hit by people, so what it says is read by people."""
    live_server["store"].set_password(new_record(PASSWORD, iterations=FAST))
    for _ in range(5):
        with pytest.raises(HubError):
            onboard.join_session(live_server["base"], name="bob",
                                 start_daemon=False, password="wrong one")
    with pytest.raises(HubError) as exc:
        onboard.join_session(live_server["base"], name="bob",
                             start_daemon=False, password=PASSWORD)
    assert str(exc.value) == "too many failed attempts, wait a minute and try again"
    assert "/ext/" not in str(exc.value)


def test_the_invite_still_works_when_a_password_exists(live_server, guest_home):
    live_server["store"].set_password(new_record(PASSWORD, iterations=FAST))
    url = f"{live_server['base']}#{live_server['invite']}"
    profile, _, _ = onboard.join_session(url, name="carol", start_daemon=False)
    assert profile.token


def test_the_password_never_appears_in_what_is_sent(live_server, guest_home, monkeypatch):
    """The claim the whole design rests on, checked at the wire."""
    live_server["store"].set_password(new_record(PASSWORD, iterations=FAST))
    sent: list[str] = []
    original = HubClient._request

    def record(self, method, path, **kw):
        sent.append(repr(kw.get("json")))
        return original(self, method, path, **kw)

    monkeypatch.setattr(HubClient, "_request", record)
    onboard.join_session(live_server["base"], name="bob", start_daemon=False,
                         password=PASSWORD)

    assert sent, "nothing was sent, so nothing was proved"
    for body in sent:
        assert PASSWORD not in body
        for word in PASSWORD.split():
            assert word not in body


# --- the URL -----------------------------------------------------------------

def test_a_bare_url_without_a_password_is_still_an_error():
    with pytest.raises(ValueError) as exc:
        onboard.split_join_url("https://example.test")
    assert "--password" in str(exc.value), "the way out should be in the message"


def test_a_bare_url_is_accepted_when_a_password_is_held():
    base, invite = onboard.split_join_url("https://example.test",
                                          invite_required=False)
    assert (base, invite) == ("https://example.test", "")


# --- what the host is told to share -------------------------------------------

def test_the_password_share_line_carries_no_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    cfg = create_session("alice", 9101, password=PASSWORD)

    line = password_join_line(cfg)
    assert line.endswith("--password")
    assert cfg.invite not in line
    assert PASSWORD not in line
    # And the invite line is unchanged: both ways in, both printed.
    assert cfg.invite in join_line(cfg)


def test_a_session_without_a_password_offers_no_such_line(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    cfg = create_session("alice", 9102)
    assert cfg.has_password is False
    assert password_join_line(cfg) == ""


def test_the_password_is_not_written_to_the_session_file(tmp_path, monkeypatch):
    """`hub.json` holds the invite and the host token; it must never hold this."""
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    cfg = create_session("alice", 9103, password=PASSWORD)
    assert PASSWORD not in (cfg.dir / "hub.json").read_text()
    assert cfg.has_password is True


def test_a_password_too_weak_to_be_a_door_never_creates_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    with pytest.raises(PasswordError):
        create_session("alice", 9104, password="short")


# --- resuming -----------------------------------------------------------------

def test_resuming_keeps_the_password_and_retires_the_invite(tmp_path, monkeypatch):
    """A link travels and gets forwarded; a password is handed over deliberately.

    Retiring it on resume would lock the host out of their own arrangement —
    what the hub keeps verifies a password, it cannot hand one back.
    """
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    cfg = create_session("alice", 9105, password=PASSWORD)
    old_invite = cfg.invite

    cfg = resume_session(cfg, 9106)
    assert cfg.invite != old_invite
    assert cfg.has_password is True

    store = Store(cfg.db_path)
    record = store.password_record()
    store.close()
    from collab.password import client_proof

    assert verify_proof(record, nonce="n", proof=client_proof(
        PASSWORD, salt=record.salt, iterations=record.iterations, nonce="n"))


def test_resuming_with_a_new_password_replaces_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    cfg = create_session("alice", 9107, password=PASSWORD)
    cfg = resume_session(cfg, 9108, password="a different long secret")

    store = Store(cfg.db_path)
    record = store.password_record()
    store.close()
    from collab.password import client_proof

    assert not verify_proof(record, nonce="n", proof=client_proof(
        PASSWORD, salt=record.salt, iterations=record.iterations, nonce="n"))


def test_a_session_that_never_had_one_does_not_grow_a_password_on_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    cfg = resume_session(create_session("alice", 9109), 9110)
    assert cfg.has_password is False


# --- the flag and the prompt ---------------------------------------------------

def test_no_flag_means_no_password():
    assert cli._password_arg("", confirm=False) == ""


def test_a_value_is_taken_as_given():
    assert cli._password_arg("hunter22222", confirm=False) == "hunter22222"


def test_the_bare_flag_asks_instead_of_reading_the_command_line(monkeypatch):
    """The form the docs lead with: nothing lands in shell history."""
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": PASSWORD)
    assert cli._password_arg(cli.ASK_FOR_PASSWORD, confirm=False) == PASSWORD


def test_setting_one_asks_twice(monkeypatch):
    """It cannot be read back, so a typo would be found by the other person."""
    asked = []

    def answer(prompt=""):
        asked.append(prompt)
        return PASSWORD

    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(getpass, "getpass", answer)
    assert cli._password_arg(cli.ASK_FOR_PASSWORD, confirm=True) == PASSWORD
    assert len(asked) == 2


def test_two_that_do_not_match_are_refused(monkeypatch):
    answers = iter([PASSWORD, "something else"])
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(PasswordError):
        cli._password_arg(cli.ASK_FOR_PASSWORD, confirm=True)


def test_with_nowhere_to_ask_it_says_what_to_do_instead(monkeypatch):
    class NotATerminal:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdin", NotATerminal())
    with pytest.raises(PasswordError) as exc:
        cli._password_arg(cli.ASK_FOR_PASSWORD, confirm=False)
    assert "--password" in str(exc.value)


# --- the flags reach the thing that uses them ----------------------------------

def test_join_hands_the_password_to_the_join(monkeypatch, tmp_path):
    parser = cli.build_parser()
    args = parser.parse_args(["join", "https://example.test", "--password", PASSWORD])
    assert args.password == PASSWORD

    seen = {}

    def joined(url, **kwargs):
        seen.update(kwargs, url=url)
        raise ValueError("stop here — the arguments are what is under test")

    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli.onboard, "join_session", joined)
    monkeypatch.setattr(cli, "_own_state_dir", lambda *a, **k: None)
    args.agent = ""
    cli.cmd_join(args)
    assert seen["password"] == PASSWORD
    assert seen["url"] == "https://example.test"


# --- a refusal is not an unreachable address -----------------------------------

def _run_join(monkeypatch, tmp_path, capsys, exc):
    """`collab join <url>` where the join raises `exc`. Returns what was printed."""
    from types import SimpleNamespace

    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "_own_state_dir", lambda *a, **k: None)
    monkeypatch.setattr(cli.onboard, "join_session",
                        lambda url, **kw: (_ for _ in ()).throw(exc))
    # Something else IS running here, which is what the misleading branch keys off.
    monkeypatch.setattr(cli.peers, "discover", lambda *a, **k: [SimpleNamespace(
        joinable=True, session_id="s_local", name="alice", repo=str(tmp_path))])

    args = cli.build_parser().parse_args(["join", "https://example.test#CODE"])
    args.agent = ""
    assert cli.cmd_join(args) == 1
    printed = capsys.readouterr()
    return printed.out + printed.err  # `fail` writes to stderr, the hints to stdout


def test_a_hub_that_refused_us_is_not_reported_as_a_stale_link(
        monkeypatch, tmp_path, capsys):
    """A wrong password is an answer FROM the session the link names.

    The advice below that branch is about a link that reached nothing, and
    printing it here sends somebody to check an address that was never the
    problem. Before the session password this was rare — a stale link, an
    expired invite; now it is the ordinary way a join fails.
    """
    out = _run_join(monkeypatch, tmp_path, capsys,
                    HubError("wrong password", status=401))
    assert "wrong password" in out
    assert "not one of them" not in out
    assert "s_local" not in out


def test_a_link_that_reached_nothing_still_says_what_is_running_here(
        monkeypatch, tmp_path, capsys):
    """The canary: the branch above must still fire when it is actually right."""
    out = _run_join(monkeypatch, tmp_path, capsys,
                    HubError("cannot reach the hub at https://example.test"))
    assert "not one of them" in out
    assert "s_local" in out


def test_the_bare_flag_parses_on_both_commands():
    parser = cli.build_parser()
    assert parser.parse_args(["host", "--password"]).password == cli.ASK_FOR_PASSWORD
    assert parser.parse_args(["join", "u", "--password"]).password == cli.ASK_FOR_PASSWORD


def test_a_flagless_host_is_unchanged():
    assert cli.build_parser().parse_args(["host"]).password == ""
