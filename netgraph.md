# NetGraph distribution — every node holds the community's whole graph

Plan for making the network graph a distributed dataset: each node publishes a
small self-report of its own links and interfaces, gossips it community-wide,
and resolves the union of everyone's reports into node/link tables published
through storage — so the browser NetGraph app, the LCD, and any on-device
logic read a complete network view without a browser ever being involved. The
browser app becomes a pure renderer.

Audience: the implementer, working in this repo. File pointers are to code
that exists today; read the referenced headers before starting — the design
leans on rnsd primitives that already work (announce fan-out, stored
announces, Channels) and adds almost no new protocol machinery to rnsd itself.

## Design invariants

These were settled deliberately; do not re-open them casually.

- **One writer per record.** A record is one node's self-report about itself:
  its identity, name, interfaces, links, announced destinations. No node ever
  writes into another node's record. The global graph is the union of records
  plus a local-only overlay for unannounced neighbours. "Newer seq wins" is
  the entire conflict story.
- **Records are atomic wholes.** Ingest replaces everything held for an origin
  in one step. There are no partial or incremental record updates, ever —
  this is what makes unsigned re-serialization by relays safe, and what lets
  a record's internal references stay record-scoped.
- **Records are unsigned.** First-hand records arrive as announce `app_data`
  and are covered by the announce's own signature (RNS signs announces);
  relayed records travel over encrypted Links between community members and
  carry no signature of their own. A member can fabricate; a signature never
  prevented that. Consequence: records must never be handed onward to a party
  that does not trust the whole community.
- **Configuration goes in records; measurements do not.** Frequency, spreading
  factor, interface names, link existence: in the record. RSSI, SNR,
  negotiated SUPE (Single Use Pad Extension) budgets, traffic counters: not in
  the record — they change constantly and would keep digests permanently
  mismatched. (Per-node live queries for those are a later phase, see
  "Deferred".) The test for any field: does a change to this deserve waking
  the whole mesh?
- **A merely-heard peer is not a link.** A peer enters the record's link list
  only once it qualifies by the medium's own criterion (for the core table:
  an announce decoded from it, which is what creates an rnsd peer row). The
  odd packet caught when the wind was right stays local.
- **No version negotiation.** All community nodes flash together. There are no
  capability bits, no schema versions, no migrations. Forward tolerance is
  structural: unknown line types are carried and skipped, unknown trailing
  fields on known lines are ignored.
- **The pipe-text format is the specification; the packed wire form is a
  tokenization of it.** Same lines, same fields, same order, mechanically
  encoded. No packed-only fields, no reordering. Everything count-dominant
  (link cells, hash prefixes, header) is binary; one-off descriptive fields
  (interface parameters) stay UTF-8 text even in the packed form, so no
  per-medium decoder is needed anywhere to expand a record back to text.

## Protocol

Two paths move records: announces push your own outward, a Channel-based
exchange fills gaps.

```
push (continuous, unsolicited, mesh-wide):
  every node:  ANNOUNCE netgraph.discovery   app_data = own record, possibly abridged
               — flooded, deduplicated, rate-limited by ordinary RNS announce mechanics

sync (on demand, over one Reticulum Channel between two nodes):
  I → R   DIGEST       every (origin, seq) I holds
  R → I   RECORD_PART* records I lacks or holds older
  R → I   WANT         origins R lacks or holds older
  I → R   RECORD_PART* those records
  both    DONE         then the initiator closes the channel
```

The sync runs: once at boot (backfill the world from one neighbour), on a slow
periodic beat (anti-entropy against digest drift), and targeted (a single WANT,
no digest) when an announce reveals a newer or abridged record we don't hold in
full.

### Why these primitives

- The announce path costs no new wire machinery: `rnsdDestOpen` +
  `RNSD_DEST_ANNOUNCE` (ports.h:410) store the record as `app_data`; the
  interfaces' own announce beats put it on the air; the announce fan-out
  (`RNSD_PORT_ANNOUNCES`, ports.h:36, aspect filter `"netgraph.discovery"`)
  delivers every arriving record — with hops, dest hash, identity hash —
  without touching rnsd's announce handler.
- The sync path uses `rnsdChannelOpen` (client) and `rnsdDestOpen` +
  `rnsdDestListenChannels` (server), both existing (rnsd.h). Channel messages
  are reliable, in-order, and typed (`[msgtype:2 BE][payload]`), which is
  exactly the digest/want/records shape. No request/response server support
  exists in rnsd and none is needed.

