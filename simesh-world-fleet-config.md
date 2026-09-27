# SIMesh on real ground: world, fleet, loss table, config, run, snapshot
## Goal

SIMesh is split along what changes independently.
- A **world** is the ground.
- A **fleet** is which devices stand where on it.
- A **loss table** follows from those two.
- A **config** is the software on the devices.
- A **run** is one simulation's output: its record and logs.
- A **snapshot** is world, fleet, config, loss table and every station's
  stored state, taken from a run.

The map shows a real world when there is one: terrain, buildings, roads,
places. The medium uses every pair's computed loss, so that each receiver
counts every concurrent transmission towards its interference, not only the
one it decodes. The page is one Quasar page that is:
- a fleet builder, with no simulation running;
- the viewer of a running simulation, with its cards, consoles and station
  web UIs.

The ground data and the propagation come from Sergey's planner. The page, the
stores and the medium stay ours.

Everything above these nouns is Python that people write: experiments,
studies, runs. It is out of scope here, apart from what this plan must leave
room for (the last section).

SIMesh also leaves spangap here: its own launcher, its own container, and
prebuilt nodes it fetches itself, so a clone runs with no firmware tree.

This is built in one go, not in stages. The checks at the end are what "done"
means. The spangap and flashmon changes come last, after everything else is
tested.

## What exists

