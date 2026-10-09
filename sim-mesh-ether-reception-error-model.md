# sim-mesh ether: a reception error model that knows frame length

Replace the ether's hard demodulation threshold and its linear CRC band with
a staged reception model driven by one symbol-error curve per spreading
factor. Add optional, seeded, time-correlated fading per link. The result:
longer frames are more fragile at the same signal-to-noise ratio (SNR),
marginal links flicker from frame to frame instead of being permanently good
or dead, and a frame fails silently (preamble never found, or header error)
or with a cyclic redundancy check (CRC) error in the proportions the chip's
own decoding chain gives.

Everything below about the current code was read on 2026-10-07 in
`sim-mesh/` (repository ropg/sim-mesh). Line numbers are approximate.
Changes to that repository go as a pull request (PR) based on `main`.

## Why

Today, against noise alone, a frame's length has no effect on whether it
arrives:

- `Ether.audible()` (`ether/ether.py` ~1252) is a hard cut: level minus
  noise at or above `SENSITIVITY_DB[sf]` (−7.5 dB at SF7, 2.5 dB lower per
  step to −20 dB at SF12; `SLOWEST_SENSITIVITY_DB` for a frame naming no
  spreading factor). Below it, no lock and no `rx_begin` (except an energy
  one if the sense threshold is crossed). Above it, the noise test in
  `verdict_for` (~1678) always passes.
- The level is fixed per frame and receiver (`level_of`, ~1237, cached in
  `Frame.levels`) from a static loss table. No fading. The testbed's
  shadowing (`testbed/geodata.py` `shadowing_db`, applied by
  `losses.with_shadowing`) is one static draw per pair for the whole run.
- The CRC band (`--crc-margin-db`, default 0 = off; `crc_margin_fails`,
  ~361; applied in `deliver_end`, ~1775) is one seeded draw per frame and
  receiver. The failure chance falls linearly from 1 at the threshold to 0 at
  the top of the band. Length is not an input.
- Length enters only through interference: longer air time means more
  overlap, and `segments()` + the worst-piece rule in `verdict_for` judge
  every stretch.

What real hardware shows: a frame mostly either arrives or it does not, and
CRC errors are rare (the reticulous firmware counts them: `stats.crc_err` in
`iface-lora/esp-idf/src/lora_mon.cpp` ~404, shown by the CLI in
`lora_cli.cpp` ~291).

### What the theory says (computed for this plan)

LoRa demodulation (dechirp, fast Fourier transform, pick the largest bin) is
non-coherent detection of M = 2^SF orthogonal signals in additive white
Gaussian noise (AWGN). Its symbol error rate (SER) is exact and computable
(formula below). Run through the decoding chain (preamble + sync, header
block, payload blocks with Hamming coding) it gives packet error rates (PER)
like these, at margin d dB from the datasheet threshold, coding rate 4/5,
explicit header, CRC on, no implementation loss:

- SF7, 50-byte payload: d = −2 → 95% lost, −1 → 45%, 0 → 7%, +1 → 0.5%.
  At 200 bytes: −1 → 90%, 0 → 24%, +1 → 1.7%.
- SF9, 50 bytes: −2 → 70%, −1 → 16%, 0 → 1.2%. 200 bytes: −1 → 45%, 0 → 4%.
- SF12, 50 bytes: −3 → 75%, −2 → 16%, −1 → 1%. 200 bytes: −2 → 48%, −1 → 3.6%.

So the waterfall is about 2 dB from almost all lost to almost all received,
and quadrupling the payload shifts it by roughly 0.5–0.8 dB. For the
reference frame below (121 bytes) the ideal-receiver curve has its 50% point
0.6 dB (SF7) to 2.2 dB (SF12) under the datasheet's thresholds, growing
about 0.3 dB per step; that gap is the chip's implementation loss.