### Channel message types

Pick a msgtype block clear of `RNS::Channel::MSGTYPE_RAW` (0x0100) and rnsh's
types; suggested 0x4e00 ("N"):

| msgtype | name        | payload |
|---------|-------------|---------|
| 0x4e00  | DIGEST      | `total:u16 LE \| offset:u16 LE \|` entries, each `origin:16 \| seq:u32 LE`. Chunk when > channel MDU (maximum data unit, ~428 B — that is ~21 entries per message). |
| 0x4e01  | WANT        | `count:u8 \|` count × `origin:16` |
| 0x4e02  | RECORD_PART | `origin:16 \| seq:u32 LE \| parts:u8 \| part:u8 \|` record bytes. `parts`=1 is the normal case; reassemble in order (Channel guarantees order), apply on last part. |
| 0x4e03  | DONE        | empty. Each side sends when it has nothing further; initiator disconnects after sending and receiving DONE, or on timeout. |

Digests use the full 16-byte origin hash: a false match here silently loses an
update, and the cost is only ~20 B per community node on an exchange that runs
every half hour against one neighbour.

## Record format

### Pipe-text form (the specification)

A record is UTF-8 lines, fields separated by `|`. Line order: `n`, `dt`, then
`if`/`ln` pairs per interface, then any detail lines.

```
n|Kitchen T-Deck|t
dt|3|a1b2c3d4 9f3e2a11 77ab01cd
if|lora|radio0|868.5|7|125|s
ln|radio0|37|9f3e2a11.0.t 8ab2c3d4.1 7c1d99f0.2 ...
if|tcp|wan0
ln|wan0|1|55aa66bb.0.t
lora|if|radio0|<class-owned detail fields>
```

- `n` — display name (the device's hostname, or its LXMF display name where no
  hostname is set; may be empty) and flags: `t` = this node is an RNS transport
  node. The name has
  `|`, newline, and control characters replaced by spaces at build time; that
  substitution is the entire escaping story, and consumers may split on `|`
  unconditionally.
- `dt` — the node's own announced destination hashes, as 4-byte (8-hex)
  prefixes. This is the join evidence: another record's `ln` cell naming one
  of these prefixes is a link to this node.
- `if` — one per interface: class (the same class word behind the status-line
  pills — `lora`, `tcp`, `ble`, `espnow`), the registered instance name, then
  class-owned configuration fields, which are opaque to everyone but that
  class's straddle and rendered verbatim where undecodable.
- `ln` — the links on one interface, referenced **by interface name** (never
  by position). Second field is the TRUE link count; then at most K cells
  (all of them in the full record, the K freshest in an abridged announce —
  count > cells is itself the "fetch the rest" signal). Cell subfields are
  dot-separated: `prefix.freshbucket[.t]` — 4-byte destination prefix of the
  peer, freshness bucket **relative to the record's own timestamp** (0 ≤ 5
  min, 1 ≤ 1 h, 2 ≤ 6 h, 3 older), `t` = that peer is a transport node.
  Only identity-bearing peers (ones that have announced) get cells; a peer
  known only by transport address is counted but not listed — no other node
  could join it to anything anyway.
- Detail lines (`lora|if|radio0|…`) — class-owned extra lines, scoped by
  reference to an `if` (or, dot-notation, a link). None are defined in v1;
  the rule exists so a straddle can add one without touching core code.
- Exactly two separator levels, ever: `|` for fields, space for list cells,
  `.` for subfields within a cell. Nothing nests further.

Unknown first-field → skip the line but carry it verbatim (it re-serializes
into the packed form untouched). Known line with extra trailing fields →
ignore the tail.

### Packed wire form

```
header:  magic:u8 = 0xF5 | origin:16 | seq:u32 LE | flags:u8 (bit0 = abridged)
lines:   repeated  len:u16 LE | tag:u8 | body   until end of payload
```

`seq` doubles as the record's build timestamp: device unix seconds, guarded
monotonic (`seq = max(now, last_seq + 1)`), last value persisted under
`s.netgraph.seq` so a reboot with a bad clock cannot re-issue an old seq.

