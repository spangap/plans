# LXMF proxy — an always-on server holding the account, a roaming client

> Status: **built.** The straddles are `reticulous/lxmproxy` (server) and the
> proxy-client section of `reticulous/lxmf`; the documentation that stands is
> `lxmproxy/README.md`, `lxmproxy/INTERNALS.md` and lxmf's own §8c.

## What this is and why

Store-and-forward that keeps the mailbox blind to what it holds needs *other
people's clients* to speak its protocol: an announce field naming the mailbox,
a deposit handshake, a delivery confirmation back to the sender. Until they
do, a Reticulous user talking to Sideband gets nothing from it.

This is the pragmatic inverse, shaped like POP/SMTP. One device is the
**server**: it holds an LXMF identity's secret keys, registers and announces
`lxmf.delivery` for it, receives messages, and sends them. Another device is
the **client**: it holds the same keys, registers nothing, and exchanges
messages with the server over a permanently-held Channel. The rest of the world
sees an ordinary, always-online LXMF node and needs to implement nothing.

The cost is stated plainly and not designed around: **both devices hold the
account key and the cleartext.** The version where the server holds only
ciphertext is a propagation node, and it needs the world to change. This does
not.

What it buys:

- Messages arrive while the user's device is off; the server delivers their
  outbound while they are away.
- The roaming client advertises nothing, so no announce tick from a moving
  device and no waiting for paths to propagate to it. A link initiator needs no
  inbound path; responses ride the link home.

What it assumes: the server's uplink is better than the client's. When both sit
on the same LoRa mesh every message crosses the air twice, once to the server
and once to the client, and the design buys only the offline half.

## 1. Shape

Two halves, deliberately asymmetric:

- **Client → in the `lxmf` straddle.** It touches everything lxmf already owns:
  the role setting, the delivery-queue handoff, message statuses and
  checkmarks, the link indicator, writing inbound into the message tree, the
  body-download affordance. A separate straddle would have to reach into
  lxmf's storage tree and re-speak its vocabulary.
- **Server → new straddle `reticulous/lxmproxy`.** Self-contained:
  the box's proxy destination, provisioning, the handover bookkeeping, quota
  and the retention sweep. Small roaming boards get the client for free and
  never pay for the server.

Shared frame codec lives in `lxmf` (`lxmproxy_wire.{h,cpp}`); the server
straddle depends on `lxmf`.

The server runs each hosted account as an **ordinary lxmf identity**: it holds
the keys, so lxmf's own store, delivery queue, retry, `delivery_timeout` and
status transitions do all the work. There is no separate spool. The proxy
straddle watches storage and translates. Accounts per box are bounded by
lxmf's identity slots (`LXMF_MAX_IDENTITIES`), and each costs one hosted
destination in rnsd (§12).

## 2. Exactly one registrant

The invariant, and the source of most of the failure modes:

> At every moment, exactly one device registers and announces the account's
> `lxmf.delivery` — never zero, never two.

One per-identity key holds the role:

```
s.lxmf.id.<n>.proxy_role = off | server | client
```

`server` registers `lxmf.delivery` and announces per the device's normal
per-interface announce settings. `client` registers nothing, announces nothing,
and hands its delivery queue to the proxy Channel. Making it one enum means the
both-ends-registered state is unreachable by construction.

Both transitions are handshakes with a pending state, never a local flag flip:

- **Proxy on:** client keeps its own destination registered until the server
  confirms it is serving, then unregisters.
- **Proxy off:** client stays proxied — fully working, server still delivering —
  until the server acknowledges the release, then registers. It does *not* enter
  a state where it is neither.

Deregistering a destination tells the network nothing: the previous
registrant's announces keep bouncing around until they age out, and peers keep
their cached path for as long as their path expiry allows. The new registrant
announces at once and the network converges as that announce spreads. We live
with the window; nothing detects or corrects it.