How the failures split in pure AWGN (SF7, 50 bytes): at d = −1.5, 5.8%
silent (preamble/sync not found), 0.2% header error, 69.5% CRC error, 24.4%
clean. At d = −1: 2.5% silent, 42.8% CRC, 54.6% clean. **Inside the
waterfall, CRC errors dominate the failures**: the payload is 135 symbols
(15 blocks of 9) against the 6 that must be right to lock. So the rarity of CRC errors in the field does not come from the chain's
shape. It comes from how rarely a link sits inside that 1–2 dB window. A link
well above it is clean, and a link below it is silent (nothing to lock on).
Time variation decides how often a link passes through the window. That is
what the fading term is for, and the field's ratio of CRC errors to clean
receptions is what calibrates it (Calibration, below). With a static table
and no fading, a link parked in the window would produce CRC errors on every
other frame forever. That is the main unrealism to avoid.

Script used for these numbers (pure Python, no numpy): the formula in "The
symbol error curve" with Simpson integration, and the block counting in "The
stages".

## Wire sketch (what changes on the wire)

The header outcome is decided at the lock and carried in `rx_begin`. The
station's chip model raises `HEADER_ERR` instead of `HEADER_VALID` at
`t_hdr` and lets go of the frame. The ether closes the reception at `t_hdr`
with an `rx_end` of verdict `hdr`, for the record. The chip model ignores it,
because it already dropped the lock.

```
transmitter → ether  tx {…, cr, hdr: "explicit", crc, pre, payload}        unchanged; the ether now reads cr/hdr/crc/payload length
ether → receiver     rx_begin {slot, id, t0, t_pre, t_hdr, t_end, level, hdr_ok: false}   new field; absent = true
                     (chip model at t_hdr: HEADER_ERR, drop lock; firmware forgets the frame)
ether → receiver     rx_end {slot, id, t, verdict: "hdr", cause: "noise", payload, rssi, snr}   at t_hdr, not t_end; t = t_hdr
```

A frame that passes the header:

```
ether → receiver     rx_begin {…, hdr_ok absent}
ether → receiver     rx_end {slot, id, t, verdict: "clean" | "crc", cause, payload, rssi, snr}   at t_end
```