| tag  | line | body |
|------|------|------|
| 0x01 | `n`  | `flags:u8` (bit0 transport) `\| name:str` |
| 0x02 | `dt` | `count:u8 \|` count × 4 raw bytes |
| 0x03 | `if` | UTF-8 text of the pipe line after the tag word, verbatim (`lora\|radio0\|868.5\|7\|125\|s`) |
| 0x04 | `ln` | `iface:str \| count:u16 LE \| cells:u8 \|` cells × (`prefix:4 \| flags:u8` — bits 0-1 fresh bucket, bit 2 transport) |
| ≥0x20| detail | UTF-8 text of the whole pipe line verbatim |

`str` = `len:u8 | bytes`. Tags 0x05–0x1f are reserved for future core lines;
an unrecognized tag's line is carried by its length and republished as text
where possible, hex otherwise.

The magic byte 0xF5 is an invalid UTF-8 lead byte: it exists so nothing that
sniffs announce `app_data` for a display name mistakes a record for text.
**Verify `rnsdAnnounceName` (rnsd.h:504) returns `""` for a 0xF5-led payload
and fix it to reject invalid UTF-8 if it does not** — otherwise every
netgraph announce pollutes peer listings with a garbage name. Also add
`netgraph.discovery` to the aspect table behind `rnsdAspectLabel` so listings
name the aspect.

### Size expectations

Header 22 B; a typical record (name, 3 dests, two interfaces, 8 link cells)
lands near 150–200 B packed, comfortably inside announce `app_data` on LoRa.
A dense LoRa channel (37 peers → 37 five-byte cells) makes the full record
~350 B — still one Channel message, but the announce carries the abridged
form.

## The on-device component

New client component in the `rns` straddle: `esp-idf/src/netgraph.cpp` +
`include/netgraph.h`, registered via `rnsServiceRegister("netgraph", …,
RNS_PHASE_CLIENT)`, gated by `s.netgraph.enable`. It owns four things.

### 1. Record builder

Sources, all existing:

- Own identity hash: `rnsdIdentityHash` on rnsd's default identity
  (`secrets.rnsd.identity`).
- Own announced destinations for `dt`: rnsd knows its hosted destinations but
  exposes no walk; add a small `rnsdHostedDestsForEach(cb, ctx)` to rnsd.h —
  the control DC's "list dests" path shows where the list lives.
- Interfaces for `if` lines and links for `ln` lines:
  `rnsdNodesForEach` / `rnsdPeersForEach` (rnsd.h:458+). Group peers by node;
  an attributed node's cell prefix is its freshest announced destination; an
  unattributed radio peer is its own cell keyed by its destination prefix.
  Interface class = registered name up to `/` or `_` (same rule as
  `ifaceClass` in `browser/src/lib/netGraph.ts`).
- Own transport flag: rnsd's transport-enabled setting (grep `s.rnsd.` in
  rnsd.cpp for the key).
- Display name: the same source the LXMF announce name comes from.
- `if` class-owned fields: v1 hardcodes none (bare `if|<class>|<name>` lines);
  the contribution API below adds them in a later phase.

Rebuild triggers: a link appearing or disappearing from the composed set, a
contributing config change, or the horizon expiring a link — floored to one
rebuild per `s.netgraph.rebuild_floor_s` (default 600; a trailing-edge timer,
so a burst of changes coalesces). Freshness buckets are recomputed only at
rebuild and are relative to the record timestamp, so bucket drift alone never
triggers a rebuild. Links older than `s.netgraph.link_horizon_h` (default 6)
leave the record.

On rebuild: bump seq, persist `s.netgraph.seq`, store the record locally like
any received one, hand the abridged form (`s.netgraph.announce_cells`,
default 8, freshest cells per `ln`, abridged flag when anything was cut) to
rnsd via `RNSD_DEST_ANNOUNCE`. The interfaces' announce beats take it from
there; no netgraph-owned announce timer. Note: hosting this destination adds
one announce per beat sweep to every medium's airtime — accepted for the
prototype; if measurement later shows it matters, per-destination cadence in
the replay sweep is the knob to build, not a netgraph-side timer.

### 2. Record store

Heap store, RAM only, capped by `s.netgraph.store_kb` (default 24): per
origin, the packed record bytes plus `{seq, received_at}`. No flash
persistence — a rebooted node backfills (~3 KB compressed-equivalent over one
link) faster than flash-wear accounting is worth; revisit only if boot-time
LoRa-only backfill proves painful.

