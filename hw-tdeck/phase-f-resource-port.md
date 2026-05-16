# Phase F — Resource engine port (executable spec)

Branch `phase-f-resource`. This is the actionable port plan; the
scoping/decision rationale is in [link.md §9.0 ⚠️](link.md). Reference
(Apache-2.0, re-fetch into gitignored `research/` if absent):

```
research/ratspeak-mr/{Resource.cpp,Resource.h,ResourceData.h}
# raw.githubusercontent.com/ratspeak/microReticulum/master/src/<f>
```

Do **not** copy ratspeak/ratdeck (AGPL). `ratspeak/microReticulum` is
Apache-2.0 — algorithm reference only; adapt to our fork's APIs.

## Operating directive (READ FIRST)

This is a long mechanical port, not a research task. **There is no
remaining architectural decision** — approach A is locked, the
reference is chosen, the API deltas and wiring points are mapped
below. "Large" and "not incrementally hardware-verifiable until
substantially complete" (§9.0) are **not** stop conditions.

- **Do not stop** until all four build/verify steps are done and the
  §9.5 acceptance has been attempted on hardware, OR you hit a genuine
  *unfixable* blocker (a real one — a missing primitive that cannot be
  ported, hardware unavailable, etc.), OR a *new* architectural fork
  that did not exist at spec time. Running low on context is itself a
  reason to keep moving and commit progress, not to pause for
  acknowledgement.
- **Decide small things autonomously.** Naming, helper placement,
  log-level choices, struct layout — just choose and proceed. Do not
  ask "should I continue / proceed / commit per step" — the answer is
  always yes; this document is the standing authorization.
- **Report faithfully, never overclaim.** A step is "done" only when
  it builds with **0 warnings** (`feedback_fix_warnings`); the port is
  "verified" only after the §9.5 hardware soak passes. Mark WIP as WIP.
- Commit each numbered step as its own commit on this branch as you
  finish it — never batch-at-the-end, never wait to be told.

## Decision (locked): approach A

Keep the attermann-shaped `Resource` / `ResourceAdvertisement` /
`Resource::accept` API that our `Link.cpp` already calls. Implement the
bodies with ratspeak's algorithm inside `ResourceData`. Minimal
`Link.cpp` churn. Do **not** adopt ratspeak's `OutboundResource`/
`InboundResource` class split (that would force porting ratspeak's
Link.cpp too — different fork lineage).

## Fork API deltas to adapt (verified 2026-05-16)

- **`Bytes` has no single-byte `append(uint8_t)`** — only
  `append(const Bytes&)`, `append(const Data&)`,
  `append(const uint8_t*,size_t)`, `append(const void*,size_t)`.
  ratspeak's msgpack helpers call `buf.append((uint8_t)b)` constantly.
  Add a local `static inline void ab(Bytes&, uint8_t)` helper
  (`uint8_t v=b; buf.append(&v,1);`) and s/`.append((uint8_t)…)`/`ab(buf,…)`/.
- **`Identity::full_hash(const Bytes&) -> Bytes`** exists; use for
  `get_map_hash` / `compute_resource_hash` / `compute_expected_proof`
  (ratspeak uses the same).
- **`Identity::get_random_hash() -> Bytes`** exists — confirm it yields
  ≥4 bytes; resource random_hash is 4 (`Type::Resource::RANDOM_HASH_SIZE`).
- **`Link::encrypt/decrypt(const Bytes&) -> const Bytes`** exist
  (Link.h:223-224) — ratspeak's `link.encrypt/decrypt` map directly.
- **`Type::Resource::SDU`** = `Packet::MDU`; `MAPHASH_LEN`=4,
  `WINDOW`=4, `RANDOM_HASH_SIZE`=4, `status` enum already matches
  ratspeak's `ResourceStatus` value-for-value (NONE=0…CORRUPT=8).
- **Logging:** our fork uses microReticulum LOG macros (DEBUGF/
  WARNINGF/…), not ratspeak's `WARNING()`. Map accordingly.
- **`ResourceAdvertisement` is already ported** in our `Resource.h`
  (t/d/n/h/r/o/i/l/q/f/m + pack/unpack/is_request/is_response/
  read_request_id/read_size/read_transfer_size). Cross-check its wire
  against ratspeak's `pack()`/`unpack()` (both target Python
  Resource.py — fixmap(11), keys t,d,n,h(32),r(4),o(32),i,l,q(bin|nil),
  f(uint flags),m(bin)). Reuse ours; only fill gaps.

## Engine state — extend `ResourceData`

`ResourceData` currently holds `_link,_hash,_request_id,_data,_status,
_size,_total_size,_callbacks`. Add (covers both roles; role flag):