- **The planner** (`sergey/planner`, Rust, Apache-2.0). Its `OWNERSHIP.md`
  gives `crates/` to a core owner and `ui/` to a UI owner; `ui/` is an empty
  placeholder for a Flutter app, and the working browser UI is
  `crates/planner-web/src/index.html`, so everything touched here is the core
  owner's.
  - **Packs** are directories compiled from public data by
    `planner pack build` (`crates/planner-pack/src/build.rs`):
    - `dtm.tif` (terrain), `clutter_h.tif` (clutter height),
      `data_quality.tif`;
    - `building_top.tif`, `built_fraction.tif` and `buildings.jsonl`, only
      when LoD2 data is given. A `buildings.jsonl` record is one LoD2
      building part: `id`, `e`, `n`, `ground_z`, `area_m2`, `height_m`, with
      easting and northing absolute in the pack's CRS, not relative to its
      origin. Footprint `rings` are written only with `--lod2-geometry`,
      which is off by default;
    - `population.tif`, `clutter_class.tif`, `roads.bin`, `places.bin`,
      `nodes.bin`, each present only when its input was;
    - `manifest.json`: `crs_epsg` (32600 plus the UTM zone, 32633 for
      Berlin), `region.bbox` in WGS84 lon/lat, `licenses`, and
      `calibration: []`.
  - **`planner-web --pack P --host H --port N`** (`crates/planner-web/src/
    main.rs`) serves, all at the root:
    - `/tile.bin`: "PTL1", a window of terrain, classes, population and
      clutter as typed arrays; `/basemap.bin`;
    - `/roads.bin`, `/buildings.bin`, `/buildings/values.bin`, `/nodes.json`;
    - `/search`, `/area.bin`;
    - `/link.json`: one pair's P.1812 loss, budget, Fresnel verdict and
      profile evidence. It falls back to `near_field::loss_db` whenever
      P.1812 returns an error;
    - `/loss.bin`, `/loss/start`, `/loss/status`: coverage for one site;
    - `/network/start`, `/network/status`, `/network.bin`: the census over
      the deployed nodes;
    - `/api/pack`, `/api/presets`, `/view.png`, `/coverage.png`;
    - `/`, `/planner_wasm.js`, `/planner_wasm_bg.wasm`: its own page, which
      fetches everything by absolute path.
    It has no CORS handling and no auth.
  - **`planner-wasm`** (`crates/planner-wasm`) renders a `tile.bin` in the
    browser with the server's own renderer (`planner-render`). It is a
    wasm-bindgen cdylib built with wasm-pack, as its `Cargo.toml` metadata
    configures. Its output lands in `crates/planner-web/static/`, which is
    untracked, and `planner-web` embeds it at compile time with
    `include_bytes!`, so a clean clone does not compile `planner-web` until
    the wasm has been built.
  - **P.1812-8** (`crates/planner-propag/src/p1812/`) takes a time
    percentage and a location percentage (`LinkParams`, defaults 50 and 90).
    Only the free-space term scales as 20·log10(f); diffraction, troposcatter,
    ducting, the location spread and the P.2108 terminal clutter correction
    all carry the frequency differently, and P.2108 is valid to 3 GHz. The
    CLI exposes the location percentage as `--pl` and no time percentage.
  - **Pairwise loss** runs in `backbone_graph` (`crates/planner-coverage/src/
    gaps.rs`), in parallel over pairs with rayon. It drops pairs beyond the
    sweep radius before computing anything (10 km library default, 12 km
    from `planner gaps --radius-km`), writes NaN for pairs under 250 m
    without calling near_field, and keeps only pairs within the smaller
    budget minus a margin; over-budget losses are thrown away.
    `planner_propag::near_field::loss_db` (free space plus a P.526 knife
    edge over the caller's real profile, never below free space) is what the
    coverage sweep and `link.json` use under 250 m.
  - **Optimiser output**: `planner optimize` writes `<pack>/sites.csv`,
    columns `lat, lon, tx_power_dbm, surface_masl, cs_neighbours`.
  - **Deployed network**: `planner nodes import` reads a MeshCore map JSON
    and writes a CSV with header `id, name, kind, lat, lon, height_agl_m,
    tx_power_dbm, last_seen_unix`, heights and powers blank where unknown.
    `planner nodes bake --pack --csv` and `planner gaps --nodes` read it
    back.
  - **Calibration**: `crates/planner-import` has an `Observation` schema
    (`time_unix`, tx and rx id and position, `pos_quality`, `snr_db`,
    `rssi_dbm`, `freq_mhz`, `source`) with PotatoMesh, rangetest, MeshCore
    map and sensing importers; `planner import` is a stub that exits 1, and
    nothing fills the manifest's `calibration` list yet.
- **SIMesh**:
  - a scenario is one YAML file: `origin`, `physics` (exponent, noise figure,
    capture margin), `kinds`, a shared `setup` list that goes only to the
    first kind, `obstructions` (`between: [a, b]`, `db`), and `nodes` by
    name with `id`, `kind`, `pos`, `gain_db` and its own `setup`;
  - a snapshot is a directory with that file, a `from` file naming the source
    scenario, and every station's state under `nodes/<name>/state/`. Ten are
    committed: city99 and town100, lora, supe and supe0, warm and one hour;
  - `testbed/scenario.py` loads, edits and saves both. The format is read by
    simd, front, simctl, airtime, compare, delivery, links, seq, traffic,
    the ether's own `--scenario` mode, `test_scenario.py` and
    `test_ether.py`, and written by `gen_town100.py`;
  - the ether (`ether/ether.py`, Python) computes
    `FSPL(1 m, f) + 10·n·log10(d) + obstruction(pair)` per pair, already at
    the frame's own carrier. A receiver is told of a frame only when its
    bandwidth, SF and sync word match and the carrier is within a quarter
    of the bandwidth (`MATCH_KEYS`, `same_carrier`). A frame is judged per
    receiver against each overlapping audible frame one at a time, with a
    fixed 6 dB capture margin; nothing is summed and noise plays no part in
    the capture test. simd's `levels` handler assumes 869.525 MHz;
  - the chip model (`radio/src/model.cpp`) has its own 6 dB capture lock
    (`kCaptureDb`): a frame arriving while another is being demodulated is
    taken only if it leads by that margin;
  - the page is Quasar 2 / Vue 3 / Pinia, one store (`stores/sim.ts`). The
    map (`SimMap.vue`) is a canvas in the ether's equirectangular projection;
  - the front (`testbed/front.py`) runs one simd per simulation behind one
    port, routes station web UIs by `Host` header
    (`<station>.<sim>.sim.localhost`), and forwards every control message it
    does not own to the child;
  - a station's id fixes its loopback address (`stations.bind_addr`, 1000
    ids in a /22) and the MAC the hw-linux board derives from it. Setup
    lines carry `{name}`, `{id}` and `{addr}` and run only on a station with
    no state.
