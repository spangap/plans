# SUPE in iface-lora — implementation plan

> Scope: **how to build [`SUPE.md`](SUPE.md) in `iface-lora`, and what not to
> spend time rediscovering.** SUPE.md is the protocol and stays authoritative for
> anything on the air; this file is the map from it to this codebase — what already
> exists and can be reused, what must be added, in what order, and which of this
> straddle's habits will bite. Phase 0 lands the spec edits this plan depends on,
> so the two documents never disagree while code is being written.
>
> Everything named here was read out of `iface-lora/INTERNALS.md`,
> `esp-idf/src/lora.cpp` and `esp-idf/src/rolling.h`. Where a line number is given
> it was accurate at the time of writing and is a starting point, not a contract.

## 0. One thing that changes the protocol

**On this interface the first on-air byte is the split header, not the Reticulum
flags byte** (`INTERNALS.md` §5): `seq<<4 | split`, whose reachable values are
every byte ending in 0 or 1 — including `0xC0`, `0xC1`, `0xD0` and `0xD1`. That
is why SUPE.md §0.1 assigns no type value ending in 0 or 1, and it leaves the
receive path one clean rule with three branches: **byte 0 in `0xC2`–`0xDF` not
ending in 0 or 1 is a SUPE frame; a byte ending in 0 or 1 is a split header;
anything else is neither, and is discarded.** The third branch is stated on
purpose — discard is SUPE.md §3's designed response to everything unrecognised,
and dispatch is where that discipline starts. Two consequences:

- **SUPE frames are not split-framed.** They carry no split header at all; their
  type byte *is* byte 0. Nothing SUPE sends exceeds ten bytes except ANNOUNCE2,
  whose `5 + 4·count` must simply stay under the single-frame maximum — a bound
  on the bundling count, not a job for the framing.
- **The packets inside a train are split-framed as usual.** The peer counts
  reassembled RNS packets, not LoRa frames, which is what the existing `splitBuf`
  path already produces (`INTERNALS.md` §6).

**Do check the rule still holds if the framing ever changes.** It is the receive
path's whole basis for telling a SUPE frame from a packet, and it is a one-line
assumption sitting between two files.

## 1. Decisions — made here, landed as spec edits first

Phase 0 writes these into SUPE.md before any code exists, so nothing is built
against wording the build has already left behind. Each was an open question in
SUPE.md §16 or a mismatch with this codebase; the resolution and the reason.
The *Edit:* lines name where each change lands, and they are starting points,
not inventories — before the phase 0 commit, grep SUPE.md for every mention of
the thing changed (ANNOUNCE1 and the announce channel, `SUPE.regime`,
`announce_bundling`), because a missed occurrence is exactly the drift phase 0
exists to prevent.

- **`s.lora.<n>.afa` is the regime number; `SUPE.regime` does not exist.** The
  key is already seeded (`storageDefault`, ~line 6209), read into `r->afa`
  (~line 2547), exposed as a dropdown in `straddle.yaml`, and documented in
  `INTERNALS.md` §18.2 in almost the same words SUPE.md §14 uses. `SUPE.enable`
  is the only new gate: off means today's behaviour exactly, on with `afa` 0 is
  regime 0, on with `afa` 1 is regime 1. That costs no migration, keeps the
  shipped dropdown, and `afa`'s existing "unrecognised number resolves to no
  agile channels" is exactly the safe default SUPE.md §4 asks for. One
  contradiction rides along: `lora.cpp` (~104, ~925) and `INTERNALS.md` §18.2
  say "0 is not a regime — it means no agility", while SUPE.md §3/§4 make
  regime 0 a working regime, never renumbered. The reuse survives because
  regime 0 has no channel plan, so `regimeChans(0)` yielding nothing stays
  correct either way — but the comments must say so, or the next reader fixes
  one side against the other.
  *Edit: SUPE.md §3 and §4, both of which name the setting; the code and
  INTERNALS comments in phase 3.*

- **The sender does not sense the candidate channel before offering.** SUPE.md
  §16 left the choice open while §11's escalation wording assumed sensing.
  Choosing not to removes an excursion — retune, read, retune — from the
  transmit path entirely, and loses little: the peer senses before answering
  regardless, and the peer's end was always the end that mattered (SUPE.md §8).
  *Edit: SUPE.md §11's "sensed clear" wording; strike the §16 item.*

- **ANNOUNCE1 and the announce channel are dropped.** SUPE.md §16 already had
  them on notice once no power sweep follows ANNOUNCE2. ANNOUNCE2 goes out on
  the main channel in every regime, as regime 0 always did. That deletes a frame
  type, a channel role and a per-announcement deafness window for every listener
  in earshot, and makes regimes 0 and 1 announce identically; what it costs is
  `5 + 4·count` bytes of shared-channel airtime once per interval per node.
  *Edit: SUPE.md §0, §0.1, §3, §6, §7, §9, §16 — §6 holds ANNOUNCE1's full
  message block and §9 names the frame; `0xC3` becomes reserved, not
  reassigned.*