Implementation note for the client side of both transitions: rnsd deregisters
asynchronously on its own task, and opening a destination that is still
registered yields a silent outbound-only handle. Close the old handle, wait for
the slot to clear, then open — and re-issue the inbound-Link listen after every
open, as `connectOurDest` does today. The `enabled` setting never unregisters
anything, so `proxy_role` is the first code path that does.

**Dead-server override.** If the server is physically gone the release never
lands, so there is a manual override, and it must state what it costs:

- if the server ever returns it will register and announce the same address, and
  nothing on the network can tell the two apart;
- anything the server still holds is stranded — undelivered outbound, and
  inbound it accepted but never handed over;
- the key is still on that box, and there is no revocation short of a new
  identity, which is a new address;
- the ratchet state on the server is stranded with it (§3): until peers hear
  the client's fresh announce, what they send is encrypted to ratchets the
  client does not hold.

## 3. Provisioning

```
server              mints one proxy identity per box, registers lxmproxy.server
                    on it, announces with the operator's label in app_data
user     → client   picks the server by label from heard lxmproxy.server
                    announces → s.lxmf.id.<n>.proxy_dest
client   → server   CHANNEL (identifies with the account identity)
server              shows the identified account as pending
operator → server   approves it → s.lxmproxy.serves gains the account hash
client   → server   HANDOVER [privkey, display_name, ratchet record]
server              writes the ratchets, registers lxmf.delivery, announces,
                    confirms
client              unregisters its own destination, role := client
```

Nobody types a hash. The client lists the proxies it has heard by label; the
operator approves accounts on the server as they identify.

**One proxy destination per box, with its own identity.** Not derived from any
account — a destination hash is `H(name ‖ identity_hash)` and an account's
identity hash is public in every `lxmf.delivery` announce, so an
account-derived proxy dest would be computable by anyone who has ever seen that
account announce, and the announce would say out loud which account this box
serves. An independent identity leaks neither. What it does concede is that
every account on one box links to the same hash, so an observer near the
clients can tell they share a server. That is accepted: hosted-destination
slots are the scarcer resource, and the box's label is in the announce anyway.
Moving the server to new hardware means migrating that identity or picking the
new one on each client.

**The account key is the credential.** The link handshake proves the server
holds the proxy identity; the Channel's identify proves the client holds the
account key, which is the entitlement being checked. The `serves` list is
operator policy about which accounts this box hosts, populated by approving
pending identifies, not an auth mechanism. The identify is validated by rnsd
before its result appears in `rnsd.chan.<tag>.remote_identity`, and the server
acts on no message from a Channel whose identity it has not yet read.

**Ratchet state moves with the key — all of it.** Peers encrypt opportunistic
packets to the ratchet in the last announce they heard, and only that ratchet's
private key decrypts them, so a server starting fresh cannot read anything sent
before its own announce reaches each peer. rnsd keeps a destination's whole
retained ratchet set in one storage record, `secrets.rnsd.ratchets.<dest_hex>`
(rotation timestamp plus every retained private key, newest first), applied
when the destination opens. HANDOVER carries that record verbatim; the server
writes it before opening `lxmf.delivery`; the client forgets it once serving is
confirmed, since everything it receives from then on arrives decrypted over the
Channel. RELEASE hands the record back the same way.

## 4. The Channel

Permanently held whenever the client is online, so push is live and lxmf still
feels immediate; the reconnect path is the exception, not the norm.

The transport is an RNS **Channel** (`rnsdChannelOpen` /
`rnsdDestListenChannels`): reliable, in-order, deduplicated, identifying at
establishment with the identity it was opened with. Every control frame is one
Channel message; bodies over a Channel message ride as a Resource on the
Channel's Link (§12). No correlation ids, no timeouts of our own, no proofs
standing in for acks.