- **netgraph** (`reticulous/netgraph`) draws the mesh from each node's path
  table and neighbourhood. Its records carry configuration, never
  measurements, as a stated rule in its README: a record's `if` field holds
  the interface's frequency, SF, bandwidth and configured power, and no
  RSSI or SNR. rnsd's neighbourhood table holds, per peer heard, the RSSI
  and SNR of its last announce.

## The data model

```
world  ──┐
         ├──► loss table (derived, per band, cached)
fleet  ──┘                                   │
config (includes) ───────────────────────────┼──► simd ──► ether + stations ──► run
snapshot = world + fleet + config + loss table + states ──┘
```

### World

`worlds/<name>.yaml`, one of two kinds:

```yaml
pack: ../packs/berlin-city        # a planner pack directory
```

```yaml
plane:                             # no ground data: the flat synthetic world
  origin: [52.374, 4.8897]
  exponent: 3.5                    # log-distance, as the ether does it today
```

- A pack world says nothing the pack does not already say. Its CRS, extent
  and layers are read from the pack's `manifest.json` through the sidecar.
- Packs are not copied into SIMesh: a world refers to one by path.
- A world names no device. Obstructions, which are between two named
  devices, are the fleet's.
- The medium's physics beyond path loss (noise figure, the interference
  figures) belongs to the medium, not the world, and stays in simd's
  settings.

### Fleet

`fleets/<name>.yaml`:

```yaml
world: berlin-city
devices:
  gw-alex:  { id: 1, lat: 52.5219, lon: 13.4132, height_m: 38, height_from: roof,
              antenna: { gain_dbi: 5 }, board: hw-heltecv4, tags: [gateway, transport] }
  n017:     { id: 2, lat: 52.5301, lon: 13.4018, height_m: 15, height_from: assumed,
              antenna: { gain_dbi: 2 }, board: hw-heltecv4, tags: [rooftop] }
obstructions:                      # plane worlds only; extra loss between one pair
  - { between: [gw-alex, n017], db: 40 }
```

- **Physical facts only.** Position, antenna height and where that figure
  came from (`measured`, `roof`, `raster`, `assumed`), antenna gain, board
  (which implies the chip), tags. Transmit power and radio settings are the
  config's.
- **Names are the reference.** Configs, obstructions, snapshots and the page
  refer to a device by its name, never by its id.
- **The id is stored and editable.** It is the station's network identity:
  its loopback address and its MAC follow from it. A new device takes the
  lowest free id, as `Scenario.next_id` does today. Changing a device's id
  moves the station to a new address: any `{id}` or `{addr}` its setup lines
  already ran with is stale, and anything the station wrote about its own
  address, a hostname or a TCP server bound to it, is too. The editor allows
  the change and restarts a station with state, and says so.
- **Editing.** Placing, dragging, deleting, heights, tags, obstructions and
  ids are edits to the fleet, saved or saved-as like a file. A fleet belongs
  to one world; another world is another fleet.
- **Import:**
  - the planner's `sites.csv`: position per site; the `tx_power_dbm` column
    goes to a config;
  - the deployed-network CSV that `planner nodes import` writes: the
    `height_agl_m` column where filled, else a height chosen at import,
    marked `assumed`; the `tx_power_dbm` column, where filled, to a config;
  - a fleet from a running network (see the last section).

### Loss table

Derived, cached in `losses/<world>/<fleet-hash>/<band>.bin`, never edited in
the cache.
- **Key:** the world, and the fleet's geometry: positions, heights,
  obstructions and the device set, hashed. Tags, gains, boards and ids don't
  affect it.
- **One table per band:** 433, 868 and 915 MHz. It is computed at the band's
  centre frequency, and the ether adds 20·log10(f/f0) per frame. That
  correction is a within-band one: only the free-space term scales that way,
  which is why a band has its own table and there is no scaling across
  bands. 2.4 GHz has no table: the SX1262 is the only chip here, and the
  planner's clutter correction stops at 3 GHz.
- **For a pack world:** computed by SIMesh through the sidecar's
  `link.json`, one request per pair (below).
- **For a plane world:** log-distance plus obstructions, computed by SIMesh
  itself into the same format, so the ether has one code path.