```
bool                 _outbound;
std::vector<Bytes>   _parts;            // enc chunks (out) / slots (in)
Bytes                _hashmap;          // concat 4-byte map hashes
uint8_t              _resource_hash[32];
uint8_t              _random_hash[4];
uint8_t              _original_hash[32];
Bytes                _expected_proof;   // out
std::vector<std::array<uint8_t,4>> _map_hashes;  // in
size_t _transfer_size,_data_size,_total_parts,_received,_window;
ResourceFlags        _flags;            // add ResourceFlags to Resource.h
double               _started_at, _timeout;
Bytes                _assembled;        // cached for proof/data()
```

## `Resource` body map (ratspeak fn → our member)

| our `Resource` method | ratspeak source | notes |
|---|---|---|
| ctor(data,link,advertise,…) outbound | `OutboundResource::init` + `get_advertisement` | chunk/encrypt(link)/random_hash/resource_hash/hashmap/expected_proof; status ADVERTISED; if `advertise` send RESOURCE_ADV packet (see Link wiring) |
| `static Resource accept(packet, concluded_cb, [progress], [request_id])` | `InboundResource::init` | parse `ResourceAdvertisement::unpack(packet.plaintext())`; alloc slots; status TRANSFERRING; stash cb; emit initial RESOURCE_REQ |
| `receive_part(Bytes)` (new) | `InboundResource::receive_part` | map-hash match → slot; on complete → `assemble` |
| `assemble()` (new, internal) | `InboundResource::assemble` | concat → `link.decrypt` → strip 4B rh → (compressed⇒CORRUPT) → `_data`; status COMPLETE; fire concluded |
| `handle_request(Bytes)->parts` | `OutboundResource::handle_request` | for RESOURCE_REQ |
| `validate_proof(Bytes)` | `OutboundResource::handle_proof` | COMPLETE + fire concluded |
| `generate_proof()` (new) | `InboundResource::generate_proof` | for RESOURCE_PRF send |
| `cancel/get_progress/status/hash/data/request_id/size/total_size` | trivial | back by `_object` |

## Link.cpp wiring (replace //p///z sketches, lines ~1158-1297)

- `RESOURCE_ADV`: `ResourceAdvertisement::unpack(packet.plaintext())`;
  if `is_request` → `Resource::accept(... _request_resource_concluded)`;
  `is_response` → match pending_request, `accept(... request_id=)`;
  else → `accept(... _callbacks.resource_concluded)`; insert into
  `_object->_incoming_resources`; send initial RESOURCE_REQ.
- `RESOURCE` (data part): for each `r : _incoming_resources`
  `r.receive_part(packet.data())`; on complete assemble → concluded →
  send RESOURCE_PRF; remove from set.
- `RESOURCE_REQ`: for each `r : _outgoing_resources`
  `idx=r.handle_request(plaintext)`; send those parts as RESOURCE
  context packets; window/flow per ratspeak (initial: send all
  requested, no retransmit window v1 — note as limitation).
- `RESOURCE_PRF`: for each `r : _outgoing_resources`
  `r.validate_proof(plaintext)`; COMPLETE → concluded → erase.