(`t` is the ether's instant, carried on every `rx_end` today.)

`cause` is new on every `rx_end` that is not clean: `noise` (the error curve
failed it), `interference` (a rejection figure, the capture rule or bench
capture failed it), `talked_over` (the receiver transmitted during it),
`lost` (taken off by another frame, or arrived while the receiver was busy).
A real chip cannot know the cause. It is there for the record and the
testbed's tools. The station ignores it. Change the wire outright: every
node is rebuilt together, so no capability bits and no compatibility path.

## The symbol error curve

For SF s and linear SNR γ (signal over noise in the receiver bandwidth), the
energy per symbol over noise density is Es/N0 = 2^s · γ. With a = √(2·Es/N0)
and M = 2^s, the probability a symbol is right is

```
Pc(s, γ) = ∫₀^∞ x · exp(−(x² + a²)/2) · I0(a·x) · (1 − exp(−x²/2))^(M−1) dx
SER = 1 − Pc
```

(the correct bin is Rician, each of the M−1 others Rayleigh). Numerically:
use exp-scaled I0 (I0(z)·e^(−z): power series for z < 15, the asymptotic
1/√(2πz)·(1 + 1/(8z) + 9/(128z²)) above), write the integrand as
`x·exp(−(x−a)²/2)·i0e(a·x)·exp((M−1)·log1p(−exp(−x²/2)))`, and integrate
over [max(0, a−10), a + √(2·ln M) + 15]. 400–1000 Simpson points are plenty.
Clamp the result to [0, 1].

Evaluate lazily: `functools.lru_cache` on (sf, SNR rounded to 0.05 dB). Only
the SNRs actually seen get computed. Pure Python. `ether.py` imports no numpy
and must stay that way (stdlib only; `statistics.NormalDist` is available for
the fading).

Apply a per-SF implementation loss `IMPLEMENTATION_LOSS_DB[sf]`: the curve is
evaluated at SNR − L. Fix L so that a **reference frame** (121 bytes, coding
rate 4/5, explicit header, CRC on, 8-symbol preamble: the frame the
tools/rncapture bench used) has PER = 50% exactly at `SENSITIVITY_DB[sf]`.
Compute L once per SF by bisection at first use, not as hand-typed
constants. Then the datasheet threshold keeps its meaning (half of a typical
frame gets through there), `audible()` and `levels()` keep answering
"reachable" at the same place, and only the shape around it is new. This
50% anchor is an assumption to be replaced by the bench sweep (Calibration).
Before settling on it, read the footnote under the SX1261/2 datasheet's
sensitivity table: if the figures are stated at a packet error rate and a
payload length, that PER and that length are the anchor, not 50% and 121
bytes. Put the anchor, its reason and the reference frame in the figures
section at the top of `ether.py`, with sources, the way every other figure
there is.

## The stages

Frame shape (Semtech AN1200.13, the arithmetic in
`sim-mesh/radio/src/toa.h` `loraToaSeconds`): preamble (n + 4.25 symbols,
ending at `t_pre`; `SYNC_SYMBOLS` = 4.25 of that is sync word + start-of-frame
delimiter), then a first block of 8 symbols at coding rate 4/8 and reduced
rate (it carries the header when explicit), then
`B = max(ceil((8·P − 4·SF + 28 + 16·crc − 20·implicit) / (4·(SF − 2·DE))), 0)`
blocks of (cr + 4) symbols, P the payload length in bytes, cr the coding-rate
denominator minus 4, DE = 1 when a symbol lasts over 16 ms. `t_hdr` is the
end of the first block.

Interleaving: within a block each symbol contributes one bit to each
codeword. One wrong symbol therefore puts at most one bit error in each
codeword. Coding rates 4/7 and 4/8 correct a single bit error per codeword.
4/5 and 4/6 only detect. So, with p = SER at that block's SNR:

- block of n symbols survives, cr 4/7 or 4/8: (1−p)^n + n·p·(1−p)^(n−1)
- block of n symbols survives, cr 4/5 or 4/6: (1−p)^n

Two approximations, both pessimistic, both stated in one comment beside the
formulas: the reduced-rate first block is treated with the same p, although
reduced rate tolerates adjacent-bin errors; and two wrong symbols are taken
to kill a correcting block, although they defeat a codeword only when both
flip the same bit position, which with Gray-coded bins happens to none of
the SF codewords about (3/4)^SF of the time.

The first block is always 8 symbols at coding rate 4/8, whatever `cr` the
frame's payload blocks use, so `frame_blocks` reports it as correcting even
when the payload blocks are not.

1. **Lock (preamble + sync found)**, at `arrive` / `arrive_pairwise`, both
   rules. P_lock = (1−p)^(PREAMBLE_FOUND_SYMBOLS + 2): four preamble symbols
   (`PREAMBLE_FOUND_SYMBOLS`, ~154) plus the two sync-word symbols. p is at
   the faded level at the lock instant (the frame's start, or `now` for a
   late lock via `tell_late`). One draw, `seeded_draw(seed, eid, rsid, slot,
   "lock")`, compared against P_lock. A late lock and an on-time lock of the
   same frame at the same slot read the same draw. Failing the lock is
   exactly today's "not decodable": the frame is energy only (the existing
   sense-threshold `rx_begin` with `cad`, or nothing). This replaces
   `self.audible(level, frame.bw, frame.sf)` in the decodable test of both
   arrive functions. In `arrive` that one `decodable` serves CAD slots and
   RX slots alike, and it stays that way: a CAD is a preamble detection, so
   a slot in CAD is told of the frame when the same "lock" draw passes, and
   a CAD and an RX of one frame at one slot agree. `matches()` stays as is.