- **Every ordered pair is computed.** No budget cut: a pair too weak to
  carry a frame still adds to a receiver's interference. A radius cap exists
  only as a compute bound, default 30 km. Beyond it, and for a path that
  leaves the pack, the pair is flagged and its loss is set to "never heard".
- **A run works on its own copy.** The run directory holds the table the
  ether reads; a device moved during a run has its row recomputed into that
  copy and never into the cache. A snapshot carries the copy.

The format, little-endian:

```
"SLT1" | u32 header_len | header (UTF-8 JSON) | u32 n
       | f32 loss_db[n·n]            full matrix, row-major, [from][to]; the diagonal unused
       | u8  flags[n·n]              bit0 near-field model, bit1 no terrain / off pack,
                                     bit2 beyond radius, bit3 line of sight clear,
                                     bit4 measured (see the last section)
       | u16 samples[n·n]            measurement count, 0 when modelled
header: { world, pack_manifest_hash, band, f0_hz, model: "P.1812-8"|"log-distance",
          p_time_pct, p_loc_pct, devices: [ {name, id, lat, lon, height_m, height_from} ],
          computed_at, planner_version }
```

The matrix is full rather than triangular because measured losses are per
direction. A modelled table is symmetric. For 200 devices that is 40,000
cells, about 280 KB.

### Config

`configs/<name>.yaml`, recursive:

```yaml
include: [reticulous-base]
kinds:
  reticulous:
    build: stable                              # a node package (hw-simesh-<arch>) or a path
    settings:                                  # every device of this kind
      - set s.lora.0.freq_hz 869525000
    by_tag:
      transport: [ set s.rnsd.transport_enabled 1 ]
      gateway:   [ set s.tcp.servers.0.enable 1, … ]
    by_device:
      gw-alex:   [ … ]
  berlinmesh:
    elf: ../../sergey/reticulum/fw/simesh/target/release/simesh
    settings:
      - "set --freq-hz 869525000 --sf 8 --bw-hz 125000 --cr 5 --txpower-dbm 14"
```

- Every line list is under a kind, because the lines are that kind's
  language: our CLI for `reticulous`, `rncfg` arguments for `berlinmesh`. A
  device's kind is the config's `kinds` entry its board maps to, or the
  fleet's single kind when there is one.
- Includes are resolved depth-first and in order. A later list appends; a
  later scalar replaces. A cycle is an error, with the chain named.
- Setup lines keep their macros (`{name}`, `{id}`, `{addr}`) and still run
  only on a station with no state, as today.
- `by_tag` refers to the fleet's tags, so a config works on any fleet.
  `by_device` is the escape hatch for the odd one-off, by name, and the
  loader warns when it names a device the fleet does not have.
- Transmit power, frequency, spreading factor and the rest of the radio are
  settings like any other.

### Run

`runs/<name>/`: the record, every station's log, the ether's log, the loss
table copy the ether read, and `run.yaml` (world, fleet, config as resolved,
time mode, build stamps, when it started). A move during the run is logged
with its T. The analysis tools (airtime, compare, delivery, links, seq,
traffic) read a run, and take their geometry and radio settings from it.

### Snapshot

`snapshots/<name>/`:
- `world.yaml` (the reference, as it was);
- `fleet.yaml` and `config.yaml` (the config fully resolved, includes
  flattened);
- `losses/<band>.bin`, the run's copy;
- `nodes/<device>/state/`;
- `snapshot.yaml` (taken at which T, from which run, which station builds by
  stamp).

Loading one brings the fleet, config and table back as they were, without a
recompute and without depending on which planner is installed. A snapshot is
tied to its resolved config: a changed config is a new simulation from
scratch, because setup lines never re-run on a station with state.

### What happens to scenarios

Removed. The committed examples (smoke4, smoke7, soak30, mixed, cityE, cityF,
city99-*, town100-*) are converted once into a plane world, a fleet and a
config each by a converter script run by hand, then checked. `gen_town100.py`
writes a fleet plus the three configs that differ by SUPE lines, instead of
three scenarios. Every reader of the format moves to world, fleet, config
and run: simd, front, simctl, the six analysis tools, the ether's
`--scenario` mode (which becomes `--world --fleet --losses`), and the tests
in `test_scenario.py` and `test_ether.py`. The old loader goes; there is no
reader for the old format. The ten committed snapshots are unreadable after
this and are taken again from converted runs, an hour of simulation each for
the one-hour ones.