- `RESOURCE_HMU` / `RESOURCE_ICL`: HMU = hashmap-update (multi-segment;
  v1 single-segment resources don't need it — log+ignore). ICL =
  initiator-cancel → cancel matching incoming resource.
- Outbound send path: `Resource` ctor with `advertise=true` must emit
  the RESOURCE_ADV packet on the link (Packet(link, adv.pack(),
  context=RESOURCE_ADV).send()). Wire where Link.cpp:495
  (`request_resource`) / :891 (`response_resource`) construct Resources,
  and a new `Link` API for lxmf-driven big sends.

## rnsd + lxmf (after engine compiles & Link wired)

- rnsd §9.1: `set_resource_callback`/`_started`/`_concluded` on Link in
  `onIncomingLinkEstablished` + outbound slots. Policy gate
  `s.lxmf.max_resource_size` (262144). `malloc(total_size)` PSRAM,
  fill via engine, hand to consumer via aux on
  `LXMF_LINK_RESOURCE_AUX_PORT(101)` (`rnsd_link_resource_done_t`,
  ports.h). Progress → `rnsd.links.<tag>.resource.<rid>.*`.
- lxmf Phase F: open port 101; on INBOUND_DONE take buf ownership,
  feed `onInboundLxm`, `rnsdResourceRelease`. Outbound: `processReady`
  oversize-direct path sends via Resource instead of one Link packet.

## Build/verify strategy (§9.0: not incrementally HW-verifiable)

1. Engine bodies in Resource.cpp + ResourceData/Resource.h + add
   `ResourceFlags`; keep Link.cpp untouched first → `idf.py --diptych
   build` green (engine compiles standalone, 0 warnings).
2. Wire Link.cpp contexts → build green.
3. rnsd §9.1 + lxmf 101 → build green.
4. HW: `lxmf send <echo> <16KB lorem>` → echo Resource reply → lands +
   sig-verifies; `<128KB>` round-trip; advertise-and-drop → FAILED, no
   leak; 10× 64KB → flat PSRAM HWM (link.md §9.5).

Each numbered step is one reviewable commit on this branch.

## Branch / fork discipline

- All Phase F work happens on reticulous branch **`phase-f-resource`**
  (already created; `git switch phase-f-resource`). Tested A–E is on
  `main` beneath it (`e2bb0ae`).
- **Never commit to `main`.** One commit per numbered step (§"Build/
  verify"), on this branch. Do not push (user controls remotes).
- **diptych stays on `main`** — Phase F is reticulous-only
  (`components/microreticulum/` + `main/lxmf.cpp` + `main/rnsd.cpp`).
  No diptych-core changes are expected; if one becomes necessary that
  is a *new* fork — surface it.
- Don't `git add` `web-interface/package-lock.json` (deliberately
  untracked since `7f87eaf`). `research/` is gitignored by design.

## Build / flash / test environment

The bare `idf.py` wrapper has a silent-exit-1 bug (sourced-probe;
see diptych `docs/development.md`). Reconstruct the working wrapper
(machine-local, not in git):

```sh
# /tmp/idfenv.sh — eval activate -e env, prepend good node, exec real idf.py
ACT="$HOME/.espressif/tools/activate_idf_v5.5.4.sh"
E="$(sh "$ACT" -e 2>/dev/null)"
export IDF_PATH=$(printf '%s\n' "$E"|sed -n 's/^IDF_PATH=//p')
export IDF_PYTHON_ENV_PATH=$(printf '%s\n' "$E"|sed -n 's/^IDF_PYTHON_ENV_PATH=//p')
export IDF_TOOLS_PATH=$(printf '%s\n' "$E"|sed -n 's/^IDF_TOOLS_PATH=//p')
export ESP_ROM_ELF_DIR=$(printf '%s\n' "$E"|sed -n 's/^ESP_ROM_ELF_DIR=//p')
export PATH="$HOME/.nvm/versions/node/v22.22.2/bin:$(printf '%s\n' "$E"|sed -n 's/^PATH=//p'):$(printf '%s\n' "$E"|sed -n 's/^SYSTEM_PATH=//p')"
exec "$IDF_PYTHON_ENV_PATH/bin/python" "$IDF_PATH/tools/idf.py" "$@"
```

- **Build:** `cd reticulous && umask 000 && /tmp/idfenv.sh --diptych
  build` (the `--diptych` flag pulls the sibling diptych-core checkout,
  required — the storage fix lives there). Confirm `EXIT=0` and
  `grep -ciE 'warning:' == 0`.
- **Flash:** host runs a flasher daemon. From the VM:
  `flasher host /Volumes/code/diptych/reticulous` touches
  `build/flashme`; daemon flashes + reboots. Wait until `build/flashme`
  is consumed, then poll `diptych-cli date` for a `2026-*` reply.
- **Device output is NOT tee'd here** (`build/flasher.log` is stale).
  Interrogate live via `diptych-cli "<cmd>"` (default host
  `reticulous.local` = 192.168.2.22, CLI port persists across flash),
  the dev-loop `.rns/rnsd.log`, and storage observability
  (`diptych-cli "show rnsd.links"`, `"show rnsd.mailbox"`,
  `"rnsd links"`, `"lxmf msgs"`).
- **Test rig (host, this machine = 192.168.2.21):**
  `scripts/rns up &` (dev-loop rnsd, :4242 + shared :37428);
  `scripts/lxmf-echo --echo &` (LXMF/Link peer — **DIRECT by
  default**, prints `READY <hash>`; `--opp` forces opportunistic).
  For Resource specifically the echo must reply with a payload >
  ~440 B so it goes out as a Resource (extend lxmf-echo if needed —
  e.g. `--pad N` to fatten the echo body; that is a small autonomous
  change, just do it).
- `scripts/announce-sniff [aspect]` discovers a device-hosted dest
  hash via the shared instance.

## Definition of done

Phase F is **done** when, on `phase-f-resource`:

1. Steps 1–3 each built green (0 warnings) and committed.
2. §9.5 hardware acceptance attempted and results recorded honestly in
   this file + `link.md` (status box) + memory
   (`project_link_resource_unported` → flip to done, or record the
   precise failure):
   - `lxmf send <echo> <~16 KB>` → echo Resource reply lands, signature
     verifies, persists in `lxmf msgs`.
   - `<~128 KB>` round-trips.
   - peer advertises then drops → FAILED aux, buffer freed, no leak.
   - peer advertises > `s.lxmf.max_resource_size` → rejected at
     advertisement, no allocation.
   - 10× 64 KB sequential inbound → flat PSRAM HWM (`top`/heap).
3. Then update `link.md` §11 table (F ✅), `MEMORY.md` pointer, and
   stop — Phase G (its own §10 ⚠️ blocker) is a *separate* decision,
   not part of this branch's mandate.

If §9.5 reveals the port is functionally incomplete, that is normal
for a port this size — keep going (fix and re-verify), do not stop to
report a half-result as if it were terminal.
