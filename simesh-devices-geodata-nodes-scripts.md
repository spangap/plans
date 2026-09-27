# SIMesh: Devices, Geodata, Nodes, Scripts, Simulations

## Goal

The page is organised around what a person handles, one tab each:

- **Devices**: the station builds that can run, as files that are imported.
- **Geodata**: the ground, previewed on its own and later edited.
- **Nodes**: which devices stand where, with what radio and role, edited on
  the map, with the network's coverage drawn by default.
- **Scripts**: Python against SIMesh's own library. Configs are scripts too.
- **Simulations**: what is running, and **Run from current world** to put the
  current nodes on the current geodata in real time and interact by hand.

A status line at the bottom says how many simulations are running.

World, fleet and config become geodata, nodeset and script everywhere: files,
directories, front messages, simd, simctl, the page and the READMEs. Nothing
reads the old names afterwards and the repository's own files are converted
once as part of the change.

## The files

```
SIMesh/devices/<catalogue>/<package>/     expanded device files (today's nodes/)
SIMesh/devices/imported/<package>/        device files uploaded on the page
testbed/geodata/<name>.yaml               ground: a planner pack or a synthetic plane
testbed/nodesets/<name>.yaml              nodes, their declarative settings, the loss offsets
testbed/scripts/<name>.py                 setup and drivers
```

### The device file

One format for every firmware: the node package of NODE.md, a zip holding
`node.yaml`, the executable and whatever else the station needs. NODE.md
gains the keys that today sit in a config because no package carried them:

| Key | Value |
|---|---|
| `name` | what the page calls it, e.g. `Reticulous dev 2026-09-25 03:50`; absent, built from `project`, `catalogue` and `stamp` |
| `stands_for` | the hardware it plays, e.g. `ESP32`; the page shows it as "virtual ESP32" |
| `tools` | a mapping of tool name to its path in the archive (`rncfg` for berlinmesh) |
| `env` | extra environment; a value starting `./` is a path in the archive |

A berlinmesh build is packaged the same way with its `rncfg` inside, so the
`elf:`, `fixed:`, `tools:` and `env:` keys leave configs altogether and a
kind is only the code in `testbed/kinds/` that `node.yaml`'s `kind` names.

**Import** on the Devices tab uploads a zip. The front validates it per
NODE.md and expands it under `devices/imported/`. An invalid zip is refused
with the rule it broke. The catalogues' packages keep arriving by
`simesh devices refresh`.

A node names its device as either a catalogue (`dev`, `stable`: the newest
there, at the time a simulation starts) or a package name (pinned). A
simulation records the package each node resolved to, and so does a snapshot.

### Geodata

Two kinds, as now, but a plane no longer sits on the real Earth:

```yaml
# testbed/geodata/plain-27.yaml
synthetic:
  terrain: flat
  exponent: 2.7
  extent_m: 20000                  # the square the preview and the nodeset filter use
```

```yaml
# testbed/geodata/berlin-city.yaml
pack: ../../../sergey/planner/.cache/packs/berlin-city
```

**Synthetic geodata lives at 0°, 0°.** Its positions are still latitude and
longitude, projected by the fixed rule of one nautical mile per minute of arc,
on both axes: `x = lon · 60 · 1852 m`, `y = lat · 60 · 1852 m`. The page can
therefore show metres or degrees, and a nodeset made on one synthetic ground
stands on any other. `terrain: flat` keeps today's log-distance loss. Other
synthetic terrains come with the geodata editor. A node's height is always
above the ground under it, so the same nodeset on another terrain rises and
falls with it.

**Import** on the Geodata tab uploads a zip of a planner pack. The front
checks its `manifest.json`, expands it under the planner's pack cache and
writes the geodata file that names it.

### Nodesets

```yaml
# testbed/nodesets/mitte7.yaml
nodes:
  internet:
    id: 1
    lat: 52.5219
    lon: 13.4132
    height_m: 38                   # above ground
    height_from: assumed
    antenna: { gain_dbi: 2 }
    device: stable
    role: transport                # transport | client
    radio: { freq_mhz: 869.525, sf: 8, bw_khz: 125, cr: 5, tx_dbm: 14 }
    tags: [lora, tcp-peer]
  gw02: { ... }
offsets:                           # dB added to the computed loss of one pair, both ways
  - { between: [internet, gw02], db: 40, note: "wall, measured 2026-09-20" }
```