## The pieces

### The sidecar

- **One `planner-web` per pack in use.** The front starts it on
  `127.0.0.1:<free>` when the first simulation or builder session opens a
  world on that pack, and stops it when the last one closes.
- **Proxying.** The front passes `/planner/<world>/…` through to it with the
  prefix stripped. Only our page calls it, by that prefixed path; the
  planner's own page, which fetches by absolute path, is never shown, so its
  absolute paths do not matter.
- **Where the planner comes from.** SIMesh finds it with `SIMESH_PLANNER`
  (the planner repository), or by default beside SIMesh in the workspace.
  With no planner, pack worlds are refused with that sentence and plane
  worlds work.
- **Building it.** SIMesh's build step builds `planner-wasm` with wasm-pack
  first, because `planner-web` embeds its output, then `planner-web`, both
  from the planner tree as they are, in SIMesh's own container (below),
  which has cargo for this and for the berlinmesh kind. Packs are built by
  hand with `planner pack build` and are not SIMesh's business.

### The pair computation

No Rust is written for it. `testbed/losses.py` builds a pack world's table
by asking the sidecar's `link.json` once per pair, which already composes
everything a cell needs: the profile extraction and P.1812 call, the
near-field model under 250 m with `model` saying which was used, and the
Fresnel verdict. It computes the loss whether or not the pair is within
budget; only the margin depends on the budget.

- Requests go in parallel up to the sidecar's render slots (it answers 429
  above that), with each end's height from the fleet and the band's centre
  as the carrier. `link.json` judges at the planner's fixed time and
  location percentages; the header records them.
- Two devices under 20 m apart, which `link.json` refuses, get free space
  at their distance, flagged near-field. A path longer than the sidecar's
  window cap, or leaving the pack, is "never heard" with its flag.
- One device's row and column are the same requests for that device only,
  written into the run's copy.
- For 200 devices that is 19,900 requests; a pair is a few milliseconds of
  the sidecar's time, so a table is a minute or two, and one device's row a
  second or two. Progress is per pair, on the page.

The format is ours and is written up as a spec so rnscale can read it. A
`planner pairs` command doing the same in one process, parallel over pairs,
is a request to Sergey for when the minute matters, not something this plan
writes.

### The ether: receiver-centred reception

The loss table exists for this, so it is in this plan, in Python. The move to
native code is a later plan.

