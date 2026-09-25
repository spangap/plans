# town100 run K: reviewer notes

Five changes against run J, one hw-linux build, the town100 pair re-run on it
(lora and supe, channel plan 1), then a third run of the same town with SUPE on
channel plan 0. The run report is `simreport-runK.md` at the workspace root.

## The changes

### 1. LoRa neighbour table, 24 to 64 rows

- `iface-lora/esp-idf/src/lora_peers.h`: `NEI_MAX` 64. The `NEI_IDS_MAX`
  comment's cost figure restated for 64 rows (16 B per slot per row, 8 KB a
  radio) and its history sentence dropped.
- Every use of `NEI_MAX` read (lora_peers.h/.cpp, lora_observe.cpp,
  lora_cli.cpp, lora_mon.cpp, lora.cpp, lora_supe.cpp). Nothing breaks at 64:
  - every walk is an `int` loop to `NEI_MAX`;
  - row ids travel as `uint16_t` (`peersIdOf`, the queue's `peer_id`,
    `NeiLink::initPeer/respPeer`, `NeiSeen::from`) with `LORAQ_PEER_NONE` =
    0xFFFF as the sentinel, far above 63;
  - `NeiHash::node` is a `uint8_t` row index with no sentinel (it holds 0..63);
  - the rnsd origin key carries `slot + 1` in two bytes;
  - no bitmask is indexed by row (the only 32-bit mask in the component,
    `s_annNowMask` in lora.cpp, is per radio);
  - the CLI listing, `publishPeers` and `publishMeas` have no fixed buffer
    sized by the row count: they print or write one row at a time
    (`char k[48]` keys, per-row tag arrays sized by the per-row maxima).
    More rows means a longer `lora n` and `show lora.0.meas` reply, and both
    replies already reach the console's 8 KB reply bound at the busiest
    stations (run I open item 6).
- Beyond the constant: two counters on `NeiState` (`evicted`, `goneSilent`),
  bumped in `peersAlloc` when the slot taken was in use and in `peersExpire`,
  and a second header line in `lora <n> neighbors`:
  `  table <used> of 64 rows; <n> evicted, <n> gone silent`. This is how run K
  measures the table's occupancy and evictions; before it, the only trace was
  rnsd's `peer left` line, printed only when a withdrawal dropped a route.
  Documented in iface-lora/INTERNALS.md §13 and §13.1, README example updated.
- RAM (`sizeof`, compiled against the header on this host; every member is at
  most 4-byte aligned and there are no pointers, so the layout is the same on
  the 32-bit ESP targets):

  | | `Neighbor` | `NeiState` (one per radio) |
  |---|---|---|
  | `NEI_MAX` 24 | 888 B | 23 588 B |
  | `NEI_MAX` 64 | 888 B | **59 116 B** (+35 528 B) |
  | 64, `CONFIG_LORA_NO_SUPE` | 880 B | 58 508 B |

  Allocated once per radio by `peersInit` through `gp_alloc`: PSRAM when the
  board has it (every ESP32 board straddle in this workspace sets
  `CONFIG_SPIRAM`), internal DRAM on a board without PSRAM, where the 59 KB
  comes out of the internal heap. A failed allocation leaves the table off.
- Found in the probe, not changed (the build had already been made): rnsd's
  neighbourhood (`rns/esp-idf/src/rnsd_peers.cpp`) holds 32 nodes
  (`RNSD_NODES_MAX`, capped at 32 by the 32-bit publish mask `s_pubNodes`)
  and 32 peers. Each LoRa row is declared to it as a node, and on plain LoRa
  a neighbour takes two rows (its `lxmf.delivery` identity and its transport
  identity are different identities, and nothing folds them without a SUPE
  announcement), so a station hearing more than 16 neighbours overflows it.
  rnsd then logs `peers: node table full (32)` per declaration and leaves
  that node out of the neighbourhood; routing is unaffected, but a departing
  undeclared node drops no routes. In the probe (before any traffic) 48
  stations logged 460 such lines.

### 2. Community radius 25

`SIMesh/testbed/scenarios/gen_town100.py`: `set s.lora.0.community_radius 25`
in the shared setup, **before `lora up`**, because the radio hands the radius
to rnsd at registration and rnsd caches it for the life of that registration
(iface-lora/README.md); after `lora up` it would take effect only at the next
boot. Regenerated with seed 1: `town100-lora.yaml` and `town100-supe.yaml`
differ from run J's files only in that line and the two header comment lines,
and from each other only in the two SUPE lines; 33 transports. The probe's
`rnstatus` answers `Community    within 25 hops`.

The generator also writes `town100-supe0.yaml` (SUPE on channel plan 0), which
differs from `town100-supe.yaml` only in `set s.lora.0.SUPE.afa 0`.

### 3. Directory: two announce blobs per budget unit

- `rns/esp-idf/components/microreticulum/src/Directory.h`: the unit is named
  by `RDIR_UNIT_GUARDS` 8, `RDIR_UNIT_DIRS` 4, `RDIR_UNIT_BLOBS` 2, and
  `RDIR_BUDGET_UNIT` is priced from them: 8 × 28 + 4 × 160 + 2 × 320 = 1504 B.
