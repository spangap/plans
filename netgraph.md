# NetGraph — routing truth locally, remote management for the rest

The graph has two sources and no others.

**What this node knows, for free.** Its own path table and its own interface
state: every destination we hold a route to, the neighbour we route it
through, and every peer our radios hear whether or not routing uses it. No
protocol, no traffic, and the drawing changes the moment our state does.

**What other nodes know, when asked.** Reticulum's own remote-management
service — `/path` and `/status` on `rnstransport.remote.management` — visited
node by node, once per crawl, started by a human. It works against stock
Reticulum installations, which is the whole point: it reaches the nodes whose
software we do not write, and it is the same facility we serve to them.

Records are never announced. The self-report record, its store and its
Channel sync stay in the tree and keep working over a Link, but a record
flooded per node per announce beat does not scale on LoRa (Long Range radio),
and until there is a relay or a central distribution point for network data
the crawl is how the rest of the graph gets filled in.

Audience: the implementer, working in this repo. File pointers are to code
that exists today; read the referenced headers before starting.

## Design invariants

These were settled deliberately; do not re-open them casually.

- **One writer per record.** A record is one node's self-report about itself:
  its identity, name, interfaces, links, announced destinations. No node ever
  writes into another node's record. "Newer seq wins" is the entire conflict
  story. Records are one evidence class among four in the drawing, not the
  drawing's foundation.
- **Records are atomic wholes.** Ingest replaces everything held for an origin
  in one step. There are no partial or incremental record updates, ever —
  this is what makes unsigned re-serialization by relays safe, and what lets
  a record's internal references stay record-scoped.
- **Records are unsigned.** They travel over encrypted Links between community
  members and carry no signature of their own. A member can fabricate; a
  signature never prevented that. Consequence: records must never be handed
  onward to a party that does not trust the whole community.
- **A solid line is a route.** Line style states the evidence class and
  nothing else: solid = this node's RNS (Reticulum Network Stack) path table
  says so, thin white = a route two hops out, dashed = an interface hears or
  holds the peer but routing does not use it. No style ever means "old".
- **We never draw a return path we have not been told about.** A line reaches
  from the node that reported it towards the peer and stops short: the peer's
  own view of the same adjacency is a separate fact, and arrives only when
  that node is visited.
- **One visit per node per crawl.** The crawl opens one Link to a node, asks
  its two questions, closes it, and does not come back until a human asks
  again. A device that quietly refreshes a neighbour's tables on a timer is
  what gets its identity hash removed from an allow list.
- **Community membership is a key, not a roster.** One community keypair,
  derived from a name and a passphrase, admits every node that holds it — no
  bilateral exchange, nothing per-peer to configure. Individually granted
  identity hashes sit beside it for nodes outside the community.
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

One path moves records: a Channel-based exchange between two nodes.

```
sync (on demand, over one Reticulum Channel between two nodes):
  I → R   DIGEST       every (origin, seq) I holds
  R → I   RECORD_PART* records I lacks or holds older
  R → I   WANT         origins R lacks or holds older
  I → R   RECORD_PART* those records
  both    DONE         then the initiator closes the channel
```

The push path is disabled, and the sync path goes dormant with it: with no
announce, nothing on the mesh holds a path to a peer's `netgraph.discovery`
destination, so there is nobody to open a Channel to. Mothball the record
subsystem as one unit — comment out the `RNSD_DEST_ANNOUNCE` hand-off in the
builder, the announce fan-out subscription that ingests records, and the boot
and periodic sync triggers, each with a one-line note pointing here.

What stays: builder, store, resolver and the Channel server compile and stay
correct, our own record is still built and still shown by `netgraph dump`, and
the graph loses nothing it did not already get from routing and interfaces.
Restoring the flood is uncommenting those call sites, which is why they are
comments and not deletions.

### Why these primitives

- The sync path uses `rnsdChannelOpen` (client) and `rnsdDestOpen` +
  `rnsdDestListenChannels` (server), both existing (rnsd.h). Channel messages
  are reliable, in-order, and typed (`[msgtype:2 BE][payload]`), which is
  exactly the digest/want/records shape. The request/response server rnsd
  lacks is being added for remote management, not for this — records need a
  stream, not a request.

### Channel message types

The block is 0x4e00 ("N") — the existing `NG_MT_*` values (netgraph.cpp:104),
clear of `RNS::Channel::MSGTYPE_RAW` (0x0100), rnsh's 0xac00 block, and the
≥ 0xf000 range upstream reserves for system types:

| msgtype | name        | payload |
|---------|-------------|---------|
| 0x4e00  | DIGEST      | `total:u16 LE \| offset:u16 LE \|` entries, each `origin:16 \| seq:u32 LE`. Chunk when > channel MDU (maximum data unit, 425 B at the stock MTU; the implementation packs 19 entries per message). |
| 0x4e01  | WANT        | `count:u8 \|` count × `origin:16` |
| 0x4e02  | RECORD_PART | `origin:16 \| seq:u32 LE \| parts:u8 \| part:u8 \|` record bytes. `parts`=1 is the normal case; reassemble in order (Channel guarantees order), apply on last part. |
| 0x4e03  | DONE        | empty. Each side sends when it has nothing further; initiator disconnects after sending and receiving DONE, or on timeout. |