A nodeset names no geodata. The Nodes tab lists the nodesets with at least
one node inside the current geodata's extent. A pack's extent is its
manifest's, and a synthetic one is its `extent_m` around 0°, 0°.

**The declarative settings** (`device`, `role`, `radio`) are what the page
must know without running anything: the double ring for a transport node, the
coverage, and the carriers whose loss tables a simulation computes. They are
applied at setup by the library's meta layer (below), per kind, before any
script's own setup.

**Offsets** replace obstructions. They are a layer over the computed pair
losses on any geodata, pack or synthetic, and go with the nodeset. They are
where real-world calibration goes. The model's work is to make them small,
and the page can show each offset beside the loss it corrects.

## The library

`simesh`, importable by every script, which the front runs as a subprocess
with the library on its path. It wraps today's `simesh/control.py`.

```python
import simesh

async def setup(node):             # per node, on an empty store, after the declarative settings
    if "tcp-peer" in node.tags:
        await node.run("tcp peer add {addr:internet}:4965")

async def main(sim):               # the driver; absent, the script is setup only
    sim.plan(("warm", 900), ("traffic", 4500))
    await sim.all_up()
    await sim.nodes(tag="lora").announce(spread=300)
    await sim.until(900)
    for a, b in sim.pairs(sample=200, seed=17):
        await a.message(b, "hello", after=sim.rand(0, 3600))
    await sim.until(4500)
    await sim.snapshot("mitte7-1h")
```

**Selections.** `sim.nodes(tag=, device=, kind=, role=, names=)` returns a
selection, and one node is a selection of one. What is done to a selection is
done to each member, spread over `spread` seconds of run time and held back by
`after`, as the `command` message does today.

**Literal commands.** `sel.run(line)` types a line in each node's own
language, with the macros expanded. A selection spanning kinds refuses it
unless `kind=` narrows it, because the same text means different things on
different firmwares.

**Meta-commands** say what is meant, and each kind translates:
`set_role`, `set_radio`, `set_name`, `announce`, `message(to, text)`,
`path_to(node)`, `peer_tcp(node)`, `reset`, `factory_reset`, `start`, `stop`.
A kind that cannot do one raises, and the error names the kind and the verb.
A MeshCore kind will add its translations. It does not change the verbs.

**Simulation control.** `simesh.start(geodata, nodeset, script=None,
time="real" | "max" | "10x")` or `simesh.attach(name)`; `sim.until(t)`,
`sim.run_s`, `sim.plan(...)`, `sim.snapshot(name)`, `sim.move(node, lat, lon)`,
`sim.stop()`.

This is a first attempt. The verb list, and how a reply that differs by kind
comes back, get their own sparring session before the tab is built.

## The page

### Devices

A list of device files: name, "virtual ESP32" (from `stands_for`), kind,
architecture, stamp, catalogue or imported, and what uses it. Includes
**Import…** and **Refresh catalogues**.

### Geodata

A list of geodata with **Import…**. Choosing one fills the tab with its map,
with no nodes, scrollable and zoomable. **Back** returns to the list. The
editor for making and adapting geodata comes later, on this same view.

### Nodes

The map of the current geodata with the current nodeset on it. **Nodeset ▸
Open…** lists only the nodesets inside this geodata, plus **Save**, **Save
as…**, **New** and **Import CSV…**, as the builder has now.

- **Drawing.** Each node is a dot with its name. A transport node gets a
  double ring. Tags are shown under the name in a smaller font.
- **Selection.** Click selects one. Shift-click adds. Cmd/Ctrl-click toggles.
  Cmd/Ctrl-drag draws a rectangle that replaces the selection, or adds to it
  with Shift. A plain drag pans.
- **Tags panel.** Every tag in the nodeset with its count, and beside each tag
  **+** and **−** to add or remove every node carrying it to or from the
  selection.
- **Editor.** Opens on the selection. Fields that differ across the selection
  show `<multiple values>` and are left alone unless typed into. Tags show as
  chips that are either on all selected nodes or on some; clicking a chip
  adds it to all or removes it from all. **Add tag** puts a new tag on every
  selected node, and no other tag changes.