- Ingest (from announce fan-out or RECORD_PART): magic/shape validate, reject
  seq ≤ stored, reject records whose seq (timestamp) is older than
  `s.netgraph.horizon_h` (default 24) — stale records are neither stored nor
  offered in digests.
- Expiry sweep on the same slow beat as sync: drop past-horizon records and
  unpublish their rows.
- Eviction under the byte cap: stalest `received_at` first. Own record is
  never evicted.

### 3. Resolver → published rows

After any store change, re-resolve and republish. Resolution is the
evidence-join currently living in `browser/src/lib/netGraph.ts` `buildGraph`,
moved down: every record is a vertex keyed by origin; every `ln` cell resolves
its 4-byte prefix against all records' `dt` sets (and our own hosted dests);
an unresolved prefix is a pending edge, kept but marked. Then the local
overlay: rnsd nodes/peers of ours that appear in no record (unannounced BLE
peer, label-only rows) become local-only vertices and edges, so the local
graph never shows less than it does today.

Published under `netgraph.*` (ephemeral keys — RAM-tier, browser-synced
automatically via the storage DataChannel; see storage.h header comment).
Follow the slot conventions of `rnsd.nodes.*` / `rnsd.peers.*`
(rnsd_peers.cpp:563 onward):

```
netgraph.self               own origin hash, hex
netgraph.nodes.slots        walk bound
netgraph.nodes.<i>.id       origin hash hex ("" = local-only vertex)
netgraph.nodes.<i>.name     display name (may be "")
netgraph.nodes.<i>.label    transport-address label for local-only vertices
netgraph.nodes.<i>.transport 0/1
netgraph.nodes.<i>.ts       record timestamp (= seq), 0 for local-only
netgraph.nodes.<i>.stale    1 when past half the horizon (renderer dashes it)
netgraph.links.count
netgraph.links.<j>.a        node slot of the reporting side
netgraph.links.<j>.b        node slot of the peer, -1 unresolved
netgraph.links.<j>.bref     peer prefix hex, when b = -1
netgraph.links.<j>.cls      interface class word ("lora")
netgraph.links.<j>.iface    reporting side's interface name ("radio0")
netgraph.links.<j>.fresh    bucket 0-3
netgraph.links.<j>.transport 0/1 (peer side, as reported)
netgraph.ifs.<i>...         per-node if lines: class, name, detail text verbatim
```

Both directions of a link arrive (each endpoint reports it); publish both —
asymmetric hearing is information, and the renderer already draws parallel
arcs. Publish inside a `storageBegin()`/`storageEnd()` bracket so the browser
sees one coalesced patch. Skip republishing when `uiTelemetryWanted()` is
false *only* for rows no on-device consumer reads — for now publish always;
the rows ARE the API.

### 4. Sync engine

- Server: `rnsdDestOpen("netgraph.discovery", "", SINGLE)` once at start, then
  `rnsdDestListenChannels(handle, NETGRAPH_SYNC_PORT)`. Serve DIGEST/WANT per
  the protocol section; the server side also uses the initiator's DIGEST to
  push what the initiator lacks.
- Boot backfill: once rns is up and a partner is known, open a Channel
  (`rnsdChannelOpen`) to the partner's netgraph destination, run the exchange.
- Periodic anti-entropy: every `s.netgraph.sync_min` (default 30) against one
  partner, rotating.
- Targeted fetch: an announce whose record is abridged, or whose seq exceeds
  the stored one while the full record didn't fit the announce, queues that
  origin; the fetcher opens a Channel **directly to the origin's netgraph
  destination** (the announce's dest hash — RNS routes it) and sends a lone
  WANT. Rate-limit: one in-flight fetch, per-origin backoff.
- Partner selection: track per-origin `{netgraph dest hash, hops}` from the
  fan-out frames. Partners are hops==1 origins; prefer one whose destination
  sits on a non-LoRa interface (cross-check the dest against rnsd's peer rows
  for the iface name), else any.

Channel MDU discipline: every message ≤ MDU; digests chunk by `total/offset`;
records chunk by RECORD_PART. One sync in flight at a time, modest timeout,
fail-and-retry-next-beat — never block anything on a sync.

## Interface detail contribution (phase 5)

`netgraph.h` exposes:

```c
typedef size_t (*netgraph_iface_detail_t)(const char* iface_name,
                                          char* out, size_t outsz);
void netgraphContributeIface(const char* cls, netgraph_iface_detail_t cb);
```

At rebuild, for each `if` line the builder calls the class's callback (if
registered) to fill the class-owned tail of the line — pipe-separated text,
config only ("868.5|7|125|s"). iface-lora registers one and supplies
frequency/SF (spreading factor)/bandwidth/coding rate/SUPE-enabled from its
radio config. Straddles never see the record; they contribute fields, the
builder composes — the `rnsdPillSet` relationship, one layer up. Nothing in
spangap-* is touched.