- `Directory.cpp` `rdirInit`: the carve uses the same three constants (the
  unit's byte cost with the configured blob slot size, then `units × 8`,
  `units × 4`, `units × 2`). At the 2048-unit clamp the guard count is 16 384,
  inside `uint16_t`.
- `rdirSnapshot` / `imageLoad` carry counts, not geometry: the image header
  holds record counts and slot sizes, the loader refuses only an image with
  more records than the pools or different slot sizes, and blobs follow
  directory records one-for-one at most. Nothing assumes the old ratio. A
  run J image (at most 125 blobs) loads into 250 slots.
- `rns/esp-idf/src/rnsd.cpp` `rnsdDirUp`: the routes-to-units rounding uses
  `RDIR_UNIT_DIRS` instead of a literal 4; the ceiling for
  `s.rnsd.path.max` = 500 is 125 units = 188 000 B.
- Docs: rns/README.md `s.rnsd.dir.budget_kb` row (1504 B, two announce
  blobs); rns/INTERNALS.md §§ pool split (8 : 4 : 2, 1000 : 500 : 250 at the
  500-route budget) and the seen-pool size in the persistence section.
- Confirmed at first boot of the new binary (probe, t055):
  `rdir: 188000 B arena — guard 1000 x 28, directory 500 x 160, blob 250 x 320`;
  `rnsd memory`: `dir entries 53 / 500`, `dir blobs 52 / 250`.

### 4. Rate-step margin 6 dB

- `iface-lora/esp-idf/src/supe.h`: new `SUPE_RATE_MARGIN_DB` 6, beside
  `SUPE_TARGET_MARGIN_DB` 10, each with its own comment.
- `supe_engine.cpp` `chooseBudget`: `affordDeci = headroomDeci −
  SUPE_RATE_MARGIN_DB × 10`. This is the only rate-step selection site.