Digests use the full 16-byte origin hash: a false match here silently loses an
update, and the cost is only ~20 B per community node on an exchange that runs
at most once per boot against one neighbour.

## Record format

### Pipe-text form (the specification)

A record is UTF-8 lines, fields separated by `|`. Line order: `n`, `dt`, then
`if`/`ln` pairs per interface, then `up` lines, then any detail lines.

```
n|Kitchen T-Deck|t
dt|3|a1b2c3d4 9f3e2a11 77ab01cd
if|lora|radio0|868.5|7|125|s
ln|radio0|37|9f3e2a11.0.t 8ab2c3d4.1 7c1d99f0.2 ...
if|tcp|wan0
ln|wan0|1|55aa66bb.0.t
up|tcp|wan0|dublin.connect.reticulum.network:4965
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
  (all of them in the full record, the K freshest in the abridged form —
  count > cells is itself the "fetch the rest" signal). Cell subfields are
  dot-separated: `prefix.freshbucket[.t]` — 4-byte destination prefix of the
  peer, freshness bucket **relative to the record's own timestamp** (0 ≤ 5
  min, 1 ≤ 1 h, 2 ≤ 6 h, 3 older), `t` = that peer is a transport node.
  Only identity-bearing peers (ones that have announced) get cells; a peer
  known only by transport address is counted but not listed — no other node
  could join it to anything anyway.
- `up` — one per uplink: interface class, instance name, transport-address
  label, then detail where present. An uplink is an interface's far end that
  is outside the community — it never announces, so it has no prefix and can
  never be an `ln` cell; keeping it a line of its own is what stops a
  resolver joining the outside world to a member.
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
| 0x05 | `up` | UTF-8 text of the pipe line after the tag word, verbatim |
| ≥0x20| detail | UTF-8 text of the whole pipe line verbatim |

`str` = `len:u8 | bytes`. Tags 0x06–0x1f are reserved for future core lines;
an unrecognized tag's line is carried by its length and republished as text
where possible, hex otherwise.

The magic byte 0xF5 is an invalid UTF-8 lead byte, so nothing that sniffs a
payload for a display name mistakes a record for text. It costs one byte and
survives whoever restores the flood. `netgraph.discovery` is already in the
aspect table behind `rnsdAspectLabel` (rnsd_peers.cpp:79); add
`rnstransport.remote.management` beside it — that one is read constantly by
the crawl.

### Size expectations

Header 22 B; a typical record (name, 3 dests, two interfaces, 8 link cells)
lands near 150–200 B packed, which would sit inside announce `app_data` on
LoRa if it were ever announced. A dense LoRa channel (37 peers → 37 five-byte
cells) makes the full record ~350 B — still one Channel message, and still the
reason the abridged form exists.

## The on-device component

Client component in the `rns` straddle: `esp-idf/src/netgraph.cpp` +
`include/netgraph.h`, registered via `rnsServiceRegister("netgraph", …,
RNS_PHASE_CLIENT)`, gated by `s.netgraph.enable`. It owns six things — the
four below, plus the remote-management server and the crawl, both of which
have their own sections.

### 1. Record builder

Sources, all existing:

- Own identity hash: `rnsdIdentityHash` on rnsd's default identity
  (`secrets.rnsd.identity`).
- Own announced destinations for `dt`: `rnsdHostedDestsForEach` (rnsd.h:523),
  which the builder already uses.
- Interfaces for `if` lines and links for `ln` lines:
  `rnsdNodesForEach` / `rnsdPeersForEach` (rnsd.h:458+). Group peers by node;
  an attributed node's cell prefix is its freshest announced destination; an
  unattributed radio peer is its own cell keyed by its destination prefix.
  Interface class = registered name up to `/` or `_` (the existing
  `ifaceClass`, netgraph.cpp:164; the browser gets `cls` precomputed).
- Own transport flag: rnsd's transport-enabled setting (grep `s.rnsd.` in
  rnsd.cpp for the key).
- Display name: `s.net.hostname`, else `s.lxmf.id.0.display_name` — the
  order the format section's `n` bullet states.
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
any received one. The `RNSD_DEST_ANNOUNCE` hand-off is commented out — the
record leaves this node only over a sync Channel. The abridged form
(`s.netgraph.announce_cells`, default 8, freshest cells per `ln`, abridged
flag when anything was cut) is still built into the announce buffer, so
restoring the flood stays an uncomment — but nothing sends it while the flood
is mothballed; a sync always serves the full stored record.

### 2. Record store

Heap store, RAM only, capped by `s.netgraph.store_kb` (default 24): per
origin, the packed record bytes plus `{seq, received_at}`. No flash
persistence — a rebooted node backfills (~3 KB compressed-equivalent over one
link) faster than flash-wear accounting is worth; revisit only if boot-time
LoRa-only backfill proves painful.

- Ingest (from RECORD_PART): magic/shape validate, reject seq ≤ stored —
  keeping the existing equal-seq exception that upgrades an abridged copy to
  a full one, dormant while nothing abridged circulates — and reject records
  whose seq (timestamp) is older than
  `s.netgraph.horizon_h` (default 24) — stale records are neither stored nor
  offered in digests.
- Expiry sweep on the same slow beat as sync: drop past-horizon records and
  unpublish their rows.
- Eviction under the byte cap: stalest `received_at` first. Own record is
  never evicted.

### 3. Resolver → published rows

Four inputs, one row schema. They differ only in `ev`, the evidence class,
which is the single thing that decides how a line is drawn. Re-resolve and
republish whenever any of them changes; the three local ones change with our
own state, so the drawing is current without anything having been asked.

**Routing, one hop.** `rnsdDirForEach` (rnsd.h:553) already yields
`{dest, identity, via, iface, hops, have_route}` for every destination this
device has ever heard. Each routed entry with `hops == 1` is an edge from us
to that node, `ev=route1`, `cls` from the class word of `iface`. Solid, in
the interface class's own colour. This is the only thing a solid line ever
means.

**Routing, two hops.** `hops == 2` is an edge from the node named by `via` to
that node, `ev=route2` — it hangs off the neighbour we see it behind, not off
us. `via` is a next hop's transport id: resolve it against the directory's
identities and destinations; where it names nothing we hold, hang the edge on
a stub keyed by the `via` hash. Drawn thin and white, with no class colour,
because we do not know that hop's medium — our `iface` names the interface
*we* transmit on, not the one the via-node used.

Beyond two hops our own table says nothing worth drawing: the intermediate
chain is not in it. Those nodes appear when something reports them.

**Interfaces.** A peer that our lora, auto or ble interface holds a connection
to (auto, ble) or simply hears (lora), and for which no `route1` edge exists,
is an edge from us to it, `ev=heard`, dashed, `cls` from the interface.
`rnsdNodesForEach` / `rnsdPeersForEach` (rnsd.h:458+) are the source. These
are the nodes we could be one hop from and are not — a faster parallel link
carries the route instead, or nothing has routed through them yet — and
saying so is the one thing this device knows that no report will ever carry.
A peer whose last-heard exceeds `s.netgraph.heard_h` (default 3) is not drawn
at all. There is no aged style: evidence expires and the line leaves.

**The crawl.** A visited node's `/path` answer gives its own `route1` and
`route2` edges with `a` anchored at that node instead of at us; its `/status`
answer gives its interfaces. Same rows, same styles.

**Records**, where a sync has run, contribute `ev=record` edges as before.

Precedence for the same adjacency: `route1` beats `heard`; a record never
overrides a route. **One published row per `(a, b, cls)`**, carrying the
strongest `ev` held for it — not one row per `(a, b, ev)`. The precedence order
is a fold rule, so it has to apply to the rows and not merely to the prose: a
pair joined by a route and also heard on the same medium is one line, and
publishing both would draw the dashed one over the solid one for every
neighbour we route through.

The cheap case for the fold is that it bounds the row count by adjacency rather
than by evidence: N nodes at mean degree d publish 2·N·d rows however many
classes happen to corroborate each pair, which is the number the caps below are
sized against.

Both directions of an adjacency stay separate rows. Until the far end has
reported the reverse, the renderer draws from `a` and stops short of `b`: we
know how we reach it, not how it reaches us, and a line that touches both
circles would claim we do.

Published under `netgraph.*` (ephemeral keys — RAM-tier, browser-synced
automatically via the storage DataChannel; see storage.h header comment).
Follow the slot conventions of `rnsd.nodes.*` / `rnsd.peers.*`
(rnsd_peers.cpp:563 onward):

```
netgraph.self                own identity hash, hex
netgraph.radius              community radius in force
netgraph.nodes.slots         walk bound
netgraph.nodes.<i>.id        identity hash hex ("" = known only by address)
netgraph.nodes.<i>.name      display name (may be "")
netgraph.nodes.<i>.label     transport-address label where there is no name
netgraph.nodes.<i>.transport 0/1
netgraph.nodes.<i>.dist      hops from us; 0 = us
netgraph.nodes.<i>.member    1 = its management announce carried a community signature
netgraph.nodes.<i>.visited   unix seconds of the last crawl visit, 0 = never
netgraph.links.count
netgraph.links.<j>.a         node slot of the reporting side
netgraph.links.<j>.b         node slot of the peer, -1 unresolved
netgraph.links.<j>.bref      peer hash hex, when b = -1
netgraph.links.<j>.ev        route1 | route2 | heard | record
netgraph.links.<j>.cls       interface class word ("lora"); "" for route2
netgraph.links.<j>.iface     reporting side's interface name ("radio0")
netgraph.links.<j>.age_s     seconds since this evidence was last refreshed
netgraph.links.<j>.transport 0/1 (peer side)
netgraph.links.<j>.src       "" when this is our own evidence; the crawled
                             node's identity hash where a visit produced it