- **`announce_bundling` is dropped, and the spreading question with it.** With
  ANNOUNCE2 on the main channel, an unbundled announcement spends a preamble
  and a frame of shared-channel airtime per identity where bundling spends one
  for all of them — unbundling now costs strictly more for every party, and
  the one piece of machinery it still needed, how unbundled frames spread in
  time, was itself an open §16 item. One frame per interval, every identity in
  it. *Edit: SUPE.md §4, §7, §16.*

- **`SUPE.sender_ident` is marked deferred.** v1 parses and honours the
  10-byte START but never transmits it (§10), and the setting governs sending
  only — left unqualified, SUPE.md §4 would describe a knob v1 ships and
  ignores. *Edit: SUPE.md §4, the key's row marked deferred until the
  transmitting form exists.*

- **SUPE.md §14.4 states a requirement, not a data structure.** What a regime
  needs is that the transmit path reads a precomputed verdict — "may I transmit
  on this channel" — fed unconditionally, recomputed off the transmit path, and
  erring by no more than the margin the effective cap absorbs. The 10-second
  ring is one implementation; this straddle needs its own shape anyway (§6, and
  phase 8). *Edit: SUPE.md §14.4, property instead of shape.*

- **A detour is bounded to complete inside the daemon's existing timeout
  budget** — SUPE.md §12's explicit choice, taken provisionally now so the
  phases build toward one answer, confirmed against the turnaround measured in
  phase 7. The alternative — surfacing queue delay so Channel's
  `2.5 × RTT × (ring + 1.5)` inflates to match — reaches into rnsd, which SUPE
  otherwise never touches. *Edit: SUPE.md §12, marked provisional until phase 7
  produces numbers.*

## 2. What already exists — do not rebuild any of this

The single largest saving available. Roughly two thirds of what SUPE.md describes
as new machinery is already in this straddle serving adaptive power and the
neighbour table.

| SUPE.md asks for | Already here | Where |
|---|---|---|
| §5 parse the RNS header without secrets | `neiParse()` → `NeiHdr` with `ptype`, `hdr2`, `dtype`, `dest`, `transportId`, `hops` | `lora.cpp:1312` |
| §5.1 resolve a tag to the node behind it | `apNextHop4()` — HEADER_2 → transport id, HEADER_1 SINGLE → dest, link traffic → the link's dest, announces → nothing | `lora.cpp:3316` |
| §5.1 look a node up by identity prefix | `neiFindBy4()`, `neiFindByIdentity()`, `neiFindByDest()` | `lora.cpp:1370`+ |
| §5 pending-proof set | `NeiPend` — parks each elicitor's packet hash with a 30 s deadline, `neiPendAdd()` / `neiPendTake()`, expiry already wired into `nextDeadline()` | `lora.cpp:1529` |
| §5 learn our own hashes at hop zero | `neiObserve()`, called from `deliverInbound()` (rx) and `beginTx()` (tx); already does the hops-0 test, the cryptographic identity join, and the `us` marking | `lora.cpp:1789` |
| §10 per-node signal history | `Neighbor` already carries the rssi/snr envelope, the 12 × 5-min rollup, quality EWMA, `apPwr`/`haveApPwr` | §13 |
| §14 channel table and index space | `LORA_CH_HAIL` (0), `LORA_CH_MAX` (10), `RegimeChan`, regime 1's nine channels | `lora.cpp:93` |
| §14.4 per-channel airtime **telemetry** | `Rolling1h txAir[LORA_CH_MAX]` — per channel, already fed. **Telemetry only: it cannot enforce the budget** — see §6 and phase 8 | `lora.cpp:927`, `rolling.h` |
| §8 channel excursion discipline | `rssiSweepAgile()` — standby → setFrequency → startReceive → read → **unconditional** retune home | `lora.cpp:2870` |
| §11 carrier sense and a tracked noise floor | `csmaClear()`, `channelRssi()`, `r->noiseFloor` | `lora.cpp:3133`, `2821` |
| §14.5 sync word per configuration | `radioSyncWord()`, dispatched per family | `lora.cpp:4142` |
| §14.3 change spreading factor | `radioSetSf()`, dispatched per family | `lora.cpp:4159` |
| §14.6 radio family | `LoraFamily` enum and the X-macro chip table — the family values in SUPE.md §14.6 were taken from it and match | `lora.cpp:731` |
| §15 apply a power to one frame | `apApplyPower()`, and `txPwrNow` as the single authority for what the chip is set to | `lora.cpp:3375` |
| §15 clamp to configured power | `apClamp()` — already clamps to chip range *and* `tx_power`, which is SUPE.md §15's ceiling rule | §15 |

**The one gap in `apNextHop4`.** It returns nothing for a delivery proof
addressed to a packet hash, because adaptive power has nothing to do for one.
SUPE needs that case: it is §5's pending-proof entry and §6's proof-return tag.
Extend it, or add a sibling that falls through to the pend table, rather than
writing a new header walker.

## 3. New state

Small, except the compiled tables. Everything else hangs off structures that
already exist.

**Per radio, in `LoraRadio`:**

- SUPE enable flag, mirroring `r->adaptive`'s shape.
- The tag set of SUPE.md §5: a flat array of `{ uint8_t tag[3]; uint32_t expiry;
  uint8_t refs; }`. Size it at 256 entries to start — SUPE.md's thousand-entry
  figure is a ceiling, not a target, and this is PSRAM-allocated alongside
  `NeiState`.