```
C → S   CHANNEL (identify = account identity)
S → C   MSG      [msg_id, peer, peer_name, ts, title, size, body?]  pushed live
C → S   FETCH    [msg_id]                                            withheld body
S → C   BODY     [msg_id, content, fields]                           Resource if large
C → S   HANDED   [msg_id]                                            after persist
C → S   SEND     [local_key, peer, ts, title, content, fields, method, thread, pn]
S → C   STATUS   [local_key | msg_id, LxmfStatus, ts, message_id?]  once: the outcome
C ↔ S   CONFIG   [account settings]                                  both directions
C → S   RELEASE                                                      deprovision
S → C   RATCHETS [record]                                            release hands back
```

- **A message is acknowledged; a status is not.** rnsd proves a packet the
  moment the hand-off to the consumer task succeeds, before lxmf has parsed,
  verified or stored anything, and a Resource's conclusion is likewise
  pre-persist. Neither is a handover ack, so mail gets `HANDED`, sent only once
  the record is in storage — that is worth a frame. A status word is not: the
  Channel proves every envelope or tears the link down trying, so the server
  reads `rnsd.chan.<tag>.outstanding` at zero and knows the client has it.
- **Announce app_data** carries what the client needs to know before linking —
  the operator's label, and nothing that identifies any account behind the
  destination (§3). msgpack array; the client keeps its own copy from the
  announce fan-out.
- **No version field.** Everything flashes together; the wire changes outright.

**Liveness is µR's keepalive.** The initiator sends keepalives at an
RTT-derived interval between two and six minutes; either side tears the link
down when it has gone stale, and rnsd delivers that as the Channel's disconnect
callback. Detection within a few minutes is the accepted cost; there is no
application heartbeat. On disconnect the client re-establishes with backoff and
surfaces the Channel state in the UI.

**Newest Channel wins.** A client whose Channel died silently reconnects before
the server's side has gone stale, so the server can hold two identified
Channels for one account. The most recently identified one is the account's
Channel; the server closes the other. rnsd also evicts the longest-idle link
when its link table is full, so a quiet proxy Channel on a busy server can be
dropped for no fault of its own — the client simply reconnects.

The proxy Channel cannot live in lxmf's conversation-link pool:
`s.lxmf.link.idle_s` and the 4-link LRU cap exist precisely to kill long-held
links, so the proxy code owns its Channel outside that pool.

## 5. Inbound — handover, then forget

The server pushes what it has not yet handed over. **What is left is what is
owed**, so there is no cursor and no resume position to get wrong: reconnect
re-pushes the remainder, and the client dedups on `message_id` — a record it
holds with the body still absent is not a duplicate, it is a pending fetch.

One boolean per message on the server, `handed_to_client`, separates *owed* from
*stored*, so deletion is a **policy, not a wire rule**. Today's policy on an
ESP32 is delete on `HANDED`; a Raspberry Pi that keeps everything is then a
config change rather than a protocol revision.

Handover completes only when the *body* has landed, not just the metadata —
otherwise a withheld body is deleted before it is ever fetched.

**Retention.** `retain_days` on the server, applying to anything unretrieved,
not only withheld bodies: a client that never comes back cannot pin the store
forever. Consequence to state in the UI: a message can be proof-delivered to the
server and still expire before the user ever sees it.

**Quota.** The server's message store is lxmf's, on the same small `/state`
partition with no eviction of its own, and one inbound Resource can be a
quarter of it. Per account: `quota_kb` over bytes *not yet handed over* (a
connected client always drains it) and `max_envelope_kb`. At quota the server
stops accepting inbound for that account without proving it, so the sender's
own retry loop keeps the message on their side, and reports the state to the
client on its next connect. There is no LXMF way to tell an arbitrary sender
"mailbox full" other than withholding the proof. Because rnsd proves on
hand-off, the gate is rnsd's (§12).

## 6. Outbound

The client writes a draft locally; `SEND` reproduces it on the server as a draft
plus `lxmf.id.<n>.cmd.send`, and lxmf's existing queue owns retry, backoff and
`delivery_timeout`. The proxy watches the record's status field and emits
`STATUS`. The first `STATUS` carries the server-side `message_id`, mapping the
client's local key onto it.