2. **Header**, decided at the lock too, explicit header only. P_hdr = the
   8-symbol block at coding rate 4/8, at the faded level over
   [`pre_us`, `hdr_us`]. Draw key "hdr". Noise and fading only. Interference
   inside the header still ends the frame as `crc` at `t_end`, through the
   existing rule. That is a deliberate limit, stated in INTERNALS. A failed
   header: `rx_begin` carries `hdr_ok: false`, the reception is marked to end
   at `hdr_us`, and a timer at `hdr_us` sends `rx_end` verdict `hdr`, cause
   `noise`, pops the slot's lock (`station.locks`), and raises `on_rx`. From
   `hdr_us` on the slot is free to lock another frame, as a chip in
   continuous RX hunts again after a header error; the reception records
   that following stopped at `hdr_us`, so `followed_at()` (which
   `bench_survives` asks of the first frame's reception) says no from then
   on. In implicit-header mode the first block is just the first payload
   block (stage 3), judged by the 4/8 formula.
3. **Payload**, at `deliver_end` (`t_end`), for a reception not lost,
   abandoned, talked over or ended at its header, and that passed the
   interference rule. P_pay = product over the B blocks (plus the first block
   when implicit) of the block survival at each block's faded level. Blocks
   are spread evenly from `hdr_us` to `end_us`. One draw, key "payload".
   Failure: verdict `crc`, cause `noise`.

All three apply under both rules (`verdict_for` and `verdict_pairwise`) and
with `--no-interference`: they are the medium's noise model, and the rules
differ only in how they treat interference. Today `verdict_pairwise` has no
noise test at the verdict at all (only `audible` at the lock). It gains stage
3. `verdict_for`'s first noise test (`level - self.noise(frame.bw) <
self.sensitivity(frame.sf)`, ~1691) goes away, since stages 1–3 replace it.

With CRC off (`crc: false` in the tx): a failed payload is still reported as
`crc`. A real chip would hand over corrupted bytes as good, but the ether
never opens payloads. Note it as a limit. Reticulum and the reticulous
firmware always run CRC on.

What stays threshold-based (the 50% point, unfaded): `audible()` as used by
`levels()` only (the map's "who can decode whom", `simd`'s link display).
Rename `audible` to say what it now is (reachable at the median) if that
reads better. CAD's decodable half is the lock stage (above); CAD's energy
half and the RX energy reading use the faded level (`in_band_energy` and
the `level` in `rx_begin`).

`rx_begin.level` = faded level at the lock instant. `rx_end.rssi`/`snr` = the
mean faded level over the payload blocks (what the chip's packet status
reports), rounded as today. The lock and verdict use unrounded figures as
today.

## Fading

Off by default (σ = 0), so a run that does not ask is unfaded. Two settings
on `Physics`: `fading_db` (σ, the spread in dB) and `coherence_s` (Tc).

Per **unordered pair of node names** (the channel is reciprocal even when the
table is not; the key is `self.names[sid]` for each end, the nodeset's name,
which is stable across a station's restart where station ids are not; a
station the nodeset does not name has no path loss and so no level, and
never reaches `fade_db`), a unit-variance Gaussian process g(t) on the
ether's clock:

```
k = floor(t / Tc), u = t/Tc − k
z_k = NormalDist().inv_cdf(seeded_draw(seed, "fade", name_lo, name_hi, k))   (clamp the draw away from 0)
g(t) = cos(π·u/2)·z_k + sin(π·u/2)·z_{k+1}
fade_db(t) = σ · g(t)
```

cos² + sin² = 1 keeps the variance 1 everywhere. The process is continuous,
needs no state, and is the same whatever order it is asked in (the property
`seeded_draw` exists for). Its correlation at lag Tc is sin(π·u)/2 for the
phase u within the knot, 1/π on average, and exactly zero from lag 2·Tc on.
Two hashes per evaluation. Cache z_k per (pair, k) in a small dict pruned
with `prune()`.

Apply it to every link level used after the static `level_of`. Add
`level_at(frame, rsid, t) = level_of(frame, rsid) + fade_db(pair, t)` (None
stays None) and use it for: the lock and header SNRs, each payload block, the
interferers' levels in `verdict_for`/`bench_survives`/`verdict_pairwise`
(evaluate each piece from `segments()` at its midpoint; when σ > 0, also cut
pieces at knot boundaries k·Tc so a piece never spans a knot),
`in_band_energy`, and `takes()` (lead of the new frame over the held one at
the instant). `levels()` stays unfaded.

Gaussian in dB is slow fading / shadowing variation over time, not Rayleigh
multipath. That fits static outdoor nodes, where the time variation comes
from things moving in the path. Rayleigh/Rician fast fading is out of scope.
Say so in "What is deliberately not here" in `ether/INTERNALS.md`.

The testbed's static per-pair shadowing (geodata `shadowing_db`) stays. It is
spread across locations, while `fading_db` is spread over time. Both can be
on.

## Code changes, file by file

**`ether/ether.py`**

- Figures section (top, ~85–195): remove `DEFAULT_CRC_MARGIN_DB` and its
  comment. Add `REFERENCE_FRAME` (121 bytes, cr 5, explicit, CRC on, preamble
  8), `ANCHOR_PER = 0.5`, `DEFAULT_FADING_DB = 0`, `DEFAULT_COHERENCE_S`
  (pick 10 s until calibrated; state that it is a placeholder), each with
  source/reason like the rest.
- New pure functions near `seeded_draw`: `symbol_error(sf, snr_db)`
  (cached), `implementation_loss(sf)` (cached bisection), `frame_blocks(sf,
  bw, cr, payload_len, implicit, crc)` → (first-block symbols, B, symbols per
  block, correcting?), `block_survives(p, n, correcting)`, `fade_db(...)`.
  Delete `crc_margin_fails`.
- `Physics` (~426): drop `crc_margin_db`, add `fading_db` and `coherence_s`
  (validate: ≥ 0, coherence > 0). Keep `as_dict` leaving defaults out, and
  `describe()`.
- `Frame` (~543): also keep `cr` (denominator 5–8, as the station sends it;
  `radio/src/model.cpp` ~753 holds it that way), `implicit = msg.get("hdr")
  == "implicit"`, `crc = msg.get("crc", True)`, `payload_len` =
  `len(base64.b64decode(payload))` (the ether passes the base64 string
  through and still never interprets it; only its length is taken), and
  `pre_symbols = msg.get("pre", 8)`. `tx` already carries all of these:
  `radio/src/ether_link.cpp` `appendState` (~79) is appended to every `tx`
  (`etherPublishTx`, ~232).
- `Reception` (~592): add `hdr_ok` (decided at lock), `ended` (closed at the
  header), and `cause`. `followed_at(t)` is false from `hdr_us` on for a
  reception that ended there.
- `arrive` (~1567) and `arrive_pairwise` (~1627): decodable = matches and
  lock draw passes. For a frame that takes the lock: decide `hdr_ok`. If
  false, set the field in `begin_message` and schedule
  `self.call_at(frame.hdr_us, self.deliver_header_error, reception,
  key=rsid)`. Timers cannot be cancelled (`VirtualClock`/`CoreClock` heaps,
  `RealClock` returns a handle the code does not keep), so `deliver_end`
  must check `reception.ended` and return.
- `begin_message` (~1524): `hdr_ok` parameter, emitted only when false.
- New `deliver_header_error(reception)`: unless `station.locks.get(slot) is
  reception`, nothing, and `deliver_end` rules at `t_end` as today. That one
  test covers every way the receiver stopped following it before `t_hdr`:
  taken off by a louder frame (`lost`), left RX (`abandoned`), and its own
  transmission, where `recv_tx` clears `station.locks` and sets nothing on
  the reception (`talked_over` is read off the interferers at the verdict).
  Else pop the lock, set `ended`, send `rx_end` verdict `hdr` cause `noise`
  (payload included, as every `rx_end` carries it), log, `raise_event(
  self.on_rx, …, "hdr", level)`.
- `verdict_for` / `verdict_pairwise` / `bench_survives`: return (verdict,
  cause). Talked over → `talked_over`; `reception.lost` → `lost`; rejection,
  capture or bench → `interference`. Use `level_at` for interferers (and for
  the frame itself per piece) when fading is on.
- `deliver_end` (~1762): skip if `ended`; after an interference-clean
  verdict, the payload stage. Put `cause` in `rx_end` when not clean.
  `rssi`/`snr` from the mean faded payload level.
- `segments()`: optional extra cut instants (the knots) when fading.
- `takes()`: compare faded levels at `now`.
- `on_rx` (~684, `(sid, rsid, eid, verdict, level)`): add `cause` as a
  sixth argument, so what watches the air sees it without the record.
  `simd.ether_rx` (~575) is the one subscriber.
- `main()` / `serve()` (~2204–2283): remove `--crc-margin-db`, add
  `--fading-db` and `--coherence-s`. `log("physics: …")` uses `describe()`.
- Module docstring (top): mention the staged noise model and fading.
- `ether/core` (the Rust conductor, `ether_core`) is socket and barrier
  code only and never sees a level or a verdict: nothing to change there.

**`radio/src/model.h`, `model.cpp`, `ether_link.cpp`** (the station's SX1262
model)

- `VirtualRxBegin` gains `bool hdrOk`. `ether_link.cpp` `handleMessage`
  `rx_begin` (~120): `f.hdrOk = msg.num("hdr_ok", 1) != 0`. `json.cpp`
  stores a JSON boolean as a number, `true` as 1 and `false` as 0, and
  `json.h` offers only `has`, `num` and `str`, so `num` is the right reader
  (the `cad` flag is read the same way).
- `modelRxBegin` (~1032): remember `hdrOk` on the chip state. Arm `tHdr`
  (`armOnce(c, &c->tHdr, rxHdrCb, …)`, ~1067) with a callback that raises
  `IRQ_HEADER_ERR` and calls `dropLock` (~497, which clears `lockId` and
  stops the preamble, sync and header timers) when not ok, else `rxHdrCb` as
  today.
- `VirtualRxEnd.headerOk` and the `"hdr"` branch in `handleMessage`
  (`f.crcOk = verdict != "crc" && verdict != "hdr"; f.headerOk = …`, ~148)
  and the `IRQ_HEADER_ERR` line in `rxEndCb` (~1002, where today a header
  error is raised together with `RX_DONE` and the payload still copied in):
  remove. A header error now never arrives as an `rx_end` the chip acts on:
  by then `lockId` is 0 and `modelRxEnd` (~1075) returns at its `f.id !=
  d.lockId` check.
- The mode after a header error: the model has no single-RX mode
  (`CMD_SET_RX`, ~709, parses no timeout and the chip stays in RX after
  `RX_DONE`), and `dropLock` leaves the mode alone, so the chip stays in RX
  and hunts again, which is continuous RX on the SX1262. Nothing to add. The
  firmware side (`iface-lora/esp-idf/src/lora_radio.cpp` ~493) already just
  forgets the frame on `irqHdrErr`, so it is silent there, as on hardware.

**`testbed/`**

- `simd.py`: remove `--crc-margin-db` (~2187, ~2232) and the
  `Physics(self.args.noise_figure, self.args.crc_margin_db, …)` call (~432).
  Add `--fading-db`, `--coherence-s` and pass them through. The `"medium"`
  dict sent to the page (~1055) holds only `noise_figure_db` and `pairwise`,
  and the page reads only the noise figure (`ui/src/pages/NodesPage.vue`
  ~505); bench capture and the oracle are not shown there either, so leave
  fading out of it. `ether_rx` (~575) relays the verdict to the page; pass
  `cause` along in the `"rx"` broadcast too.
- `convert_scenario.py` (~80, ~111, ~428–456): old scenarios' `crc_band_db`
  maps to `--crc-margin-db` (~441). The "has no flag" warnings are
  hand-written per key in `medium_flags`, one `warnings.append` each, not a
  generic mechanism: drop the mapping, add one such line for a non-zero
  `crc_band_db`, drop the key from `OLD_PHYSICS` (~111) and fix the docstring
  (~80). Update `test_convert_scenario.py` (~82, ~196, ~314–315 expect
  `--crc-margin-db`).
- `referee.py` `collisions()` (~340): it keeps receptions with verdict
  `crc`. Make it keep only `cause == "interference"` (its question is
  collisions). `hdr` is never a collision now.
- `compare.py` `received()` (~178–195): counts every non-clean as `rx_crc`
  and as the sender's `collided`. `collided` should count only cause
  `interference`. Keep `rx_crc` as all non-clean, or split noise from
  interference in its report rows (~323–334). Prefer the split: that is the
  number to put next to the field's `stats.crc_err`.
- `links.py` (~111–121) and `airtime.py` (~113–131, ~225): count non-clean as
  `crc`. Fine as is, or split by cause the same way. `links.py` ~181 records
  `noise_figure_db` from physics; add the fading settings there too.
- `seq.py` already draws `hdr` (~36–37).
- `testbed/ui`: the page never sees `rx_end`; it gets simd's `"rx"`
  broadcast. `ui/src/stores/sim.ts` types the verdict as `'clean' | 'crc'`
  (~78, and the cast at ~388): widen to `'clean' | 'crc' | 'hdr'` and add
  `cause`. `GroundMap.vue` (~1196) paints clean green and anything else red,
  so `hdr` already renders as a failed reception.

**`ether/test_ether.py`**

- Remove the CRC band tests (~726–787:
  `test_the_crc_band_fails_frames_in_proportion…`,
  `test_a_physics_without_a_crc_band_says_nothing_of_one` (rewrite for the
  fading keys), `test_the_crc_band_verdict_is_the_draw…`,
  `test_without_a_crc_band_a_frame_over_its_threshold_is_clean`).
- `test_a_frame_under_its_spreading_factors_threshold_is_not_delivered`
  (~400: losses of 143 and 144 dB around a threshold at 143.53, so 0.47 dB
  in and 0.53 dB out) and every test that places a link a fraction of a dB
  above or below a threshold need re-reading. With the curve, 0.5 dB over
  the threshold is no longer certain. Move such tests' margins to ±3 dB or more where they
  mean "certainly in/out", and keep the threshold arithmetic beside them.
  The hand-built hidden-terminal case and its written-out calculation
  (README "Tests") must still hold. Its links are well above threshold, so
  they should.
- Helpers already send `cr`, `hdr`, `crc`, `pre` (`radio()`, ~65).
  `Bench`, `level_for`, `NOISE_DBM`, `POWER_DBM`, `listen` are the existing
  fixtures.
- New tests:
  - `symbol_error` against reference values: SF7 at −7.5 dB is about
    5.2e−4, SF9 at −12.5 dB about 1.0e−4, SF12 at −20 dB about 2.2e−6
    (ideal, no implementation loss). Monotonic in SNR.
  - Implementation loss puts the reference frame's PER at 50% at each SF's
    threshold.
  - Length: at the same margin, a 200-byte frame fails more often than a
    20-byte one over ~2000 seeded frames (in-process, as the CRC band test
    did, without the UDP bench).
  - Coding rate: 4/8 survives where 4/5 fails at the same SNR, statistically.
  - Stages over the wire: well under the curve → no `rx_begin` (or `cad`
    only); a forced header failure (choose a seed where the "hdr" draw fails
    at a chosen margin) → `rx_begin` with `hdr_ok: false`, `rx_end` `hdr` at
    `t_hdr`, and a second frame starting after `t_hdr` can take the slot;
    payload failure → `crc` with cause `noise` at `t_end`.
  - Determinism: same seed → same verdicts whatever order receptions end in
    (two receivers, swap station ids).
  - Fading: σ = 0 gives exactly the unfaded levels. g(t) has mean ~0 and
    variance ~1 over many knots, is continuous at knots, and is symmetric in
    the pair's names. Correlation at lag Tc/10 is high, about 1/π at lag Tc
    averaged over phase, and zero at lag 2·Tc.
  - Causes: talked over, lost, interference and noise each show in `cause`.
  - A header error does not reach a receiver that transmitted before
    `t_hdr`: it gets its `rx_end` at `t_end`, cause `talked_over`.
- `radio/tests/` holds pytest files (`test_model.py`, `test_conductor.py`,
  `test_shim.py`, `test_portduino_idle.py`), not CMake targets. Add to
  `test_model.py`: `rx_begin` with `hdr_ok:false` raises `HEADER_ERR` and no
  `RX_DONE`, the chip stays in RX, and the later `rx_end` is ignored.

## Documentation (once the tests pass)

- `ether/README.md`: "What it models" (~24–150): the demodulation threshold
  paragraph (now the median of a curve), the "two tests" section (the noise
  test becomes the three stages), replace "The CRC band" with the stages and
  fading, the "Absent at this depth: fading" sentence, the command line at
  the top (~14), "The wire" tables (`hdr_ok`, `cause`, `hdr` verdict at
  `t_hdr`), the physics example (~159).
- `ether/INTERNALS.md`: "The noise, and the demodulation threshold" (~200),
  "Reception: two tests…" (~283), "The figures" (~324), "Delivery" (~417:
  `rx_end` goes out at `t_end` *or* at `t_hdr` for a header error), and
  "What is deliberately not here" (~448: fading is now here, optional;
  remove the CRC band bullet; add interference-in-header, CRC-off, reduced
  rate, Rayleigh).
- `sim-mesh/INTERNALS.md` ~1793 ("The CRC band by default" open item):
  remove it, and replace it with the calibration open item below if not done.
- `sim-mesh/README.md` ~1648 (simd's flags).
- `docs/INTEGRATION_REPORT_2026-09-29.md` and
  `docs/CAPABILITY_MATRIX_2026-09-29.md` mention the CRC band. They are dated
  reports: leave them.
- Every description states the model as it now is, with no "previously",
  "replaces" or "used to".

## Calibration

Three numbers: the implementation loss (or anchor PER) per SF, σ, and Tc.
Today they are the 50% anchor, 0 and a placeholder.

- **Waterfall position and width**: the tools/rncapture bench (reticulum
  project, used for `BENCH_*`) with a step attenuator. Sweep in 0.5 dB steps
  through the threshold at SF7/125 kHz with 20- and 200-byte frames, ~200
  frames per step. Count clean, CRC error and nothing received
  (`stats.crc_err` and the receive count). The model's ideal waterfall at
  SF7 for 121 bytes runs from 10% lost at −7.3 dB to 90% lost at −8.8 dB,
  1.5 dB wide, so 0.5 dB steps give three or four points on the slope. Fit
  the implementation loss to the 20-byte curve, then check that the
  200-byte curve lands where the model puts it without further fitting. That is the test that the length effect
  is real. If time allows, repeat at SF12.
- **Fading**: from deployed nodes, per link: the frame-to-frame RSSI series
  (the firmware's per-frame telemetry record, `loraMonPush` in
  `lora_mon.cpp` ~1078, carries an RSSI for every frame, CRC failures
  included; `lora_peers.cpp` ~193 keeps per-peer last, min and max) gives σ
  (spread around the
  link's long-run mean) and Tc (autocorrelation decay). The share of CRC
  errors among receptions (`stats.crc_err` against received frames) is the
  check. A sim run of the same nodeset with the fitted σ, Tc should give the
  same order of magnitude. If the sim gives far more CRC errors, look first
  at Tc (too short makes frames straddle fades) and then at whether the
  lock stage is too lenient relative to the payload stage.

## Out of scope

Soft interference (feeding signal-to-interference-plus-noise ratio into the
same curve instead of rejection thresholds); interference inside the header
ending a frame at `t_hdr`; Rayleigh/Rician fast fading; corrupted payloads
with CRC off; preamble-length matching (the ether still ignores `pre` for
matching). Note each where INTERNALS lists what is not modelled.