- **`lora_supe.cpp` line ~506 left on `SUPE_TARGET_MARGIN_DB`.** It is not a
  rate-step site: on `SUPE_EV_TRAIN_OK` it calls `apSucceeded` (the power
  ratchet's one-dB trim, iface-lora/INTERNALS.md §15.3 "only when the peer's
  report carried real headroom") only when the reported margin exceeds the
  target plus 3 dB. Moving it to 6 would let the power controller trim at
  9 dB of margin instead of 13, which is a power change, and the brief keeps
  the power controller's target at 10. `lora_power.cpp` `apDerive` also
  unchanged.
- Spec: SUPE.md §14.3 states the rate table, its order, what an index means
  and the conformance vectors; it states no margin a step must keep. The
  choice is the READY sender's (§6, §14.6 "the rate step chooser's"), on its
  headroom reading, and the number is the implementation's. SUPE.md and
  reticulous/docs/supe.html are unchanged. §15's "10 dB of margin" is the
  example of a wrong absolute-power computation, not a rate-step rule.
- `supe-rate-table-vectors.txt` untouched: the vectors assert table
  membership, order and resolution, none of which the margin touches;
  `supe_core_test` passes 1493 checks against them and regenerates
  `golden.txt` identically.
- `iface-lora/esp-idf/test/supe_engine_test.cpp`: the stub air's default SNR
  goes from 6 dB to 2 dB, because at 6 dB the new margin affords SF5 instead
  of the SF6 step the dialogue test is written around (`B retuned to SF6 for
  the train: got 5 want 6`). 2 dB affords exactly one step (headroom 9.5 dB,
  3.5 dB to spend, SF6 costs 2.5). 228 checks, 0 failed.
- Documented in iface-lora/INTERNALS.md §15.1.

### 5. Drain 1200 s

`traffic.py --drain 1200` on every run K drive. No code change.

## Build

One build, after all edits, `spangap build reticulous/reticulous --with
spangap/hw-linux --with reticulous/netgraph -x …` (the SIMesh README's list),
02:13:25–02:13:41 UTC, exit 0. **Target: hw-linux**,
`reticulous/esp-idf/build.linux/reticulous.elf`, 02:13 UTC. No other target was
built.

## Run K, two variants

Both runs 02:15:39–02:30:07 UTC, full report in `simreport-runK.md`.

| | lora J | lora K | supe J | supe K |
|---|---|---|---|---|
| Delivered of 720 | 299 (41.5%) | **236 (32.8%)** | 252 (35.0%) | **309 (42.9%)** |
| No path at send | 387 | 64 | 386 | 63 |
| Delivered, route hops 1 / 2 / 3 | 121/123, 61/94, 56/114 | 125/137, 62/115, 32/127 | 118/122, 54/101, 40/111 | 135/136, 82/113, 68/137 |
| Latency median / p90 (s) | 109.7 / 2376 | 107.4 / 2405 | 27.8 / 1195 | 25.6 / 2006 |
| Calling channel busy, median (max) | 28.1% (47.9%) | 35.3% (58.0%) | 19.2% (32.3%) | 20.1% (32.1%) |
| Calling-channel airtime per station, mean | 52.3 s | 68.8 s | 36.9 s | 42.3 s |
| CRC failures of receptions | 18.0% | 23.2% | 15.9% | 17.9% |
| Hails unanswered | – | – | 49.6% | 56.3% |
| Exchanges with any frame below 14 dBm | – | – | 41.9% | 38.8% |
| Traffic-channel frames above SF7/125 kHz | – | – | 7.4% | 19.6% |
| Directory entries max (of 500) / blobs full stations / evictions | 161 / 22 / 280 | 197 / 0 / 0 | 153 / 22 / 210 | 194 / 0 / 0 |
| Neighbour-table rows max (of 64) / evictions | – | 36 / 0 | – | 36 / 0 |
| `peer left` withdrawals / routes dropped in the hour | 626 / 906 | 0 / 0 | 462 / 678 | 0 / 0 |
| Reload: warm, at all-up, a minute later | 7005, 6116, 5132 | 16 820, 14 158, 16 802 | – | – |
| Pace whole run / wall | 17.3x / 334 s | 18.7x / 516 s | 10.5x / 551 s | 11.1x / 867 s |

Sends without a path fell by five sixths in both variants, nothing was evicted from the directory or the neighbour table, and a reloaded warm snapshot keeps its paths; supe turned that into 57 more deliveries than run J, at every route length. lora delivered 63 fewer, 1-hop sends included, on a calling channel busy a median 35.3% of the time with 23.2% of receptions failing their CRC.

Verification: `rdir: 188000 B arena — guard 1000 x 28, directory 500 x 160,
blob 250 x 320` at first boot; `Community    within 25 hops`; `supe_engine_test`
228 checks, `supe_core_test` 1493 checks, 0 failed; `peerleft.py` reproduces
run J's 626 / 906 and 462 / 678 from J's logs.

## Run K, channel plan 0

`town100-supe0.yaml` (the generator's third file, `set s.lora.0.SUPE.afa 0`),
same binary, alone on 9013 / 7002 / 127.0.8.0/22 after the pair, 02:39:13–
02:48:30 UTC, same recipe, drain 1200 s, snapshots `town100-supe0-warm` and
`town100-supe0-1h`. Every station answered `SUPE.afa = 0`, `SUPE.enable = 1`.

| | lora K | supe K (plan 1) | supe K plan 0 |
|---|---|---|---|
| Delivered of 720 | 236 (32.8%) | 309 (42.9%) | **232 (32.2%)** |
| No path at send | 64 | 63 | 48 |
| Delivered, route hops 1 / 2 / 3 | 125/137, 62/115, 32/127 | 135/136, 82/113, 68/137 | 135/137, 60/113, 29/135 |
| Latency median / p90 (s) | 107.4 / 2405 | 25.6 / 2006 | 99.1 / 2018 |
| Calling channel busy, median (max) | 35.3% (58.0%) | 20.1% (32.1%) | 33.9% (56.1%) |
| CRC failures of receptions | 23.2% | 17.9% | 19.9% |
| Hails out / unanswered | – | 23 859 / 56.3% | 15 780 / 38.1% |
| Exchange frames above SF7/125 kHz | – | 19.6% | 20.0% (SF6 14.3%, SF5 5.7%) |
| Exchange frames below 14 dBm | – | 19.8% | 19.3% |
| Transport airtime: calling / per traffic channel / total (s) | 161.3 / – / 161.3 | 104.8 / 9.8 / 193.2 | 166.2 / – / 166.2 |
| of the calling channel: announces and path / HAIL / other SUPE / unicast | 80.2 / – / – / 81.1 | 66.8 / 22.8 / 0.1 / 15.0 | 83.2 / 13.8 / 11.6 / 57.5 |
| Endpoint airtime: calling / per traffic channel / total (s) | 23.2 / – / 23.2 | 11.4 / 1.7 / 26.4 | 24.6 / – / 24.6 |
| Directory / neighbour-table evictions | 0 / 0 | 0 / 0 | 0 / 0 |

Plan 0 answers more hails and flies as many fast and low-power exchange frames
as plan 1, but with every exchange back on the calling channel it is as busy
there as plain LoRa and delivers as plain LoRa does, 77 fewer than plan 1.

### Tooling added for it

- `SIMesh/testbed/airtime.py --roles` (needs `--scenario`): airtime per
  transport and per endpoint, calling channel split into announces and path
  traffic, SUPE HAIL, other SUPE frames and unicast payload, per traffic
  channel and total. In its docstring and the SIMesh README code table. Run
  over run J's two records as well, so the J columns come from the same code.
  `pytest SIMesh/testbed`: 28 passed.
- `gen_town100.py` writes `town100-supe0.yaml`; SIMesh README row updated.
- Scratchpad only: `calpow.py` (calling-channel power and rate steps of frames
  aimed at one node, for plan 0), `peerleft.py`, `dirstats.py`,
  `reloadcmp.py`, `sideJK.py`; `callkind.py` no longer indexes past a short
  HEADER_2 frame (it failed on supe K's record).