- **Context menu.** On one node: **Edit**, **Coverage of this node**,
  **Console**, **Web UI**, **Delete**. On several: **Edit**, **Coverage of
  these**, **Delete**. Console and Web UI are live while a simulation runs
  this nodeset, and the kind decides whether Web UI exists.
- **Coverage.** Drawn as a layer by default: the whole network, as the best
  level at each point from any node. The coverage entries in the menu switch
  that layer to the chosen nodes until it is cleared. Each node's raster is
  cached, keyed on the geodata and the node's position, height, antenna and
  radio. Nothing is computed until the tab is open and the layer is on. Cached
  rasters draw at once and missing ones fill in as they arrive. A pack uses
  the planner's point-to-area sweep, `planner-coverage`. A flat synthetic
  ground uses the log-distance formula.
- **Display menu** (hamburger). Ground choice, roads, buildings, coverage,
  links, offsets, and node labels, tags and heights. The Geodata tab has the
  same menu without the node entries. Choices are remembered per tab.

While a simulation is running, the Nodes tab can be attached to it from the
Simulations tab. It then shows that run's live map (status colours,
transmissions, live roles) and edits that run's copy, as a simulation's map
does now. **Save as nodeset** keeps the edits.

### Scripts

A list of `scripts/*.py`, each with its file shown and any `setup`/`main` it
defines. **Run** asks for geodata, nodeset and time mode when the script
starts its own simulation, or which running simulation to attach to. Output
streams into the tab. The files are edited in an editor, as configs are today.

### Simulations

The registry as today, plus **Run from current world**: the Nodes tab's
geodata and nodeset, `real` time, and optionally a script for setup. With no
script, a node gets only its declarative settings. It starts attached to the
Nodes tab.

### Status line

`N simulations running`, each one's name and phase on hover. It is on every
tab and will carry more later.

## The front's messages

```
page ── POST /api/devices/import  (zip) ──► front: validate, expand ──► {ok, name} | {ok: false, error}
page ── POST /api/geodata/import  (zip) ──► front: check manifest, expand, write geodata file
page ── ws nodeset_open {name}           ──► front ──► nodeset {name, nodes, offsets}
page ── ws coverage {geodata, nodes: [...]} ► front ── per node, cached or ──► planner sidecar
front ──► page  coverage_tile {node, key, raster}    as each arrives
page ── ws script_run {script, sim? | geodata, nodeset, time} ──► front: subprocess
front ──► page  script_output {run, line} … script_exit {run, code}
page ── ws sim_new {geodata, nodeset, script?, time, …}   (today's sim_new, renamed keys)
```

`world_*`, `fleet_*` and `config_*` become `geodata_*`, `nodeset_*` and
`script_*`. `sim_new` takes `geodata`, `nodeset` and an optional `script`.

## What goes

- Configs as YAML, and their `settings`, `by_tag` and `by_device`. The
  repository's configs become `setup()` functions in scripts, and their radio
  and role lines become each node's declarative settings.
- `obstructions`, which become `offsets`.
- Planes at a real origin. Amsterdam's two become synthetic geodata at 0°, 0°,
  and the nodesets on them are moved there once.
- `traffic.py` as a standalone driver. It becomes a script whose `main` is
  today's phases.

## Done means

- Devices lists stable and dev by full name as virtual ESP32s, and imports a
  berlinmesh zip that then runs.
- Geodata imports a pack zip and previews it. A synthetic geodata previews in
  metres.
- A nodeset made at 0°, 0° opens on two synthetic geodata. Berlin's nodeset
  is offered only on Berlin.
- Multi-select by rectangle and by tag, multi-edit with `<multiple values>`,
  and tag add and remove across the selection all work, with the other tags
  untouched.
- Coverage draws for the network and for a selection, from cache on reopening.
- Run from current world starts in real time with no script. Console and
  Web UI open from the node menu.
- The town100 recipe re-run as a script reproduces today's delivery figures
  within run-to-run noise.
- Host tests pass: `test_nodes`, `test_fleet` (renamed), `test_config`
  (replaced by the library's tests), `test_front`.
