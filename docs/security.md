# Security

This page states collab's trust model plainly and describes what the tool does
and does not protect against.
Read it before you host a session for someone you do not know, or join one.

## The trust model

collab draws a line around three parties.

- **The local user is trusted.**
  Your own configuration, your state directory, and the commands you run are
  yours.
  collab does not defend you against yourself.
- **A remote participant is not trusted.**
  Everything a participant sends — their display name, message text, task
  titles, file names, focus strings, activity text, and usage figures — arrives
  over the network and is treated as untrusted input.
- **A hub is not trusted by the client that connects to it.**
  A hub can send any URL, any filename, and any payload, so the client validates
  what it receives rather than trusting it.

Everything below follows from these three statements.

## What collab protects against

### Authentication and authorisation

Every participant holds a bearer token, issued once in exchange for an invite
code.
Every hub route except the join handshake, the agent card, and the health check
requires that token.

- Tokens and invites are stored only as SHA-256 hashes.
  A token is looked up by the hash of what the caller presented, so the raw
  secret is never stored.
- The invite travels in the URL fragment (`https://host#CODE`), so it is never
  sent in a request line and stays out of proxy and server logs.
- The join endpoint is rate-limited to ten attempts per minute.
- Host-only actions — removing a participant, withdrawing another person's file
  — are refused for anyone who is not the host.
- Removing a participant revokes their token and closes their live feed at once.
  A revoked token is rejected on its next use.
- The message sender is set by the hub from the authenticated participant, never
  taken from the message, so a participant cannot attribute a message to someone
  else.

### The session password

A host can set a password with `collab host --password`, which makes the plain
session URL a second way in: no invite code in the link, the secret handed over
separately.
It is an addition to the invite, not a replacement — a link already shared keeps
working.

**The password is never sent to the hub, in any form.**
Not in the clear, and not as a hash either: a hash sent over the wire *is* the
credential, and anyone who records one can replay it.
Joining is a challenge–response built the way SCRAM (RFC 5802) builds one.

- The hub stores a random salt, an iteration count, and a *stored key*.
  The stored key is the SHA-256 of a key that only the password derives, so
  reading the hub's database does not give anyone a way in.
- A joiner asks for a challenge, which carries the salt, the iteration count and
  a single-use nonce, and answers with a proof derived from the password.
- The proof is bound to that nonce and to every parameter the hub offered, so a
  recorded proof cannot be replayed and a challenge altered in flight produces a
  proof the real hub rejects.
- Key derivation is PBKDF2-HMAC-SHA256 at 600,000 rounds — OWASP's floor — and
  it runs on the joiner.
  The hub verifies with two hashes, so join attempts cannot be used to exhaust
  it.
- A joiner refuses a challenge asking for fewer than 100,000 rounds or more than
  5,000,000, so neither a weakened derivation nor a hostile one is accepted.
- Five *failed* credential attempts per minute, per address, closes both the join
  and the challenge endpoint for the rest of that minute.
  Only failures are counted, so a room filling up normally never trips it.
- The password must be at least 8 characters.
  It is not recoverable — the hub keeps what verifies a password, not the
  password — so `collab host --password` asks for it twice.

A password is a weaker secret than an invite code: an invite is 256 bits of
randomness, a password is something a person chose and can say out loud.
That is the trade, and it is deliberate — it buys a link that is safe to put
where a secret is not.
Choose one accordingly, and send it by a different route than the link.

### Attributable, isolated messages

Delivery and visibility are decided on a stable participant id, not a display
name.
A direct message reaches only its sender and its recipient, including when the
feed is replayed after a reconnect, so renaming yourself never exposes another
person's private messages.

### The wake feature

The wake runs a command unattended whenever a message arrives, which means
whenever a remote participant decides one should.
collab treats that command with matching suspicion:

- The command is armed only by the local user or agent, never inferred from
  anything a participant said.
- Values substituted into a command are quoted for the shell, so a target string
  cannot smuggle a second command into it.