- **The hold list of SUPE.md §6**: tags overheard in other senders' STARTs, each
  with the deadline its duration byte named. A 10-byte START adds a second
  entry, the sender's identity prefix, held to the same deadline — v1 never
  sends that form but always parses it (§10). A handful of entries — one per
  concurrently announced detour in earshot — consulted by the transmit gate (§5).
  An entry here is also the "recently seen busy" evidence §11 uses to suppress an
  absence verdict; one list serves both.
- **Absence state of SUPE.md §11**: per neighbour, last-heard time (the 5-minute
  staleness gate), an absent-until deadline (the 1-minute hold), and the decaying
  counter of unanswered offers. Both timers are local policy and need no
  agreement.
- Detour state machine: phase, chosen channel and step, the START's own SHA-256
  prefix, both deadlines, the saved hailing config to return to.
- Announce beat: next-due tick, reusing the existing 15-minute beat's timer
  mechanism rather than adding a second timer — the mechanism, not its
  interval: the beat fires per `SUPE.announce_interval`, default 30 minutes
  (SUPE.md §4).
- **Regime 1's airtime enforcement ring** (phase 8): per channel, fine-grained
  enough to defend "100 s in any 3600 s", with a precomputed per-channel verdict
  the transmit path reads. `txAir` stays telemetry.

**Per neighbour, in `Neighbor`:**

- The two capability bytes, and the adaptive-power offset, floor and
  success-count of SUPE.md §15. The existing `apPwr` is a settled absolute; SUPE
  replaces it with a derived value, so expect to change what `apSettle()` writes
  rather than to add beside it.
- **Path-loss pairs of SUPE.md §10**: a level measured here together with the
  transmit power the other side states for it one frame later, kept per
  configuration (hailing, and the step last used). These start flowing in
  phase 5 — ANNOUNCE2's power byte against our own reading of the frame is the
  first pair, at the hailing configuration — with the probe's joining in
  phase 6. Step choice consumes them from phase 7, adaptive power from
  phase 9; file them from the start anyway, so no later phase changes a
  producer.

**Compiled, not state: the regime tables.** Everything SUPE.md §3 lists as "a
constant of the regime", keyed by regime and version — channels, ladder, sync
words, frame layouts and lengths, duration encodings, ceilings, limits, retune
guard, expiry. The expiry date is the build's own timestamp plus the fourteen days
SUPE.md §3 allows — the ESP-IDF app descriptor already carries the timestamp,
and the offset is itself a table constant — never hand-maintained, and
`lora <n> supe` prints it, so a node that has gone quiet by expiry says so
instead of looking like a radio bug.

## 4. The pure core, and why it is built first

The largest piece of genuinely new code has no radio in it, and every phase from
5 on consumes it. Build it as a standalone unit with host-side tests before any
device work, so the phases on hardware are integration only. It must answer:

- **resolve(regime, version, hailing config, step) → configuration, or "no such
  step"**: the bitrate-ordered ladder of SUPE.md §14.3, regime 0's restriction to
  the hailing bandwidth, both families' limits — no SF5 on SX127x, SF6 barred
  wherever either side is family 1 — and the low-data-rate-optimisation rule,
  all from the *resulting* configuration.
- **the sync word** that configuration takes: the interface's own at step 0,
  `0x67` off it, `0x21` at SF5.
- **the top step a pair can use**, from both capability bytes and the hailing
  configuration — what the offer logic checks before it offers.