## Browser

`buildGraph` in `browser/src/lib/netGraph.ts` is rewritten to read
`netgraph.*` rows only — no more join logic, no reading `rnsd.nodes.*` /
`rnsd.peers.*` there. Vertices from `netgraph.nodes.*`, edges from
`netgraph.links.*`, colour still `rns.pill.<cls>.color`, dashing from
`stale`/`fresh`. Unresolved edges (`b` = -1): render to a small unlabelled
stub vertex so degree stays visible. Layout, parallel arcs, caption placement
(`forceLayout.ts`, `NetGraphWindow.vue`) are untouched. Delete the old join
code outright, and update the NetGraph section of `rns/README.md` — the
merge-on-evidence prose moves from "the app does this" to "the device does
this"; the app's paragraph shrinks to drawing.

## CLI

One `netgraph` verb on the rns straddle's command (pattern: `rnsdPeersCli`,
rnsd.h:493):

- `netgraph` — store summary: origins, seqs, ages, bytes, last sync.
- `netgraph dump [<origin prefix>]` — records expanded to pipe text.
- `netgraph sync` — run an exchange now.
- `netgraph rebuild` — force own-record rebuild + announce.

## Settings

All under `s.netgraph.`, seeded with `storageDefaultTree`:

| key | default | meaning |
|---|---|---|
| `enable` | 1 | run the component |
| `rebuild_floor_s` | 600 | min seconds between own-record rebuilds |
| `announce_cells` | 8 | max link cells per `ln` line in the announced form |
| `link_horizon_h` | 6 | a link older than this leaves the record |
| `horizon_h` | 24 | records older than this are dropped and never offered |
| `sync_min` | 30 | anti-entropy period, minutes |
| `store_kb` | 24 | record-store byte cap |

`s.netgraph.seq` is state, not a setting; don't seed it.

## Traffic budget (sanity ruler, not a spec)

Community of ~40, records ~200 B packed. A new node with M real neighbours
costs: one record flood for itself plus M neighbour-record floods of ~250 B
(each new edge has two owners; the rebuild floor coalesces a bursty join into
one rebuild per neighbour), one ~8 KB backfill over exactly one link, and
~20 B of digest growth everywhere forever. Steady state is digests that
match. If measured traffic exceeds this shape, first suspects are the
qualification gate (ghost peers churning records) and the rebuild floor.

## Deferred — designed for, not built now

- **Live drill-down queries** (RSSI/SNR, SUPE budgets, counters for one node
  or link): new Channel msgtypes on the same destination, answered from live
  tables, never stored or gossiped.
- **Remote-management adapter**: see the section below — it is the way a
  vanilla RNS node gets onto everyone's graph.
- **Serving remote management to stock nodes**: needs server-side
  request/response in rnsd, which doesn't exist; build only when something
  needs it. Note the asymmetry is deliberate — as a CLIENT the feature reaches
  nodes we will never be allowed to touch, which is the whole point of it.
- **Store persistence across reboot** and per-destination announce cadence:
  only if measurement demands.

## Remote management — pulling foreign nodes onto the graph

```
 device                                  stock RNS node (rnsd)
   │                                       │
   │  ← ANNOUNCE rnstransport.remote.management (every 2 h, carries the key)
   │                                       │
   │  ─ LINK ─────────────────────────────►│
   │  ─ IDENTIFY (our node identity) ─────►│   ← checked against
   │                                       │     remote_management_allowed
   │  ─ REQUEST /path ["table",dest,hops] ►│
   │  ◄ RESPONSE [{hash,via,hops,interface,timestamp,expires}, …]
   │                                       │
   │  ─ REQUEST /status [with_link_count] ►│
   │  ◄ RESPONSE [{interfaces:[…],rxb,txb,rxs,txs,…}]   (usually a Resource)
```