- `collab wake show` prints the armed command in full, so you can see what runs.
- Arming a command that is not one of the reviewed recipes requires `--yes`.
- The batch of messages is handed to the woken agent framed as untrusted data to
  interpret, not as instructions that outrank the agent's own.

### Terminal output

A display name, a message, a task title, a file name, and a usage line are all
chosen by another participant, and the plain-print commands print them to your
terminal.
collab removes control characters from those strings before printing, so a name
or a message cannot carry an escape sequence that clears your screen, rewrites
your window title, or forges a line.

### File transfer

- An upload is capped at 10 MB, enforced while streaming, so an oversized upload
  is never fully written to disk.
- The hub stores each file under a server-generated id, not under its sent name,
  so a crafted filename cannot escape the storage directory.
- A file addressed to someone is downloadable only by that person and the
  sender.
- The recipient verifies the file's checksum before confirming receipt.

### Input bounds

The text a participant declares about itself — its name, its focus, a task
title, a room name — is bounded in length and count before it is stored, so a
single participant cannot flood every roster with an oversized value, and cannot
smuggle an unbounded nested object into the roster through the join handshake.

### Following a hub that moved

A hub on a free tunnel can come back at a new address, and a hub the host
revived can come back on a new port.
A client picks up the new address only from a private, per-user registry, and
only when the address is a loopback address that cannot leave the machine.
Following an address means sending your bearer token to it, so the client never
follows an address that another machine could have chosen.

### Local file permissions

collab keeps its state directory private to your user (mode 0700), and writes
each secret file — the bearer token, the invite, the host token, an armed wake
command — with owner-only permissions (mode 0600).
On a shared machine, another local user cannot read your messages, your tokens,
or your roster.

## What collab does not protect against

Be clear-eyed about the limits.

- **A participant you admit is inside.**
  An invite is a key to the room.
  Anyone who holds it, or whom you let join, can read every room message, see the
  roster, propose and claim tasks, and download files shared to a room.
  collab controls who gets in and keeps direct messages private; it does not make
  a room member harmless.
  If you want a genuinely clean guest list, start a new session rather than
  resuming one, which retires every earlier invite.
  Note that resuming retires the invites and **keeps** the password: a link
  travels and gets forwarded, a password is handed over deliberately, and the
  host cannot be given the old one back to re-share.
  Replace it with `collab host --resume --password`, or start fresh.
- **The host sees everything.**
  The hub stores the whole conversation in its SQLite log.
  Whoever hosts the session can read all of it.
  There is no end-to-end encryption between participants.
- **Transport privacy depends on the tunnel.**
  When you share over ngrok, traffic to the public address is protected by
  ngrok's TLS.
  A hub reachable only on a local network, with no tunnel, is as private as that
  network.
- **A malicious hub is still a party to the conversation.**
  The client validates URLs, filenames, and payloads from a hub, and refuses to
  send its token to a non-loopback address it found in a file.
  It cannot prevent a hub you deliberately joined from seeing what you send it.
- **Denial of service by an admitted participant.**
  Rate limits, size caps, and input bounds blunt accidental and casual abuse.
  An admitted participant who is determined to be disruptive can still consume
  resources; the defence is to remove them with `collab kick`.
- **The local user.**
  collab runs commands you configure and reads files you own.
  It does not sandbox you from your own machine.

## Reducing your exposure

- Share invite links over a private channel, and treat them as passwords.
- Where the link has to go somewhere less private, set a session password
  instead and send the two halves by different routes — the URL in the channel,
  the password over a call.
- Start a fresh session, rather than resuming, when the guest list should change.
- Remove a participant you no longer trust with `collab kick`; their token stops
  working immediately.
- Review an armed wake with `collab wake show`, and keep to the reviewed recipes
  unless you have a reason not to.
- Host without a tunnel (`--no-tunnel`) when everyone is on one network and you
  do not need a public address.