- **the step to offer**, from a measured path loss, each step's margin cost
  (SUPE.md §14.3's margin column, which exists for exactly this) and a target
  margin, capped by the top step above. Pure arithmetic, host-testable; what
  it answers when no pair is filed yet is the first-offer policy of §11.
- **encode and decode every frame**, with the length test driven by the same
  tables: a frame's permitted lengths enumerable from regime, version and type
  alone, so adding a frame later is a table row, not a parser branch.
- **both deadlines** — sender-waits-for-HERE and peer-waits-for-MANIFEST — from
  regime constants plus the step, and the duration (20 ms) and length (5 ms)
  byte encodings with their ceilings. The retune turnaround in that formula is
  a named constant with a deliberately generous provisional value: deadlines
  must end up regime constants both sides derive identically, so phase 7's
  measurement *chooses* the constant — it never becomes a per-device figure —
  and the chosen value lands in SUPE.md §14 as normative.
- **expiry**: past the compiled date, frames naming that regime/version are
  discarded and none are sent.

## 5. The transmit-path decision point

The whole sender side hangs off one classifier, and it lives in
`drainOneOutbound()` (`lora.cpp:4455`) — after the radio-ownership gates
(`mtxPhase`, `rc.phase`, `splitPending`, `txActive`) and before channel access,
where the next RNS packet comes out of the ITS buffer. For each outbound packet,
in order:

1. **Never dropped, never detoured** — announces and path requests (`neiParse`
   already yields the type): the plain path, always.
2. **Held** — the first address's tag is on the hold list, or its node is inside
   an absent-hold: do not drain this pass (hold), or drop (absence, §11). The
   ITS buffer is first-in-first-out, so a held head packet holds the line behind
   it; that is accepted — the alternative is the per-peer reorder queue SUPE.md
   §17 explicitly declines.
3. **Detour or probe** — the tag resolves to a neighbour whose ANNOUNCE2 has
   been heard: detour if a step is available to the pair, probe first if the
   peer is stale (§11's five-minute gate).
4. **Plain** — everything else, exactly as an interface with SUPE off.

The detour state machine then owns the radio the way a radio check does — its
own phase gate at the top of `drainOneOutbound`, the same pattern as `rc.phase`
— so a detour, a radio check and an announce beat can never interleave.

## 6. Order of work

Each phase is separately testable and leaves the device working. Phases 0–2 need
no device; 3–6 need one (phase 6's answering side can be a second device or an
injected frame); 7 up need two.

**Phase 0 — the spec edits of §1.** Land them in SUPE.md before code, in one
commit, so every later phase builds against wording that will not move.

**Phase 1 — receive dispatch (§0).** The three-branch rule of §0: byte 0 in the
SUPE range routes to a SUPE handler — a stub that discards — a byte ending in 0
or 1 to the split-header path, anything else is discarded. One line plus the
stub, no behaviour change on current traffic, and every later phase assumes it.
The log-level move of §8 lands here too — per-frame tx/rx lines drop from debug
to verbose — so debug is quiet before the first SUPE line exists.

**Phase 2 — the pure core (§4), on the host.** Regime tables, step resolver,
codec, deadline arithmetic, with host-side tests covering the ladder edge cases
(SX127x pairs, slow-hailing networks, low-data-rate boundaries, expiry). Have
the tests emit golden frame vectors — the exact on-air bytes per frame type,
regime and length form — as an artefact; every later phase that injects a frame
replays these, never hand-hexed bytes, so the codec and the injections cannot
disagree.

**Phase 3 — the tag set, receive-only.** Feed it from `neiObserve()`'s existing
tap points; extend `apNextHop4` for the delivery-proof case (§2). The
access-code gate of SUPE.md §13 lands here: an interface with one configured
never enables SUPE regardless of the setting. Add a `lora <n> supe` command that
prints the set with its expiries, plus the compiled regime expiry (§3); that is
the whole test. This proves the learning rules of SUPE.md §5 against real
traffic before any of it matters.

**Phase 4 — the hold, receive-only.** Parse overheard STARTs, keep the hold
list, gate `drainOneOutbound` on it (§5). First consumer of the duration byte
and of phase 2's codec against real frames. Parse both START lengths from the
outset: sending `sender_ident` is deferred (§10) but the 10-byte form is a
legal version-0 frame and discard is silent — a build that admitted only seven
bytes would silently drop STARTs from any later node that names itself, and
what accepting it costs is one length-table row and the second hold entry
(§3). Testable on one device by injecting a golden-vector START from phase 2
and watching traffic to that tag defer for the stated duration. The
busy-evidence suppression of §11 falls out for free — an overheard START naming
a tag is both a hold and proof its node is busy rather than gone.

**Phase 5 — ANNOUNCE2 on the main channel, every regime.** A node announces its
identity hashes and capabilities on the beat; peers file them against
`Neighbor` — and file the first path-loss pair with them, the frame's power
byte against their own reading of it (SUPE.md §7), so every announced peer
carries a hailing-configuration measurement before it is ever offered
anything. The bundling count is capped by the phase 2 table constant — the
single-frame maximum, far beyond any real identity count; if it ever binds,
the surplus waits for the next beat, a sender-local choice needing no
agreement. Still nothing hops. Now `lora n` can show which neighbours speak
SUPE.

**Phase 6 — the probe (§11).** START at step 0 with no channel change, HERE
back, then the traffic plainly. This is the whole rendezvous mechanism — sense,
transmit, deadline, answer — with no retune in it, so the timing can be made to
work before frequency agility is added. The HERE is checked against the
START's hash from the outset: one quoting an offer we did not make is somebody
else's exchange — ignored, and never read as absence (SUPE.md §8, §11). The
small randomised delay before offering lands here too, with the machine it
protects. The peer's sense before answering resolves to the existing
`csmaClear()` — regime 0 inherits the hailing channel's rules, and the
regulatory clear-channel assessment is phase 8's. With it land the pieces the
probe's verdict needs: the staleness gate, the absent-hold and drop-as-absent
path (never-drop classes exempt, §5), and the probe's path-loss pairs joining
phase 5's announce pair (§3), filed under SUPE.md §10's two bindings — links
inherit under the link identifier both sides derive, relays file against the
reverse-table entry rather than the tag. Build
the probe as the detour state machine with its retune states unreachable:
phase 7 adds states to this machine rather than a second machine beside it,
which is what keeps §5's radio-ownership gate singular. Settle
the 10 ms task-tick question here too (§9): sense-and-transmit moves to the
`esp_timer` path or regime 1's compliance claim gets revisited — decide where
the loop is simplest. Independently useful: it stops full packets going to
absent peers, which is measurable on its own.

**Phase 7 — the detour, regime 0.** Add the retune: START, both ends to a
higher spreading factor on the same frequency, HERE, MANIFEST, train, home.
Regime 0 means `setSpreadingFactor` and `setSyncWord` and nothing else moves,
which is the smallest possible version of the retune path. The step-down walk
lands with it: a sender whose HERE deadline passes offers again at a lower
step — regime 0's escalation moves the step only, having no channels to move —
down through the probe to absence (SUPE.md §11); phase 8 adds the channel
dimension to this same walk rather than a walk of its own. The first offer to
a peer with nothing filed beyond phase 5's announce pair needs a policy (§11),
decided here, where the first real offers are made. **Measure the
turnaround here** — it prices a failed offer and confirms or overturns §1's
provisional §12 bound. The measurement *chooses* the turnaround constant of
§4, and the chosen value lands in SUPE.md §14 as a regime constant — both
deadlines are numbers both sides must derive identically, never a per-device
figure. Decide here too what a sender does with a step that produces silence —
hold it, drop one, or wait for the peer's next announcement (SUPE.md §16):
this is the first phase in which a train can vanish, and the decaying counter
of §3 needs that policy. This is also where LoRaMon first has something to
show: thread the real channel through `loraMonPush` (§8), which fixes the
`txAir` crediting in the same argument.
Two prerequisites: the SF5/SF6 datasheet pass from §11 lands first, because
regime 0's second step from an SF7 network is SF5 and `0x21`'s behaviour there
is unconfirmed; and the bench is SX126x-class parts or a slower hailing
configuration — an SX127x pair hailing at SF7 has zero regime-0 steps
(SUPE.md §14.3), and a null result there is the ladder working, not a bug.

**Phase 8 — regime 1.** Frequency agility, the nine channels, the escalation
walk — channel changes with every offer, no reuse within a transaction, floor at
step 0, plus the 100 ms minimum frequency-reuse gap after a detour. Before the
ring is shaped, settle SUPE.md §16's one-budget-or-two question — whether
hailing airtime counts against the same budget as detour airtime — since it
decides whether channel 0 carries a ring at all. The
enforcement ring of §3: `Rolling1h`'s six 10-minute buckets cannot defend
"100 s in any 3600 s" — a node can spend the budget late in a bucket, have it
age out, and spend it again, approaching twice the cap inside a true hour — so
regime 1 gets its own finer per-channel ring with a precomputed verdict, and
`txAir` stays what it is. The regulatory clear-channel assessment on the timer
path (−75 dBm at 500 kHz, 160 µs listen, 5 ms dead time), which is a different
measurement from the sweep (§9). Confirm SUPE.md §16's escalation-cap
arithmetic while here.

**Phase 9 — adaptive power over SUPE.** Replace `apSettle`'s absolute with
§15's derived form, and wire the probe's outcome into the offset and floor. The
path-loss pairs have been accumulating since phase 6.

### 6.1 Feature checklist, per phase

The prose above says why; this is the tracking list of what each phase
delivers. Tick items as they land, and grow a phase's list rather than
starting side work no phase owns.

**Ticked means built and building clean, not proven on air.** Every phase's code
has landed; what stays unticked is the handful of items whose deliverable is a
*measurement* rather than an implementation, and those need two radios on a
bench. §11 is still the list of what they block.

**Phase 0 — spec edits**
- [x] ANNOUNCE1 and the announce channel deleted, grep-complete (§1); `0xC3`
      reserved, never reassigned
- [x] `announce_bundling` and the §16 spreading item deleted
- [x] `SUPE.sender_ident` marked deferred in §4 — v1 receives the 10-byte
      form, never sends it
- [x] `SUPE.regime` replaced by `s.lora.<n>.afa` in §3 and §4
- [x] sender candidate-channel sensing removed from §11; §16 item struck
- [x] §14.4 restated as a property, not a data structure
- [x] §12 bounded-detour choice recorded, marked provisional until phase 7
- [ ] one commit, SUPE.md only — the edits landed, the commit did not: the
      plans repo has an in-flight move of `iface-lora/SUPE.md` to the top level
      sitting unstaged, and folding somebody else's file move into a spec-only
      commit is not this phase's to do

**Phase 1 — receive dispatch**
- [x] three-branch dispatch: SUPE range → handler, 0/1-ending → split header,
      rest → discard
- [x] SUPE handler stub that counts and discards
- [x] per-frame tx/rx log lines moved debug → verbose (§8)
- [x] no on-air behaviour change; existing traffic unaffected

**Phase 2 — pure core, host-side**
- [x] regime tables for regimes 0 and 1, version 0, expiry stamped as the
      build timestamp plus 14 days (SUPE.md §3), the offset a table constant
- [x] `resolve(regime, version, hailing, step)` with family limits (no SF5 on
      SX127x, SF6 barred for family 1) and the low-data-rate rule
- [x] sync word from the resulting configuration (`own` / `0x67` / `0x21`)
- [x] top step for a pair from both capability bytes plus hailing config
- [x] step choice from measured path loss, margin cost and target margin,
      capped by the pair's top step
- [x] codec for every frame; length table admits both START forms and
      count-derived ANNOUNCE2
- [x] ANNOUNCE2 bundling count cap as a table constant, from the single-frame
      maximum
- [x] deadline arithmetic parameterised on the named turnaround constant (§4)
- [x] duration (20 ms) and length (5 ms) encodings with ceilings
- [x] expiry: past the date, nothing sent, frames naming it discarded
- [x] host tests over the ladder edge cases; golden frame vectors emitted as
      an artefact

**Phase 3 — tag set, receive-only**
- [x] tag-set state, 256 entries, PSRAM-allocated beside `NeiState`
- [x] reference-count width judged against the collision arithmetic
      (SUPE.md §16) — or the full address kept alongside — settled here,
      where the set is built
- [x] fed from `neiObserve()` tap points, all five SUPE.md §5 classes
- [x] `apNextHop4` extended for the delivery-proof case
- [x] access-code gate: a configured access code disables SUPE outright
- [x] `lora <n> supe`: tag set with expiries, plus the compiled regime expiry
- [x] `afa` comment contradiction fixed in `lora.cpp` and `INTERNALS.md` §18.2
- [x] debug lines: tag learned, tag retired

**Phase 4 — the hold, receive-only**
- [x] START parsing, both lengths; sender-identity hold from the 10-byte form
- [x] hold list with per-entry deadlines; `drainOneOutbound` gated on it
- [x] overheard START doubles as busy evidence for §11's absence suppression
- [ ] golden-vector injection test: traffic to the tag defers for the stated
      duration
- [x] debug lines: hold taken, hold released, hold expired

**Phase 5 — ANNOUNCE2**
- [x] announce beat on the existing beat's timer mechanism; interval from
      `SUPE.announce_interval`, default 30 min
- [x] ANNOUNCE2 sent on the main channel, bundled, capabilities and power byte
- [x] receive side files capabilities against the `Neighbor` identity
- [x] path-loss pair filed: the frame's power byte against our own reading
- [x] bundling count capped at the phase 2 constant; surplus identities wait
      for the next beat
- [x] `lora n` shows which neighbours speak SUPE
- [x] debug lines: announce sent, capability filed

**Phase 6 — the probe**
- [x] detour state machine, probe states only; retune states unreachable
- [x] START at step 0, no channel change; HERE answered from in place; traffic
      sent plainly after
- [x] peer senses via `csmaClear()` before answering; sender deadline from
      phase 2 arithmetic
- [x] HERE checked against the START's hash; a foreign hash ignored, never
      read as absence
- [x] randomised pre-offer delay
- [x] staleness gate (5 min), absent-hold (1 min), drop-as-absent with
      never-drop classes exempt
- [x] probe path-loss pairs filed per §3; links inherit under the link
      identifier, relays file against the reverse-table entry (SUPE.md §10)
- [x] task-tick vs `esp_timer` decision for sense-and-transmit settled
- [x] debug lines: probe out, probe answered, probe expired, absence verdict
      set and cleared
- [ ] measurable: no full packets transmitted to absent peers

**Phase 7 — the detour, regime 0**
- [x] retune path: spreading factor and sync word only; unconditional
      return-home on every exit, error paths included
- [x] HERE / MANIFEST exchange with START-hash check on both sides
- [x] step-down re-offer on a missed HERE deadline, down through the probe to
      absence
- [x] first-offer step policy for a peer with no filed pairs decided (§11)
- [x] train transmit loop bounded by the announced duration; peer ends on
      count or length
- [ ] turnaround measured on the timer path; constant chosen and written into
      SUPE.md §14
- [ ] §12 bound confirmed, or the declared-bitrate route gets designed
- [x] silence-on-a-step policy decided and wired to the decaying counter
- [x] detour airtime to `txAir[ch]`, hailing duty view untouched
- [x] `loraMonPush` records against the channel actually tuned — a `chNow`
      field on the radio rather than a new argument, so no call site can pass
      it wrong; `txAir` credits and the APPC band follow it (§8)
- [ ] confirm on a device that LoRaMon records carry a non-zero `ch`, and that
      the browser viewer renders the token — only the built app was in tree
- [x] debug lines: offer out (channel, step, and what chose them), HERE heard,
      MANIFEST sent, train done, returned home, deadline expired

**Phase 8 — regime 1**
- [x] one-budget-or-two decided before the ring is shaped
- [x] nine-channel table live; frequency retunes with the same return-home
      discipline
- [x] escalation walk: channel and step change together, no reuse within a
      transaction, floor at step 0
- [x] 100 ms frequency-reuse gap after a detour
- [x] per-channel enforcement ring with precomputed verdict; `txAir` stays
      telemetry
- [x] regulatory CCA on the timer path: threshold per bandwidth, 160 µs
      listen, obligatory deferral, 5 ms dead time
- [ ] escalation-cap arithmetic confirmed
- [x] debug lines: escalation move, channel verdict flip, budget refusal

**Phase 9 — adaptive power**
- [x] `apSettle` replaced by the derived form: clamp(path loss + margin +
      offset, floor, maximum)
- [x] probes at adapted power; outcome drives offset and floor
- [x] fast-up (~6 dB), slow-down (1–2 dB), success-count gating, decaying
      floor
- [x] never above configured `tx_power`, under any failure
- [x] debug lines: offset moved and why, floor set, power chosen per detour

## 7. Codec notes

Not in SUPE.md because they are about this part, not the protocol.

**No checksum is achievable without reconfiguring the receiver.** LoRa's
explicit header carries a CRC-present bit, so a frame transmitted with the
payload CRC off is correctly received by a peer configured with it on. Confirm
on the part before relying on it, but this is what makes SUPE.md §3's decision
implementable at all — otherwise every SUPE frame would need the receiver in a
different mode.

**Do not touch the preamble.** SUPE.md's 26/31/36 ms figures assume preamble 8;
this interface defaults to 12 and reconfiguring per frame costs more than the
4 ms it would save. Take the interface's preamble and let every airtime figure
in the spec run about 4 ms long.

**The START hash is SHA-256 over the transmitted frame**, all seven or ten
bytes, first three bytes taken. Both ends have those exact bytes. `rnsdVerify`'s
hashing path is already available on this task.

**The length table admits both START forms from day one** — 7 and 10 bytes —
even though v1 only ever transmits 7 (§10). Receiving is where dropping the
long form would hurt, and it would hurt silently.

**Levels are `dBm + 64` in an `int8_t`.** Note the adaptive-power flag rides in
the top bit of the *maximum power* byte only — free there because a transmit
power never stores negative, unlike a received level. Do not generalise the
trick.

## 8. Logging

Two levels, one discipline: **debug is the SUPE decision trace, verbose is the
frame trace.**

- **Every per-frame tx/rx line moves from debug to verbose** — the `dbg` pair
  in `loraMonPush` (`lora.cpp:2285`, `:2289`) and the unparsed-frame line
  (`:1260`); the hexdump is verbose already. The move lands with phase 1,
  before the first SUPE line exists, so debug starts quiet.
- **Debug then carries every SUPE action and decision, one line each**: the
  classifier's verdict whenever it is anything but plain (§5), every offer
  with the chosen channel and step and the measurement that chose them, every
  probe and its outcome, every hold taken and released, every absence verdict
  set and cleared, every escalation move, every retune out and home, every
  deadline expiry, every announce sent and capability filed, every power
  choice once phase 9 lands. Each phase adds its lines as it adds its
  behaviour — the checklist in §6.1 names them per phase.
- The test for the split: at debug a detour reads as a short story — offer,
  HERE, MANIFEST, train, home — with no frame dumps between the lines; at
  verbose the same story is interleaved with every frame. A line that would
  fire per packet inside a train belongs at verbose with the frames.

**LoRaMon is already channel-aware in its record format and blind at the push
site.** The packed string carries a trailing channel token
(`r|rssi|snr|dur|bytes|type|ch`, `t|txp|dur|bytes|type|wait|ch`,
`lora.cpp:2240`) and `IfMsg` carries `m.ch` — but `loraMonPush` stamps every
frame `LORA_CH_HAIL` and credits `txAir[LORA_CH_HAIL]` unconditionally. One
new channel argument threads both: records show detour frames on the channel
they flew on, and the airtime crediting lands per channel, which is the same
requirement §9 states for contention. Lands in phase 7, the first time any
frame flies off channel 0. Verify the viewer actually renders the `ch` token
when the first detour records appear — only the built app was in this tree.

## 9. This straddle's habits, and where they will bite

- **The task tick is 10 ms.** SUPE.md §14.2's 5 ms dead time between a clear
  reading and transmitting is not reachable from the task loop. The existing
  `csmaClear()` is tick-driven for the same reason. Either the sense-and-transmit
  pair moves to the `esp_timer` path the radio check already uses for its slot
  transmits, or regime 1's compliance claim needs revisiting. Settle this in
  phase 6, where the loop is simplest.
- **Transmission is synchronous** (`radio.transmit()` runs inline on the task,
  `INTERNALS.md` §6), so a train is a loop that blocks the task for its
  duration. That is acceptable — nothing else can use the radio anyway — but the
  ITS poll does not run, so bound the train and do not let a detour outlive its
  announced duration.
- **`splitPending` gates all transmission.** A detour must not begin mid-split.
  Take the same early-out `drainOneOutbound()` uses.
- **The radio must never be stranded off the hailing channel.** `rssiSweepAgile`
  retunes home unconditionally and unchecked, precisely so a failed retune
  cannot leave it away. Every SUPE path off-channel needs the same discipline,
  including every error return.
- **`txPwrNow` is the single authority** for what the chip is set to and what
  the LoRaMon record is stamped with. If SUPE moves the power register anywhere
  other than `apApplyPower()`, the two drift and the telemetry lies.
- **Detour airtime must not be credited to the hailing channel.** SUPE.md §8
  makes this an implementation requirement, and the mechanism is already here:
  record against `txAir[ch]` for the channel actually used, and keep
  `appcAirtime()`'s view — which chooses the contention band — to
  hailing-channel transmissions only. Get this wrong and the detour stops
  shortening future waits, silently.
- **`txAir` measures; it does not enforce.** `Rolling1h` is six 10-minute
  buckets (`rolling.h:22`) and its own comment rules it out for fixed-window
  budgets. Regime 1's cap gets the separate ring of §3; do not bolt enforcement
  onto the telemetry.
- **RSSI below `LORA_RSSI_INVALID_DBM` is not a measurement**, it is the
  receiver answering before it is listening. `rssiSweepAgile` discards those; a
  clear-channel assessment must too, or it will read every channel as gloriously
  quiet.
- **Bandwidth is deliberately not retuned during a sweep** so readings share a
  noise reference. A regulatory clear-channel assessment is the opposite case
  and must match the channel's occupied bandwidth. These are different
  measurements; do not reuse the sweep's reading as a CCA.

## 10. Not in version 1

- ANNOUNCE1, the announce channel and `announce_bundling` — deleted from the
  spec in phase 0, not deferred.
- Everything in SUPE.md §17.
- `sender_ident` — **sending only.** The flag changes the START's length and
  therefore the hash input, so transmitting the 10-byte form waits until the
  fixed form works. The receive side parses and honours it from phase 4
  regardless: it is a legal version-0 frame and discard is silent, so a build
  that admitted only seven bytes would silently drop STARTs from any later
  node that names itself. The `SUPE.sender_ident` key is deferred with the
  sending — phase 0 marks it so in SUPE.md §4 — so v1 ships no setting that
  governs nothing.
- Any reverse traffic on a detour. The modem has no per-peer queue and SUPE.md
  §17 already says so.

## 11. Open, and blocking

- **Retune turnaround** (phase 7), on the timer path rather than the task path.
  Sets both deadlines, the escalation's cost, and how many offers a silent peer
  is worth.
- **The §12 confirmation** (phase 7): does a bounded detour fit inside Channel's
  retransmit budget at the measured turnaround? §1's provisional answer is yes;
  if the numbers say otherwise, the declared-bitrate route has to be designed,
  not discovered.
- **The SF5/SF6 datasheet pass** — `0x21` at SF5 and `0x67` at SF6 landing as
  SUPE.md §3's bin arithmetic says, given both spreading factors' modified
  preamble and sync handling on SX126x. Blocks phase 7, whose second step from
  an SF7 network lands on SF5.
- **Silence on a step** (phase 7) — what a sender does with a step that
  produces no delivery signals: hold it, drop one, or wait for the peer's next
  announcement (SUPE.md §16). The decaying counter of §3 needs the policy the
  moment trains exist.
- **The first offer's step** (phase 7) — what a sender offers a peer with
  nothing filed beyond phase 5's announce pair, which covers the hailing
  configuration only. SUPE.md §10 implies a slow first detour without stating
  a policy; whether that pair plus the margin table justifies opening higher
  is the decision.
- **One duty budget or two** (phase 8) — whether hailing and unicast airtime
  share a budget (SUPE.md §16). It shapes the enforcement ring, deciding
  whether channel 0 carries a ring at all, so it is settled before the ring is
  built, not after.
Everything else in SUPE.md §16 blocks nothing before its named phase; §11.1
maps it item by item.

### 11.1 SUPE.md §16, item by item

§16 is where the spec and this plan drift first, so every item carries a
disposition — resolved in phase 0, owned by a phase, or blocking (above). A
§16 item with no row here is a plan bug; keep the list exhaustive as the spec
moves.

| SUPE.md §16 item | Disposition |
|---|---|
| SF6 framing exception for family 1 | open by choice; phase 7 shows the SX127x gap, phase 8 is the last cheap moment to decide |
| channel 9 at 500 kHz | phase 8 |
| sender senses the candidate channel | resolved in phase 0 — dropped (§1) |
| retune turnaround | phase 7 — blocking, above |
| how far the escalation walks | phase 8 — confirm the cap arithmetic |
| judging a step without acknowledgements | phase 7 — blocking, above |
| one duty budget or two | phase 8 — blocking, above |
| SF5/SF6 preamble and sync on silicon | datasheet pass — blocks phase 7, above |
| spreading unbundled announcements | resolved in phase 0 — deleted with `announce_bundling` (§1) |
| tag reference count | phase 3, where the tag set is built |
| unprefixed setting aliases | phase 3, where the first `SUPE.` key lands |
| ANNOUNCE1 and the announce channel | resolved in phase 0 — dropped (§1) |

## 12. Coverage

One row per SUPE.md section, so a behaviour with no owner is visible instead of
silently unimplemented. Keep it current as phases land and the spec moves.

| SUPE.md | Where here |
|---|---|
| §0, §0.1 — frames and fields | phase 2 codec; §7 |
| §1, §2 — rationale, what it rests on | nothing to build |
| §3 — regimes, versions, lengths, sync words, quantisation | phase 2 |
| §4 — configuration | phase 0 edit; `enable` gate in phase 3 |
| §5 — addresses that mean us | phase 3 |
| §5.1 — addresses that mean someone else | phase 3 (`apNextHop4` plus its gap) |
| §6 — tag, START, **the hold** | hold in phase 4; START in phases 6–7 |
| §7 — announcing | phase 5 (ANNOUNCE1 deleted in phase 0) |
| §8 — the unicast channel | phase 7 |
| §9 — Reticulum announces untouched | no code; verify no SUPE path ever queues one |
| §10 — what is learned, where filed | filed from phase 5 (announce pair) and phase 6; step choice consumes from phase 7, adaptive power from phase 9 |
| §11 — failing well | probe, absence and the collision rule in phase 6; step-down walk in phase 7; channel walk in phase 8 |
| §12 — daemon arithmetic | decided in §1, confirmed in phase 7 |
| §13 — where it does not apply | access-code gate in phase 3; non-peers in §5's classifier |
| §14 — regimes | tables in phase 2; enforcement ring in phase 8 |
| §15 — adaptive power | phase 9 |
| §16 — open items | §11.1, one disposition per item |
| §17 — deferred | not in version 1 |