`SEND` carries everything `cmd.send` and the record carry today, so the server
packs exactly what the client would have: the client's timestamp (so the
`message_id` is the same on both ends), `method`, `thread`, and the
propagation-node target. The client's local key `o_<unix_ms>_<rand4>` is the
idempotency key: a `SEND` repeated after a reconnect meets the same record and
gets the same `STATUS`.

The server deletes an outbound record on the first scan after its `STATUS` is
proved — `rnsd.chan.<tag>.outstanding` at zero — under the same deletion policy
as inbound. A `SEND` over quota is refused with a status the client shows.

While there is no Channel, outbound sits `QUEUED` locally with **no checkmark**.

## 7. Status and checkmarks

An outbound that goes through a proxy has **five** states, and the server's own
progress is not among them:

- **none** — `QUEUED`: nothing has left this device.
- **`…`** — `SENDING_TO_PROXY`: a message too long for one Channel message,
  still crossing as a Resource that can fail halfway.
- **one check** — `ON_OUR_PROXY`: a machine that is not mine has it, and
  whatever it is doing about that is its business. Reached without a frame
  coming back — the Channel is proved end to end, so a SEND that has gone out
  is a SEND the proxy has.
- **two checks** — `OUR_PROXY_DELIVERED`: the server saw a real LXMF proof.
  Opportunistic and Link deliveries both produce one.
- **red ✕** — `OUR_PROXY_GAVE_UP`: it tried and stopped. The server's actual
  `LxmfStatus` is kept verbatim on the record (`proxy_status`) and named on the
  detail page, so the reason survives without the conversation carrying it. The
  timeout is lxmf's own `delivery_timeout` settling `DELIVERY_TIMEOUT` there.