A netgraph record is a node speaking for itself, and a node that does not run
this firmware never files one. Such a node is drawn as `routed` today: routing
knows it exists, nothing knows what it is attached to. Reticulum's own remote
management is the way to ask it directly, and it reaches the nodes we care
about most — the ones whose software we do not get to write, whose operators
would refuse a shell account but will add one identity hash to an allow list.
The ask is `remote_management_allowed = <our node identity hash>`: one line of
their config, read-only, revocable, and the same hash this record's `origin`
already carries.

**What it answers.** Two request paths on
`rnstransport.remote.management`, both read-only:

- `/path` — `["table", <dest hash or nil>, <max hops or nil>]` → a list of
  `{hash, timestamp, via, hops, expires, interface}`. This is the prize: `via`
  names the neighbour that node routes through and `interface` names the
  interface it uses, which is field for field what an `ln` cell carries. A
  destination-filtered query returns one entry and always fits a packet.
- `/status` — `[include_link_count]` → the interface stats dict: `interfaces`
  (per-interface name, status, mode, bitrate, counters) plus top-level
  `rxb`/`txb`/`rxs`/`txs` and forty more keys. Large enough on a busy node
  that the response arrives as a Resource rather than a packet.

**Discovery is free.** Management destinations announce on a 2-hour timer, so
a node offering remote management advertises itself with its key: teach
`rnsdAspectLabel` the aspect and every reachable one appears in the announce
stream, recallable and ready for `rnsdLinkOpen`, with no hash derivation and
nothing to configure. The open question is whether the announce table still
holds a 2-hourly announce when the operator asks — check the pruning before
promising this works unattended.

**What exists.** The client half is built and was written with this in mind:
`rnsdLinkRequest` (rnsd.h:838) names `rnstatus -R` / `rnpath -R` in its own
docs, its inline-payload limit binds the REQUEST (ours is a few bytes) and not
the response, Resource-delivered responses conclude through
`Link::response_resource_concluded`, and `unpack_blob_or_object` hands a
structured msgpack response up verbatim instead of demanding a bin.

**What is missing**, and it is small: the MsgPack shim packs only `double`,
`bin` and `Bytes`, so `/status`'s bool and `/path`'s string, int and nil need
packers; and the response side has envelope decoders but no map reader — a
"walk a map, dispatch on the string keys we want, skip the rest" pass over
`skip_value`, which already recurses arrays and maps. Then `rnsdLinkIdentify`
before the request, because the allow list is checked against the identified
remote identity and an unidentified link is refused.

**How the answer joins.** A path-table row is neither a node's own signed
record nor an inference from our own routing table: it is a third party's
report about itself, pulled rather than pushed, and stale from the moment it
lands. It wants its own evidence class beside `member`/`local`/`routed`, its
own freshness, and a drawing that does not let it be mistaken for a record.

**Cadence: operator-initiated only.** This is pull traffic over somebody
else's airtime. A CLI verb and a click on a vertex in NetGraph, never a
background poll — a device that quietly refreshes every neighbour's tables on
a timer is exactly what gets its identity hash removed from the allow list.

## Implementation order

Each phase leaves the device build green and demonstrably working; batch the
edits within a phase and build once.

1. **Own record + announce.** Builder, seq handling, store (own record only),
   `RNSD_DEST_ANNOUNCE` publishing, the `rnsdHostedDestsForEach` addition,
   the `rnsdAnnounceName`-vs-0xF5 check, aspect-label entry, CLI
   `netgraph dump` for self. Verify: second device's `netgraph.discovery`
   announce visible in logs; dump shows a sane record.
2. **Ingest + resolver + rows.** Announce fan-out subscription, store with
   horizon/eviction, resolver with local overlay, `netgraph.*` rows, full CLI.
   Verify: two devices, each shows the other's record and resolved rows
   without any Channel traffic.
3. **Browser.** `buildGraph` rewrite, stub vertices for unresolved edges,
   README update. Verify: NetGraph renders a three-device community from one
   device's browser, including a link the viewing device cannot hear.
4. **Sync.** Channel server + engine, boot backfill, periodic beat, targeted
   fetch of abridged records. Verify: cold-booted device converges without
   waiting for announce beats; a >8-link interface transfers whole.
5. **Detail contributions.** `netgraphContributeIface`, iface-lora's callback,
   detail text surfaced in the browser node panel.

Test rig: three boards on the bench (two LoRa-linked, one TCP-linked works
well), logs for the protocol, the browser for the result, `netgraph dump` on
each device for the stores. Build flashable images with
`spangap make-builds hw-<board>` from `builds/rop`.