netgraph.ifs.<i>...          per-node interfaces: class, name, detail text verbatim
```

`src` exists because `a` does not answer "whose statement is this". `a` is the
reporting side, and for `route2` the reporting side is the via-node even when
we derived the row from our own path table — so a locally-derived `route2` and
one the crawl pulled out of that same neighbour publish identically without it.
Those are different claims: one is our table saying a node sits behind a
neighbour, the other is the neighbour's own table saying so, asked once and
stale from the moment it landed. Empty means ours.

`fresh` and `stale` go: nothing is styled by age, and evidence that has
expired is removed rather than dimmed. Publish inside a
`storageBegin()`/`storageEnd()` bracket so the browser sees one coalesced
patch, and publish always — the rows ARE the API.

### 4. Sync engine

- Server: `rnsdDestOpen("netgraph.discovery", "", SINGLE)` once at start, then
  `rnsdDestListenChannels(handle, NETGRAPH_SYNC_PORT)`. Serve DIGEST/WANT per
  the protocol section; the server side also uses the initiator's DIGEST to
  push what the initiator lacks.
- Boot backfill, periodic anti-entropy and the targeted single-WANT fetch are
  commented out with the announce. They need a path to a peer's
  `netgraph.discovery` destination and no announce supplies one.
- `netgraph sync <hash>` is the one way left to run an exchange by hand
  (today's argument-less `sync` only kicks the beat's own partner selection —
  give it the argument): derive the peer's destination from an identity hash
  we hold and fail cleanly with no path, which is the expected outcome until
  a relay exists.
- Partner selection, when this comes back: hops==1 nodes, preferring one whose
  destination sits on a non-LoRa interface (cross-check against rnsd's peer
  rows for the iface name), else any.

Channel MDU discipline: every message ≤ MDU; digests chunk by `total/offset`;
records chunk by RECORD_PART. One sync in flight at a time, modest timeout,
fail-and-retry-next-beat — never block anything on a sync.

## Interface detail contribution

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

`buildGraph` in `browser/src/lib/netGraph.ts` reads `netgraph.*` rows only —
no join logic, no reading `rnsd.nodes.*` / `rnsd.peers.*` there. Vertices from
`netgraph.nodes.*`, edges from `netgraph.links.*`, per-node interfaces from
`netgraph.ifs.*`. Unresolved edges (`b` = -1) render to a small unlabelled
stub vertex so degree stays visible. Layout, parallel arcs and caption
placement (`forceLayout.ts`, `NetGraphWindow.vue`) are untouched.

Line style is a pure function of `ev`, and nothing else styles a line:

| `ev` | width | colour | dash | reaches `b` |
|---|---|---|---|---|
| `route1` | 2 | `rns.pill.<cls>.color` | solid | only once the reverse row exists |
| `route2` | 1 | white | solid | only once the reverse row exists |
| `heard` | 2 | `rns.pill.<cls>.color` | dashed | same rule |
| `record` | 2 | `rns.pill.<cls>.color` | solid | both ends reported by construction |

The reach rule is the visible half of an invariant: an edge whose reciprocal
row is absent is drawn from `a` and stopped a vertex-radius short of `b`, with
no arrowhead — the gap says "this is how `a` gets there; how `b` gets back is
not known". When both rows are present the two merge into one line that
touches both circles, as today.

The "inferred from routing" styling and the `stale` dash both go (today they
are one and the same `4 4` dash, NetGraphWindow.vue:347): routing *is* the
solid line now, and expiry removes rather than dims. `oneWay`, `inferred` and
`fresh` leave `GraphEdge` (`stale` sits on `GraphNode`); `ev` and `age_s`
arrive.
Hover text keeps the evidence in words — "1 hop, radio0" / "2 hops via
Kitchen" / "heard on radio0, not routed" — because the styles are only
distinguishable once someone has been told what they mean.

Update the NetGraph section of `rns/README.md` to match: what the picture
draws, in the same four classes, and where each comes from.

## CLI

One `netgraph` verb on the rns straddle's command (pattern: `rnsdPeersCli`,
rnsd.h:493):

- `netgraph` — summary: nodes and links by evidence class, community radius,
  last crawl, how many nodes it reached and how many refused.
- `netgraph l[inks]` — the resolved graph, one line per edge with its `ev`.
- `netgraph c[rawl] [<hash>]` — run a crawl now, or visit one node only.
- `netgraph d[ump] [<prefix>]` — records expanded to pipe text.
- `netgraph s[ync] <hash>` — run a record exchange by hand.
- `netgraph r[ebuild]` — force own-record rebuild.

### `-R` on the path and status verbs

The straddle's own path and status verbs take the stock switches, so the
muscle memory from `rnpath` / `rnstatus` transfers unchanged:

- `-R <identity hash>` — ask that node instead of ourselves. The destination
  is derived the stock way, `hash_from_name_and_identity("rnstransport.remote.management", <hash>)`,
  so the argument is a transport identity hash exactly as upstream takes it.
- `-i <who>` — which identity to identify with: `community` (the default when
  a community key is configured), `node` for our own transport identity, or a
  named community from `s.netgraph.communities.*` once there is more than one.
  Getting this wrong is the overwhelmingly likely cause of a refused request,
  so name the identity used in the failure line.
- `-w <seconds>` — give up. Default `RNS.Transport.PATH_REQUEST_TIMEOUT`, 15.

`path -R` prints the same columns as a local path table; `status -R` prints
the same block as local status, minus the keys the answering node omitted. A
missing key prints as `-`, never as a zero — upstream's own `rnstatus` guards
most keys with an `in` check and we owe the reader the same distinction
between "nothing" and "none".

## Settings

All under `s.netgraph.`, seeded with `storageDefaultTree`:

| key | default | meaning |
|---|---|---|
| `enable` | 1 | run the component |
| `heard_h` | 3 | a peer unheard for this long stops being drawn |
| `radius` | 2 | community radius: how many hops out the crawl goes |
| `crawl_timeout_s` | 20 | give up on one node and move to the next |
| `serve` | 1 | answer remote management (see below) |
| `community` | `""` | community name; empty disables the community key |
| `passphrase` | `""` | the community's passphrase; with the name, derives the key |
| `allow` | (collection) | identity hashes granted alongside it, one per entry |
| `rebuild_floor_s` | 600 | min seconds between own-record rebuilds |
| `announce_cells` | 8 | max link cells per `ln` line in the abridged form |
| `link_horizon_h` | 6 | a link older than this leaves the record |
| `horizon_h` | 24 | records older than this are dropped and never offered |
| `sync_min` | 30 | anti-entropy period, minutes (dormant) |
| `store_kb` | 24 | record-store byte cap |

The passphrase is an ordinary setting, edited in the NetGraph settings pane
like any other. Every node in the community holds it, so a tier that hid it
from the operator who has to type it into the next node would buy nothing. The
DERIVED key is the secret — `secrets.netgraph.identity`, persisted, never
synced to the browser — because that is an actual private key.

`allow` is a collection rather than a comma-separated string: the pane binds
rows to it, and every mutation arrives on a `netgraph.allow.add` /
`.remove` sentinel that validates before writing, so a malformed hash cannot
reach the list by any route. `s.netgraph.seq` is state; don't seed it.

The community identity hash is published to `netgraph.community.id` once
derived, so the pane can show it with the one instruction an operator has to
carry elsewhere: a stock Reticulum node knows nothing about communities, and
what it understands is a hash in its `remote_management_allowed`.

## Traffic budget (sanity ruler, not a spec)

A crawl of a community of ~40 within radius 2 costs one Link per reachable
node, each carrying a few-byte REQUEST and a `/path` answer of ~30 B per
routed destination plus a `/status` answer of a few hundred bytes to a couple
of kilobytes as a Resource. That is the whole recurring cost, and it recurs
only when somebody presses a key. The local half costs nothing at all.

The number to watch is per-node `/status` size against LoRa: a busy node's
interface list is the one answer that can take a minute to arrive on a slow
link, which is why the crawl asks `/path` first, publishes what it got, and
treats `/status` as the part it may not finish.

Airtime is not the ceiling that binds first, though — the tables are. Three
counts have to hold at ~30 nodes, and they fail differently:

- **Edges.** 30 nodes at mean degree 4 is 120 adjacencies and 240 directed
  rows, both directions staying separate by design. `NG_MAX_LINKS` is 192, so
  even the well-behaved one-hop case overflows today. Raise it, and make the
  truncation deterministic — see below.
- **The directory walk.** `NG_MAX_DIR` is 96. Thirty nodes announcing four
  destinations each is 120, so a quarter of this node's OWN evidence is
  dropped before the resolver sees it. This is a plain undersize.
- **Vertices.** A `/path` answer carries `{hash, timestamp, via, hops,
  expires, interface}` with no identity hash in it, so a remote node's four
  destinations fold into one vertex only where we hold announces for all four.
  Where we do not, one node arrives as up to four stubs that look like nodes
  and are not. Fold what can be folded, cap what cannot, and prefer a stub
  labelled by prefix over four of them.

## Remote management

Both halves of Reticulum's own facility, as close to stock as we can get: we
ask stock nodes, and stock nodes can ask us.

```
 as client — us → any node running stock Reticulum
   │  ← ANNOUNCE rnstransport.remote.management   (theirs, every 2 h, carries the key)
   │  ─ LINK ────────────────────────────────────►
   │  ─ IDENTIFY (community identity, or ours) ──►   checked against their
   │                                                 remote_management_allowed
   │  ─ REQUEST /path   ["table", nil, 1] ───────►
   │  ◄ RESPONSE [{hash, timestamp, via, hops, expires, interface}, …]
   │  ─ REQUEST /status [true] ──────────────────►
   │  ◄ RESPONSE [stats-dict, link-count]            (a Resource when large)
   │  ─ CLOSE ───────────────────────────────────►   one visit, one Link

 as server — any RNS installation → us
   │  ← ANNOUNCE rnstransport.remote.management   (ours, on the stock 2 h beat,
   │                                               app_data = community signature)
   │  ─ LINK ────────────────────────────────────►
   │  ─ IDENTIFY (their identity) ───────────────►   checked against the community
   │                                                 identity hash ∪ s.netgraph.allow
   │  ─ REQUEST /path | /status ─────────────────►
   │  ◄ RESPONSE (the same shapes, from rnsd's own tables)
```

The address is the stock one: `rnstransport.remote.management` on this node's
own transport identity, so `rnstatus -R <our transport identity hash>` from an
unmodified `pip install rns` works with nothing on the other side but that
hash in a config file. The community key is what *identifies*, never what
addresses — building the destination on it would give every community node the
same destination hash, and a management query is always about one specific
node.

### Serving it

rnsd has no request/response server. µR itself has the whole mechanism —
`Destination::register_request_handler` with ALLOW_LIST gating, dispatched
from `Link::handle_request` (Link.cpp:1023) — but nothing in rnsd calls it.
Add the bridge — an
`rnsdDestListenRequests(handle, path, cb)` beside `rnsdDestListenChannels`,
the callback handed the request path, the unpacked argument list and the
identified remote identity, returning packed response bytes or nothing. That
is the largest single piece of work here; everything else in this section is
small.

Then two handlers, upstream's shapes exactly:

- `/path` — argument `[command, dest-hash|nil, max-hops|nil]`. `"table"`
  answers a list of `{hash, timestamp, via, hops, expires, interface}` built
  from `rnsdDirForEach`, filtered by dest hash and max hops on our side, not
  the caller's. `"rates"` answers an empty list — we keep no announce-rate
  table. Know that a stock remote client misreports this: rnpath treats any
  falsy answer as "The remote request failed. Likely authentication failure."
  (rnpath.py:316), and an upstream node with an empty rates table trips the
  same line, so there is nothing better to answer.
- `/status` — argument `[include-link-count, include-profiling]`. Answers
  `[stats-dict]`, appending the link count when asked, never profiling.

**Which `/status` keys are mandatory.** Upstream's `rnstatus` guards most keys
with an `in` check but not all, and an unguarded miss raises instead of
degrading. Derive the required set mechanically before writing the serializer:
the keys `rnstatus.py` subscripts on `ifstat` and `stats`, minus the keys it
guards. Unguarded per-interface: `name`, `status`, `mode`, `clients`, `rxb`,
`txb`, `txdrp`, `txbuffered`, `txstalled` (`short_name`, `hash`, `type` are
never subscripted; `peers` is guarded). The guards also cross keys — sending
one key makes another mandatory: `bitrate` pulls in `mtu`;
`rxs`/`txs`/`prxs`/`ptxs` together pull in `arxs`/`atxs`;
`incoming_announce_frequency` pulls in `outgoing_announce_frequency` and
`arxc`/`atxc`; `incoming_pr_frequency` likewise for the path-request set;
`ifac_signature` pulls in `ifac_size` — ship a family whole or not at all.
Top-level: `interfaces` always; `rxb`, `txb`, `rxs`, `txs` under `-t`;
`rxpps`, `txpps` under `-p`; the whole `-q` queue-counter set is unguarded, so
supply it as zeros.
Zero is a fine value for a counter we do not keep; omitting a key upstream
does not guard is not.

**Access control** is upstream's `ALLOW_LIST`: the identified remote
identity's hash must be the community identity hash or appear in
`s.netgraph.allow`, and an unidentified link is refused outright. The list is
per handler, so `/path` and `/status` can be granted separately if they ever
need to be — one is a map of everywhere we can reach, the other is counters.
Gate the destination on `s.netgraph.serve`, default on.

### The community identity

`Identity::load_private_key` takes 64 bytes: 32 X25519 followed by 32 Ed25519.
Derive them from the community name and passphrase and the keypair is
reproducible on every node that knows both, with nothing exchanged and nothing
per-peer:

```
salt = "netgraph-community:" ‖ s.netgraph.community
key  = PBKDF2-HMAC-SHA256(s.netgraph.passphrase, salt, iterations, 64 B)
```

mbedTLS ships PBKDF2 (Password-Based Key Derivation Function 2) on ESP-IDF as
`mbedtls_pkcs5_pbkdf2_hmac_ext` — the un-suffixed variant is compiled out,
IDF's mbedTLS config sets `MBEDTLS_DEPRECATED_REMOVED`. Choose the iteration count from what the slowest
supported board can afford once at boot, and write the measured figure in the
comment rather than a round number. The slowness is the point: a
passphrase-derived identity is only as strong as the passphrase against an
offline attack, and its hash becomes public the moment we announce.

`rnsdLinkIdentify` and `rnsdDestOpen` take a storage key, not key bytes, so
the derivation lands the 64 bytes as 128 hex characters in
`secrets.netgraph.identity` — secrets tier: persisted, never synced to the
browser — and that key name is what identifies as the community.

Say this plainly wherever the setting is edited — **the passphrase is the
community's access credential**, not a network name. Anyone holding it can
query every node that trusts it and can identify as the community to third
parties. The NetGraph settings pane carries that sentence beside the field.

Beside it the pane shows the derived identity hash, with the one instruction
an operator has to carry elsewhere: *ask remote nodes that do not have NetGraph
to enable network management and to allow access to this identity to make them
answer queries by this community.* A stock Reticulum node knows nothing about
communities; a hash in `remote_management_allowed` is what it understands.

`s.netgraph.allow` is the other half: identity hashes admitted individually,
for a node outside the community whose operator we know. Same 32-hex-character
form as stock `remote_management_allowed`, so a line copies straight across in
either direction.

### Asking

The client half is built and was written for this: `rnsdLinkRequest`
(rnsd.h:847) names `rnstatus -R` / `rnpath -R` in its own docs, its
inline-payload limit binds the REQUEST (ours is a few bytes) and not the
response, Resource-delivered responses conclude through
`Link::response_resource_concluded`, and `unpack_blob_or_object` hands a
structured msgpack response up verbatim instead of demanding a bin.
`rnsdLinkIdentify` (rnsd.h:808) goes before the request.

What is missing is small, and the server half needs the same pieces: the
MsgPack shim's variadic overload set packs only `double`, `bin` and `Bytes` —
`detail::` already holds uint, fixstr, nil and map-header packers that
Resource.cpp calls directly — so bool needs writing and the rest need
surfacing; and the response side has envelope
decoders but no map reader — a "walk a map, dispatch on the string keys we
want, skip the rest" pass over `skip_value`, which already recurses arrays and
maps.

Discovery costs nothing. Management destinations announce on the stock 2-hour
beat, so every node offering the service advertises itself with its key:
subscribe the announce fan-out to `rnstransport.remote.management` and each
one arrives recallable and ready for `rnsdLinkOpen`, with no hash derivation
and nothing configured. The announce table evicts by memory pressure, never
by time (Directory.h:106): an unclaimed announce sits early on the eviction
ladder, so recalling a 2-hourly announce reliably means claiming it —
`rnsdClaim` with a new consumer slot beside the existing `RNSD_CLAIM_*` set.

Community membership rides in that announce's `app_data`: a community-key
signature over the announcing node's own identity hash and an issue time.
Upstream announces this destination with no `app_data` and stock clients
ignore whatever is there, so adding it breaks nothing — and a node holding
only the community *public* key can verify membership without being able to
claim it. That signature is what sets `netgraph.nodes.<i>.member` and what
tells the crawl who is inside the community radius.

## The crawl

One key press, one pass, one Link per node.

```
 gather   nodes at distance ≤ s.netgraph.radius from local evidence only:
          route1 and route2 vertices, heard peers, and every node whose
          management announce carried a valid community signature
 visit    each, once, in distance order:
            LINK → IDENTIFY(community) → /path ["table", nil, 1] → publish
                                       → /status [true]          → publish
            CLOSE
 extend   a visited node's route1 answer adds vertices; those inside the
          radius join the queue for this same pass
 stop     queue empty, radius reached, or the operator interrupts
```

Rules that are not negotiable:

- **Exactly once per node per crawl.** Keep a visited set for the pass; a node
  reachable two ways is still one visit.
- **One hop per node, never more.** `["table", nil, 1]`, filtered by the node
  answering. Two hops is right for OUR table, where it is free; it is the wrong
  thing to ask of thirty other nodes. A node's one-hop answer is an adjacency
  list — O(degree) — and the union over a crawl is O(N·degree). Its two-hop
  answer is a reachability table, and reachability tables are quadratic: on a
  30-node mesh of mean degree 4 each node reaches ~16 others within two hops,
  so the crawl folds ~500 edges out of ~2000 path rows to learn what ~120
  adjacencies already said. The information is not even lost — a node inside
  the radius gets visited anyway, and its own one-hop answer supplies the edge
  its neighbour's two-hop answer would have inferred. What one hop gives up is
  the frontier: nodes adjacent to a node we chose not to visit. Those are
  exactly the ones we can never corroborate, and drawing them would put the
  least reliable evidence on the map at the greatest distance.
- **`/path` first, then `/status`.** The routes are the graph; the interface
  list is decoration that may not arrive over LoRa. Publish after each, so a
  crawl that dies half way still leaves the picture better than it found it.
- **Failure is normal and quiet.** No path, refused identify, timeout: count
  it in the summary, leave `visited` as it was, move to the next node. One
  refusal must never stall a pass.
- **Never automatic.** `netgraph crawl` and a button in the browser panel, and
  nothing else. No timer, no boot pass, no refresh-on-idle. This is pull
  traffic over somebody else's airtime, and a device that quietly re-reads a
  neighbour's tables on a schedule is what gets its hash removed from an allow
  list.

Crawl results carry the node they came from and expire on `s.netgraph.heard_h`
like everything else. A path-table row from another node is neither its signed
record nor an inference of ours: it is a third party's report about itself,
pulled rather than pushed, and stale from the moment it lands.

## Deferred — designed for, not built now

- **Relaying or centralising network data.** What would let records flow again
  without a flood per node per beat. Until it exists the crawl is the
  distribution mechanism and the record subsystem stays mothballed.
- **Live drill-down queries** (RSSI/SNR, SUPE budgets, counters for one node
  or link): new Channel msgtypes on the netgraph destination, answered from
  live tables, never stored.
- **Per-node attribution inside the community.** Every member identifying with
  the same key means a server cannot tell which member is asking — no per-node
  audit, no per-node rate limit. The fix is members identifying with their own
  key and proving membership with a community-signed certificate on an `/auth`
  path, which leaves upstream's contract behind; build it when something needs
  it.
- **Interface detail contributions.** `netgraphContributeIface` and
  iface-lora's callback, per the section above. It feeds the record, which is
  mothballed; revisit with the relay.
- **Store persistence across reboot**: only if measurement demands.

## Considered, not settled

Things the scale work above turned up that do not block the phases below. None
is urgent; all of them get worse rather than better as the community grows, so
none should be forgotten either.

- **Deterministic, prioritised truncation.** When the edge table fills, what
  gets dropped is currently whatever the passes happened to reach last — so a
  cap silently eats an arbitrary third of the graph in run order. It should
  drop in a stated order (local evidence before crawled, freshest first) and
  say in the log what it dropped and why. A smaller graph you can reason about
  beats a larger one you cannot.
- **Diffed publish.** Every re-resolve rewrites every row: ~240 edges × 8 keys
  plus vertices is ~2400 storage writes into a browser-synced tier, and
  re-resolve fires whenever any input changes. Keeping the previous resolved
  set and writing only what changed takes steady state to approximately zero.
  This is the one that degrades continuously rather than truncating, which is
  why it is easy to miss and unpleasant to live with.
- **Uplinks have no evidence class.** The four classes are all adjacencies
  between nodes, and the row schema has no `kind`, so the `up` line — which
  stays in the record format, and which the record builder still emits — has
  nowhere to land in the resolved rows. Uplink rendering is dropped by this
  plan without that being argued anywhere. Either the drawing keeps a fifth
  class for "the far end of a standing connection out of the community", or
  the `up` line should stop being built; carrying it in the format while the
  resolver ignores it is the one state that helps nobody.
- **A shared `/path` decoder.** The crawl and `path -R` decode the same
  response into the same shapes and will drift apart if written twice.
- **Radius as a display question.** `radius` currently says how far to go
  looking for nodes to serve. It is not a filter on what is worth drawing, and
  the two will be confused the first time somebody wants a smaller picture
  rather than a shorter crawl.

## Implementation order

Each phase leaves the device build green and demonstrably working; batch the
edits within a phase and build once.

1. **Mothball the flood.** Comment out the `RNSD_DEST_ANNOUNCE` hand-off, the
   announce fan-out subscription that ingests records, and the boot and
   periodic sync triggers, each with a one-line note pointing at this plan.
   Verify: no `netgraph.discovery` announce on the air, `netgraph dump` still
   shows our own record, nothing else regresses.
2. **The local graph.** Resolver rewritten onto `rnsdDirForEach` plus the
   peer/node walks: `route1`, `route2`, `heard`, the `heard_h` expiry, the new
   `netgraph.*` rows, the `(a, b, cls)` fold, and the cap raises the scale
   section calls for. Verify on the bench rig that a node two hops out draws
   against the neighbour it sits behind, and that unplugging a radio removes
   its lines within the horizon rather than dimming them.
3. **Browser.** `buildGraph` and the renderer onto `ev`; the stop-short
   geometry; delete the `inferred`/`stale`/`fresh` styling; README update.
   Verify: three boards, one of them stock RNS, all four line classes visible
   and distinguishable, hover text saying which is which.
4. **MsgPack both ways.** Packers for bool, string, int and nil; the map
   reader over `skip_value`. Verify with a unit exercise against a captured
   upstream `/status` response before any of it is wired to a Link.
5. **Client `-R`.** `rnsdLinkIdentify` + `rnsdLinkRequest` against a stock
   node, `-R`/`-i`/`-w` on the path and status verbs. Verify against a real
   `rnsd` on the bench with our hash in its `remote_management_allowed`, both
   identifying as the node and as a community.
6. **Community identity.** PBKDF2 derivation, `s.netgraph.community` +
   `secrets.netgraph.passphrase`, the announce `app_data` signature and its
   verification into `member`. Verify: two devices with the same passphrase
   derive the same identity hash; a third with a different one does not and is
   not marked a member.
7. **Server.** `rnsdDestListenRequests`, the `/path` and `/status` handlers,
   the allow list, `s.netgraph.serve`. Verify with unmodified `rnstatus -R`
   and `rnpath -R -t` from a laptop, both granted by community key and by an
   individual hash in `s.netgraph.allow`, and confirm a refused identity is
   refused.
8. **The crawl.** Queue, visited set, distance ordering, radius, per-node
   failure handling, `netgraph crawl`, browser button, `src` on the rows it
   produces. Verify: a crawl over three boards plus one stock node fills in
   links the crawling device cannot hear, and a second crawl visits each node
   exactly once again.

Test rig: three boards on the bench (two LoRa-linked, one TCP-linked works
well) plus a laptop running stock `rnsd`, which is what makes the
compatibility claims testable in both directions. Logs for the protocol, the
browser for the result, `netgraph links` on each device for the resolved
graph. Build flashable images with `spangap make-builds hw-<board>` from
`builds/rop`.
