# The host assigns the colours

**Status:** design, approved. Not implemented.
**Date:** 2026-09-02

## The premise

Everyone in a session should see the same colour for the same participant.

That is not true today, and it is not true by accident. This design makes the
host the one authority on who is what colour, and has the answer travel with
the roster so that every viewer paints from it rather than from a private
guess.

## What is true today

Two halves, and only one of them works.

### The half that works: a declared colour travels

A participant who has chosen a colour already gets it shown everywhere.

- `collab color` writes it into the agent's identity and reports it with
  `report_stats({}, identity={"color": ...})`.
- `app.py` accepts it into the participant's `meta`, alongside `machine`,
  `machine_id` and `user`.
- `hub.py` flattens `meta["color"]` into the roster's `color` field — the
  roster is what every client reads.
- `record_colours()` in the viewer reads that field on every refresh, so a
  change made in one terminal reaches panes that are already open.

There is nothing to build here. The transport exists end to end.

### The half that does not exist: nobody assigns

A participant who has **not** chosen one is dealt a colour by each viewer,
separately:

```python
_ORDER = list(range(len(SPEAKER_COLORS)))
random.shuffle(_ORDER)                                  # once per process
_SLOTS[name] = C_SPEAKER_BASE + _ORDER[len(_SLOTS) % len(_ORDER)]
```

`_dealt_slot()` deals in **arrival order over a permutation shuffled at
startup**. The consequences are exactly what the premise forbids:

- two people watching the same session see different colours for the same
  third participant;
- the same person reopening the viewer sees a different set than before;
- the palette differs by terminal — twelve colours at 256, six at 8.

The docstring says so plainly: *"It changes between runs, because the order is
shuffled at startup."* This was a deliberate improvement over a name hash,
which collided. It is not a bug. It is simply local, and the premise needs it
to be shared.

## Decisions

| Question | Decision |
|---|---|
| Who gets a colour assigned? | Everyone who arrives without one — people and agents alike. |
| Does it survive a reconnect? | Yes, for the life of the session, across a host restart. |
| What does the host publish? | A hex, in the `color` field that already exists. No protocol change. |
| Does the host colour itself? | Yes — at session start, and only if it has none already. |

## Design

### 1. Two keys in `meta`, never one

| key | written by | meaning |
|---|---|---|
| `color` | the participant, via `collab color` | what they chose |
| `assigned_color` | the host | what it gave them for having none |

`hub.py` resolves the roster's single `color` field as:

```python
"color": p.meta.get("color") or p.meta.get("assigned_color", "")
```

One field out, so no viewer and no older hub needs to learn anything.

**They cannot share a key.** `collab color none` sends `color: ""`, and the
meta-reporting loop reads an empty value as "clear it" and pops the key. If the
host wrote its assignment there, anyone clearing their own colour would delete
the host's too — and then, having no colour at all, would fall back to the very
local dealing this design exists to remove.

A declared colour always wins. The host only ever fills a gap; it never
overwrites a choice, and a `collab color` run later displaces the assigned one
without erasing it.

### 2. Where the assignment happens

Two call sites, because participants arrive two ways.

**A guest joins** — `app.py`, where `add_participant(..., is_host=False,
meta=hello)` runs. The `hello` payload is where a declared colour would be. If
it carries none, the host picks one and stores it as `assigned_color` before
the roster is next published.

**The host is created** — `session.py`, where
`add_participant(cfg.host_name, cfg.host_token, is_host=True)` runs. Note that
this call passes **no `meta` at all**: today the host starts with no colour
recorded anywhere. It is assigned one at that moment, under the same rule and
from the same picker.

This closes a hole that would otherwise be visible precisely to the person who
set the session up: the host does not "join", so a design that only colours
arrivals leaves the host as the one participant every screen paints
differently.

**Only if there is not one already.** The check is on `assigned_color` being
absent, not on the participant being new. A host restarting an existing session
finds its own row and keeps the colour it had; re-rolling on every restart
would repaint the whole room each time the hub came back.

### 3. Choosing one

The host walks its palette and takes the first free colour, where "free" counts
**every colour already in the session** — both the `color` values participants
brought with them and the `assigned_color` values the host handed out. Counting
only its own assignments would let it give somebody the exact colour a
neighbour had chosen.

Selection is random among the free ones rather than sequential, so two sessions
started from the same state do not produce the same order.

If the palette is exhausted, it starts again. With twelve colours it takes a
thirteenth participant to see a repeat.

Revoked participants do not hold a colour: their rows are excluded from the
"taken" set, so a colour returns to the pool when somebody is removed.

### 4. Persistence is already built

Nothing new to store, and no expiry rule to invent.

`meta` is a JSON column on `participants` in `hub.db`, written through
`store.update_meta()`. The file is on disk, so it outlives the hub process. And
`participants.name` is UNIQUE, so somebody rejoining under the same name lands
on the same row and finds their colour where they left it.

That satisfies "sticky for the life of the session, across a host restart"
without a line of storage code.

### 5. The eight-colour terminal, which is not a detail

**Without this section, terminals with eight colours lose colour entirely.**

Trace what happens to a host-assigned hex on such a terminal today:

1. `record_colours()` parses it and records it in `_CHOSEN`.
2. `_colour_index()` converts it to the nearest of the **256**, because
   `curses.can_change_color()` is false and there are fewer than 256 colours.
3. `_pair_for()` hands that index — typically well above 7 — to
   `curses.init_pair`, which raises.
4. The exception is caught and `C_TEXT` is returned. **White.**

So every assigned colour would render white, and every participant would look
identical. This is worse than the local dealing being replaced, which at least
produced six distinct colours.

The fix is a mapping to the nearest of the terminal's six speaker colours by
RGB distance, applied when the terminal cannot serve the hex. It is
deterministic, so two eight-colour terminals agree with each other; they differ
from a 256-colour terminal only in precision, not in who is distinguishable
from whom.

### 6. `$SPEAKER` keeps a meaning

Themes document two variables:

- `$DEFAULT_COLOR` — the person's own colour if they chose one, else the one
  they were dealt.
- `$SPEAKER` — the dealt colour, ignoring their choice.

Once the host publishes a colour for everyone, there is no dealing left to
ignore, and `$SPEAKER` becomes a synonym for `$DEFAULT_COLOR`. A theme could
then no longer paint a frame differently from the text it surrounds, which both
shipped themes rely on.

They are redefined so the distinction survives:

- `$SPEAKER` — the colour the host assigned.
- `$DEFAULT_COLOR` — the chosen one if there is one, else the assigned one.

Neither theme file changes appearance: `chat.md` uses `frame: $DEFAULT_COLOR`
and a generated `classic` uses `frame: $SPEAKER`, and they still differ for
anyone who has chosen a colour of their own.

## Data flow, end to end

```
collab host
  └─ session.py  add_participant(host, is_host=True)
       └─ no assigned_color yet  →  pick a free colour  →  meta.assigned_color

collab join                        (guest carries a colour)
  └─ app.py  add_participant(..., meta=hello)
       └─ hello.color present     →  stored as meta.color, nothing assigned

collab join                        (guest carries none)
  └─ app.py  add_participant(..., meta=hello)
       └─ hello.color absent      →  pick a free colour  →  meta.assigned_color

roster published
  └─ hub.py   "color": meta.color or meta.assigned_color

every viewer
  └─ record_colours()  →  _CHOSEN[name]
       └─ 256 colours   →  the exact hex
       └─ 8 colours     →  nearest of the six, deterministically

collab color "#00cccc"  (later)
  └─ app.py  meta.color = "#00cccc"     assigned_color untouched
       └─ roster now resolves to the chosen one

collab color none       (later)
  └─ app.py  meta.color popped          assigned_color untouched
       └─ roster falls back to the assigned one — never to local dealing
```

## What could go wrong

**A hub older than this change** publishes `meta["color"]` only, so a
participant with just an `assigned_color` arrives with an empty `color` field
and that viewer deals locally, as it does now. Degradation, not breakage.

**A viewer older than this change** needs nothing: it reads one `color` field
and always has.

**A colour that will not parse** is already handled — `record_colours()` drops
it and falls back to the deal. The host must therefore only ever assign values
that `hex_to_rgb` accepts; assigning is under our control, so this is a
constraint on the picker, not a runtime branch.

**Two participants joining at the same instant** could both be handed the same
"first free" colour if the read and the write are not serialised. The store
already holds a lock around its writes; the pick must happen inside it, or the
palette check is a race.

## Testing

Existing tests must keep passing untouched — `test_colour_propagates.py`,
`test_colour_push.py`, `test_colour_maths.py` cover the half that already
works. If one of them fails, that is a finding to report, not a test to relax.

New tests:

- A guest arriving with no colour leaves with one in `assigned_color`.
- A guest arriving with a colour keeps it, and gets no `assigned_color`.
- The host has a colour immediately after `collab host`.
- A host restarting an existing session keeps the colour it had.
- An assigned colour never duplicates one already in the session, including one
  a participant brought with them.
- A revoked participant's colour returns to the pool.
- `collab color none` does not erase the assigned colour, and the roster falls
  back to it.
- A later `collab color` displaces the assigned colour in the roster without
  removing it.
- Two viewers reading the same roster resolve the same colour for the same
  name — the premise itself, asserted directly.
- A hex the terminal cannot serve maps to one of the six, and the same hex maps
  to the same one every time.

## Out of scope

- Letting a person choose which colour the host assigns them. `collab color`
  already covers that case.
- Any change to `collab color`, to the identity file, or to the roster's shape.
- The dealing code itself. `_dealt_slot()` stays as the fallback for a
  participant the roster says nothing about — an older hub, or a colour that
  will not parse.

## Files this touches

| file | change |
|---|---|
| `src/collab/server/session.py` | assign at host creation |
| `src/collab/server/app.py` | assign at join |
| `src/collab/server/store.py` | the picker, inside the write lock |
| `src/collab/server/hub.py` | roster resolves `color or assigned_color` |
| `src/collab/client/tui.py` | nearest-of-six fallback; `$SPEAKER` redefined |

It does not belong on `viewer-scrollbars-and-demo`, which is about the viewer
answering the mouse. It gets its own branch and its own pull request.