Not among the five, and deliberately: `CANCELLED` (our own doing) and
`PROXY_REFUSED` (the server would not take it — trouble reaching the proxy, not
the proxy's verdict on reaching the recipient).

Status codes 29, 31, 32 and 39 carry them; 33 is a retired gap and stays one.

**The server sends one STATUS per outgoing message, and it is the outcome.**
Its own progress through path requests, sends, proof waits and link retries is
commentary the client cannot act on. `lxmfStatusIsVerdict()` is the test, shared
by both ends; a non-verdict that arrives anyway is ignored.

## 8. Body size and download state

Bodies over a threshold are withheld; the client renders a download affordance
and fetches on demand. `s.lxmproxy.inline_bytes` is the threshold, 2048 by
default. Setting it to 0 derives one from the measured link RTT
(`rnsd.chan.<tag>.rtt_ms`) instead — which a server never has, because rnsd
measures a round trip only on a channel the device DIALS and a proxy's channels
are all dialled to it, so that path lands on its 256-byte floor and withholds
almost everything.

Client-side this needs an explicit durable field on the message record, `body =
absent | present` plus `size`: empty content is a legitimate message, so absence
cannot be inferred. That field is also what survives a reboot mid-decision.

## 9. Direct links coexist

A user-opened conversation link to a peer bypasses the proxy in both directions
— we identify on our own links so peers reply over them (`lxmf/INTERNALS.md`,
"Link identification"). Opening one needs only the identity key, not a
registered destination, and replies over it verify against the precomputed
destination hash, so a proxied client can do this unchanged. A proxied client
does **not** open one for delivery on its own initiative; the proxy is the
default path and direct links are the user's deliberate act. Anything that
rides the mailbox handle — `cmd.ping`, `opportunistic-or-fail` — is unavailable
while proxied.

Consequence: messages sent or received over a direct link leave holes in the
server's copy of the thread. Irrelevant while the server deletes on handover;
it matters the day the server is meant to be an archive, and the fix then is a
frame carrying a message *upward* as already-delivered.

## 10. Settings split

Account-scoped settings live on the server, because it faces the world, but are
only ever *edited* on the client, because it is the only end with a UI:
`display_name`, `stamp_cost`, `enforce_stamps`, propagation-node list, identity
enabled. `CONFIG` pushes them on change and reconciles on connect; the server
pushes back what only it knows — real announce state, propagation results,
quota state, what is actually on the air — so the client displays truth rather
than intent. The announce tick is not account config: it belongs to each
interface of whichever device airs the announce.

Device-scoped stay local: notification sound, inline threshold, link tuning, UI.

**Contacts are not synced.** Both ends auto-create on first contact and are
allowed to diverge; the client keeps its interfaces and its announce catalogue,
so discovery is unchanged. `MSG` carries the peer's display name as an
opportunistic hint for the case where the client never heard that peer announce.

Inbound filtering happens on the server, so client-side blocking is a silent
no-op until it rides `CONFIG` — deferred to the blackholing work, which brings
its own filtering.

## 11. Deliberately later

- **Multiple clients per account.** That is read state, deletion propagation and
  per-client cursors — actual IMAP, and a different shape from this. Not an
  increment; do not half-build toward it. Everything flashes together, so
  deferring costs nothing.
- **A server that is not an ESP32.** Full-text search over large archives is the
  same IMAP shape. The one thing kept open for it now is that deletion is a
  policy (§5), not a wire rule.
- **Consumer-side request handlers in rnsd.** µR runs a request's response
  generator synchronously on the rnsd task, so today only rnsd's own handlers
  exist. The proxy does not need them; a nomad/micron server will.
- **Rotating destination hash**, so the client does not identify itself to its
  radio neighbours by linking to the same hash forever. The adversary here is
  local to the *client* — a mesh neighbour, not the server's ISP. The proxy
  destination already has its own identity (§3), so rotation is a property of
  that identity alone and touches nothing about the account. Notes for whoever
  builds it:
  - **Path requests are louder than links**: broadcast, and they name the
    destination in the clear before any link exists. Rotation must cover path
    acquisition or it is cosmetic.
  - **The announce is the leak.** A fresh announce at the rotation boundary
    re-correlates the epochs by timing alone. Keeping several epochs live at
    once, announced at unrelated times, is the shape of the answer.
  - Against a transport-layer adversary none of this helps; that is a tunnel
    problem, not an RNS one.
- **A server that holds only ciphertext.** The endgame.
  For a client in a hostile place, seizure dominates traffic analysis, and this
  design puts the key and the full cleartext history on the roaming device —
  which argues for at-rest protection and a fast wipe on the client first.

## 12. What rnsd needs

- **Resources on a Channel.** A Channel slot owns a hidden Link that is never
  exposed, and the Resource path only looks up links in the plain link table,
  so a Resource on a Channel's Link is refused today. Needed: a
  `rnsdChannelSendResource(tag, …)` on the hidden Link, and the inbound
  advertisement gate consulting the channel table, completing on the same aux
  port as Link Resources.
- **More hosted destinations.** `RNSD_MAX_OUR_DESTS` is a define sized to the
  consumers that existed; each slot is a few hundred bytes of PSRAM. Raise it
  so a server can host lxmf's full identity count plus its own destinations.
- **A per-destination accept gate.** rnsd proves inbound on successful hand-off
  and never on a dropped one, so quota (§5) is a flag on the hosted destination
  that makes rnsd drop inbound for it without proving — packets and Resource
  advertisements alike.

## Aspect and names

The aspect is **`lxmproxy.server`**. Announce filters key on the app name, so
it is one nobody else uses, and it names the protocol rather than the project
so another implementation could speak it. Should upstream ever adopt the idea
under `lxmf.proxy`, renaming is one flash.

The server straddle is `lxmproxy`, its storage namespace `s.lxmproxy.*`
(per-account settings under `s.lxmproxy.id.<n>.*`), and
its CLI verb `lxmproxy`.

## Open

- Whether the retention sweep tells the sender anything when a message expires
  unretrieved, or expires silently.