- **Received power** of each transmission at each receiver: transmit power
  (per frame, from the station's `tx` message) + gains − loss(pair) − the
  20·log10(f/f0) correction.
- **Who is affected, and who can decode, are two questions.** Every
  transmission whose carrier overlaps a receiver's bandwidth counts towards
  that receiver's interference, whatever its spreading factor or sync word.
  A receiver can decode only a frame whose bandwidth, spreading factor and
  sync word match its state, as today.
- **Per receiver, per frame being decoded, two tests hold over the whole
  frame, the worst segment deciding:**
  1. signal over thermal noise at or above the spreading factor's
     demodulation threshold (−7.5 dB at SF7, 2.5 dB lower per step);
  2. signal over each class of interference at or above that class's
     rejection figure. Interference is summed within a class before the
     test. The class of a same-SF transmission needs the signal about 6 dB
     above it, the figure the pairwise capture margin stood in for. Each
     other spreading factor is its own class with its own figure, from the
     inter-SF rejection matrix measured by Croce et al. ("Impact of LoRa
     imperfect orthogonality", 2018) and Goursaud and Gorce (2015); the
     SX1262 datasheet gives only the same-SF figure. Off-bandwidth
     transmissions contribute nothing.

  The demodulation threshold is against noise and is negative; the same-SF
  figure is against a chirp and is positive. They are not one weighted sum.
- **The ether owns the lock.** A frame is decoded only when the receiver was
  locked on to it at the preamble: it was not demodulating another frame,
  or this one led that frame by the same-SF figure at its preamble. The chip
  model's own capture lock goes; the chip takes whatever `rx_begin` the
  ether gives it, because only the ether sees the sum.
- **Carrier sense and channel-activity detection** answer from the same sum:
  busy when anything decodable, or energy above the sense threshold, is
  present.
- The noise floor stays thermal plus the noise figure.
- Every figure used is in one table in `ether.py`, with its source.

### The page

The one store is split into three, in the existing Pinia pattern:
- **`world`:** which world; for a pack, its manifest and the sidecar base URL;
- **`fleet`:** devices, obstructions, selection, dirty state, save;
- **`sim`:** today's store, the live half.

The map (`SimMap.vue`) becomes `WorldMap.vue`: one canvas with layers in the
world's CRS (pack metres, or today's projection for a plane):
- **base:** `tile.bin` windows rendered by `planner-wasm`, with terrain,
  clutter height or population as the choice. For a plane, today's grid;
- **buildings:** `buildings.bin` outlines under 9 km, filled by height under
  4 km: a mode the planner lacks and we add;
- **roads** from `roads.bin`, **places** for search;
- **fleet:** devices with height and tag marks, obstructions as dashed
  lines, drag handles when editing;
- **links:** from the loss table. For the selected device: every other device
  coloured by the power it would receive, decodable or interfering, and its
  line-of-sight flag;
- **live:** today's rings, reception flashes and radio state, when a
  simulation is shown.

`planner-wasm` is consumed as a built package: a `file:` dependency on the
wasm-pack output directory.

The pages and tabs:
- **Fleets:** open, new, save, save as, and the builder. It works with no
  simulation running.
  - Placing a device, dragging it, setting its height (a number, or "on this
    roof" from a clicked footprint's `height_m` plus `ground_z`), tags,
    antenna, board, id. The roof pick needs a pack built with
    `--lod2-geometry`; without the rings the pick is offered as the
    `building_top.tif` value at the point instead, marked `raster`.
  - The pair inspector: `link.json` between two devices, with its profile
    drawn.
  - "Compute losses", with progress.
- **Running simulations**, with a tab per simulation, as now. New simulation
  picks world, fleet and config, or a snapshot, then a time mode and a build.
- **Scripts:** see the last section.

Dragging a device while a simulation runs:
1. It edits that simulation's copy of the fleet.
2. The front recomputes the device's row and column through the sidecar,
   into the run's table copy.
3. The ether uses the device's old row until the new one arrives, and the map
   marks the device stale meanwhile.
4. The run's log records the move with its T.

### simd and the front

- **simd loads** a world, a fleet and a resolved config, or a snapshot, plus
  a loss table path, in place of a scenario. Its control messages follow:
  - `sim_load {world, fleet, config}` and `snapshot_load {name}`;
  - `fleet_*` edits, by device name;
  - `snapshot_save_as`;
  - `levels` answers from the table at the frame's carrier, not at an
    assumed one.
- **The front computes loss tables before starting a child.** It runs the
  pair computation, through the sidecar or on the plane, when the cache
  misses, copies the result into the run directory, and reports progress on
  the page. Computation is a subprocess; nothing blocks.
- **`simctl.py new`** takes `--world --fleet --config` or `--snapshot`,
  with `--time` and `--stagger` as today.

### SIMesh on its own

SIMesh is not part of spangap, and only arguably of reticulous: it simulates
whatever firmware has a station kind, and Meshtastic or MeshCore nodes are
as much its business as Reticulous. So it is not a spangap verb. It has its
own launcher, its own container, and its own supply of prebuilt nodes.

- **The launcher.** `simesh` at the repository root, one script. `simesh`
  starts the front and opens the page; `simesh nodes refresh` fetches node
  packages; `simesh build` builds the page, the chip library and the
  planner. Everything the front needs runs under it, and Ctrl-C stops all
  of it, as `spangap sim` does today.
- **The container.** A station binds a `127.x.y.z` address per process and
  the virtual-time shim is an `LD_PRELOAD` library, so stations need Linux.
  On Linux `simesh` runs natively; elsewhere it runs its own small image:
  Ubuntu 24.04 of the same architecture as the node packages (the ELF is
  dynamically linked against that libc, libstdc++, zlib and libbsd),
  python3 with aiohttp and pyyaml, node for the page build, gcc and cmake
  for the chip library, cargo for the planner and the berlinmesh kind. It is
  SIMesh's Dockerfile, not spangap's ESP-IDF image, and it publishes one
  port.
- **The port is 8800.** Round, and free in both trees: spangap holds 9010
  and 9011, the planner 8787, the front's per-simulation control and ether
  pairs count from 9100 and 7100, a station's CLI is 8081 and Reticulum's
  TCP interface 4242. The number lives in `front.py`, `proxy.py`,
  `test_front.py`, the README and INTERNALS, and nowhere else.
- **Node packages.** A node is an ELF, its `/fixed` tree and a `node.yaml`
  naming its kind type, architecture and build stamp, zipped in the
  catalogue filename format with `hw-simesh-aarch64` or `hw-simesh-x86_64`
  in the board position: `reticulous_hw-simesh-aarch64_<stamp>.zip`. SIMesh
  expands them into its own `nodes/<slug>_<entry>_<stamp>/` directory, which
  is what a config's `build:` names: `stable`, `dev`, a stamp, or a path to
  a workspace's `build.linux` for the developer loop, which stays spangap's.
  `simesh nodes refresh` fetches the newest of each entry from the SIMesh
  repository's release artefacts, or from any catalogue URL or local
  `builds/<name>` directory given to it.
- **Producing the packages** is spangap's side: `make-builds` learns the
  Linux case, zipping the ELF, `/fixed` and `node.yaml` for a
  `target: linux` entry with an `arch:` key so a builder of the other
  architecture skips it and says so; it writes a derived `data-target`
  (`esp32s3`, `esp32p4`, `linux`) on every catalogue link, and flashmon
  leaves every non-chip image out of both its detected-board path and its
  manual pick, and refuses a chip that does not match. `hw-simesh-<arch>`
  is then one entry in `dev` and `stable`.
- **Protocol-specific parts are behind their protocol.** The LXMF traffic
  driver and delivery analysis, SUPE's frame classes in airtime and links,
  and the transport ring with its poll are Reticulum's. They move under
  `simesh.reticulum` (the library the scripts section names), the ring
  becomes a per-kind `role` that a kind reports (transport, router,
  repeater, client), and the page shows Reticulum's verbs under a
  `reticulum` heading that a fleet without Reticulous stations does not
  show. The ether, the record, the map, per-carrier airtime and link
  geometry stay generic.
- **The order of work.** Nothing in spangap or flashmon changes until
  everything above it is built and tested against a workspace build named
  by path: the `spangap-outside` script, `make-builds`, the catalogues and
  flashmon are edited last, in one pass, because a changed spangap script
  under several open windows disturbs whatever they are doing. Until that
  pass, `spangap sim` keeps working as it is and the port stays where it
  is.

## What done means

- **Conversion.** smoke7 and town100, converted, run in `max` mode for a
  fixed seed with the same frames on the air as the scenario did under the
  pairwise rule, when the ether is switched to the pairwise rule for this
  check. The run is deterministic, so any difference is traced to an f32
  rounding of a borderline level and to nothing else. The three town100
  variants are one fleet and three configs of a few lines each.
- **Reception.** A hand-built three-device case (two hidden senders, one
  receiver, once on the same SF and once on different SFs) matches a hand
  calculation with the figures table. town100 before and after, with the
  differences in delivery and collisions explained per cause.
- **Ground.** A smoke7-sized fleet placed in Mitte runs with rings on real
  streets; cards, consoles and station web UIs work as today.
- **Pairs.** The 169 live Berlin repeaters' table in a few minutes with
  progress shown; one device's row in a few seconds; a cell equals what the
  pair inspector shows for the same two devices, near-field pairs included.
- **Builder.** The deployed-network CSV imported, heights assumed at 15 m
  and shown as assumed, an obstruction added, an id changed with the station
  restarted, saved, reloaded, run. A snapshot taken from that run reloads
  without the planner present.
- **Mixed kinds.** The converted `mixed` fleet runs with its berlinmesh
  station configured by its own kind's lines.
- **On its own.** A fresh clone of SIMesh on a machine without spangap:
  `simesh nodes refresh`, `simesh`, the page on port 8800, smoke7 started
  with build `stable`, its console and web UI opened. The same clone with a
  workspace beside it runs a config whose `build:` is that workspace's
  `build.linux`. A catalogue with a `hw-simesh-aarch64` entry is served by
  flashmon with that entry absent from its board pick.
- **Docs.** SIMesh README: getting started from a clone with no spangap,
  then a world, fleets, configs, the builder; the developer loop with a
  workspace. INTERNALS: why its own launcher and container; why a sidecar;
  why a table of every pair; the reception model, its two tests and its
  figures; why the ether owns the lock. The loss table's spec. The node
  package's `node.yaml` spec.

## Later, not in this build: losses from a running network

Nothing in this section is built now. It is here so the loss table's format
and the fleet's import leave room for it, and it waits on netgraph.

The loss table can be fed by a real network instead of by the model, and the
difference between the two is what calibrates the model.

- **Where the numbers come from.** netgraph will expose each node's radio
  measurements of its peers, RSSI and SNR per peer heard from rnsd's
  neighbourhood table, but does not today: its rule is that a record
  carries configuration and never a measurement, and changing that is
  netgraph's own plan, on its own time. Until it does, this section has no
  source. SIMesh will read what netgraph exposes, as the planner's
  `Observation` schema, and nothing below it.
- **From reception to loss:** the sender's transmit power (from its record's
  `if` field, or asked by the crawl) plus gains, minus the received power.
  - Below the noise floor, RSSI reads the noise, so the received power there
    is derived from SNR plus the noise floor.
  - Each measurement is one direction at one moment. The table takes the
    median over time per direction; the matrix is per direction already.
- **What it can fill:**
  - pairs that hear each other get a measured loss, `measured` set and
    `samples` counted;
  - pairs that don't get a lower bound, from the sensitivity: "at least this
    much";
  - every other pair keeps the model's value.
- **What it's for:**
  1. Simulating the real network as it is, with the measured table in place
     of the modelled one.
  2. **Calibration.** Model minus measurement, per pair, over many pairs,
     says where the model is wrong: a height assumption, a clutter figure, a
     site's surroundings. Fitting per-site height and a per-site clutter
     correction to those residuals, and recomputing coverage with the fitted
     values, turns measurements into a better coverage map. The planner's
     manifest has an empty `calibration` list, `planner-core` has the
     profile types and `planner-import` the observation schema; the
     calibration itself is the planner's to own, and SIMesh is one source of
     observations. That is a conversation with Sergey, not a patch.
  3. **A fleet from the network:** devices from netgraph's view, with the
     positions nodes publish where they do, and heights from the calibration.

  What this stage needs from netgraph's exposure, per measurement:
  - the receiving node and the peer;
  - RSSI and SNR;
  - when it was taken;
  - the carrier, spreading factor and bandwidth it was taken on;
  - the sender's transmit power at the time, or enough to find it: SUPE
    varies power per frame, so a configured power alone is not enough.

## Out of scope, with room left for it

- **Experiments, studies, runs.** These are Python scripts in `scripts/`,
  started from a Scripts tab and run by the front as subprocesses, with their
  output streamed to the tab and their files written wherever they choose.
  They use three libraries:
  - `simesh`: start a simulation from world, fleet and config or a snapshot;
    pace; wait until up; clock; snapshot; move or switch off devices;
  - one per mesh protocol, starting with `simesh.reticulum`: send a message,
    announce, paths, which translate an intent into the commands of each
    station kind;
  - `simesh.analysis`: the run, the record, the logs, delivery, airtime,
    geometry.

  Those libraries and the Scripts tab are the next plan. This plan only has
  to keep simd's control messages and the data model clean enough to wrap.
- The ether in native code, and neighbourhood synchronisation instead of the
  global barrier.
- Chips other than the SX1262, and technologies other than LoRa.
- Fading and time-varying links.
- Meshtastic and MeshCore station kinds. The launcher, the node packages
  and the per-protocol libraries leave room for them: a kind type, a
  package per firmware, a library per protocol.
