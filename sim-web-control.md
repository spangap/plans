# The simulated testbed under web control

> Status: **built (2026-09-20).** The testbed, the map and the verbs below are
> in `reticulous/sim/`; [`sim/README.md`](../reticulous/sim/README.md) is how
> to use it and [`sim/INTERNALS.md`](../reticulous/sim/INTERNALS.md) is why it
> is shaped that way, and where the two differ from this plan, they hold.
>
> **§3 and §5 were superseded during the build.** What changed, and why:
>
> - **A scenario no longer carries state.** It is one file,
>   `sim/scenarios/<name>.yaml`, holding the design alone — geometry, air,
>   setup lines. A **snapshot** (`sim/snapshots/<name>/`) is a scenario plus
>   every station's state store. The plan's single "scenario directory" was
>   both at once, which meant there was no way to describe a network that had
>   not happened yet, and loading one twice did not give the same network
>   twice.
> - **`hostname <name>` is not injected by simd.** The setup lines take
>   `{name}`, `{id}` and `{addr}`, expanded per station, so `hostname {name}`
>   is an ordinary shared line. The file is then the whole of what a station is
>   told, and the name still cannot drift per node because there is one line,
>   not one per node.
> - **"Apply setup" is gone.** Its two jobs are split: **Factory reset** (wipe
>   state, restart, the lines run on the empty store) and **Run command**, which
>   types any CLI line at every running station and reports what each said. A
>   verb that re-sent the setup lines to a configured station was honest only
>   for the lines that happened to be idempotent.
> - **Reset means reset.** Pressing reset restarts the process and leaves state
>   alone; factory reset is the one that wipes. The plan called the first
>   "restart", which reads like the second.
> - **simd flushes the stations' store** before any stop, reset or snapshot, and
>   after setup. `s.storage.flash_delay` is a minute, so without it a station
>   reset just after setup came back unconfigured and a snapshot could miss its
>   last minute.
> - **The line-of-three is the ether's test fixture**, not a shipped scenario:
>   §4 asked for it to ship while §8 forbids committing anything under
>   `sim/scenarios/`.

## 0. The shape

One command on the host starts one process in the container, and that process
is the whole testbed: the ether, the stations, the proxy that fronts them, and
a web page that controls all of it. The page opens in the host browser as
soon as the process answers.

```
host                     container                                 browser
spangap sim ──exec──► simd.py ─┬─ ether        (UDP, in-process)
                               ├─ stations     (firmware processes, one pty each)
                               ├─ proxy        <name>.sim.localhost:9011 ─► 127.0.0.1<id>:80
                               └─ control      localhost:9011  ◄──── websocket ──── the map
```

A **scenario** is a directory: the arrangement of the network and every
station's persistent state. The page starts empty, with a menu that makes a
new scenario or loads one. From then on the main tab is a 2D map with the
stations on it, and everything a person does to the network is done there.

## 1. Files

```
reticulous/sim/
  simd.py            the process: ether, stations, proxy, control server
  stations.py        one firmware process, its pty, its log, its supervisor
  proxy.py           the hostname proxy as a module, started by simd
  scenario.py        the scenario directory: load, save, save as, reload, new
  setup.py           the setup lines, sent to a station over its TCP CLI
  seq.py             unchanged
  ui/                the Quasar app (package.json, quasar.config.ts, src/)
  scenarios/         one directory per scenario            (gitignored)
  run/               the live working copy of the loaded scenario (gitignored)
reticulous/ether/
  ether.py           positions and path loss in place of stated links
  test_ether.py      tests against positions
```

`run.py` and `topology.txt` go. `sim/README.md`, `sim/INTERNALS.md`,
`ether/README.md` and `ether/INTERNALS.md` are rewritten to describe this.

Python dependencies: the standard library, PyYAML, and **aiohttp** for the
HTTP and websocket server, added to the system-python pip line in
`spangap/build-system/Dockerfile`. The image is rebuilt once for it.

## 2. Starting it

**`spangap sim [--dev]`** in `spangap-outside`, on the flashmon pattern:
`ensure_container`, then `docker exec` in the **foreground** running
`python3 reticulous/sim/simd.py`. While that runs, the verb probes
`http://localhost:9011/` every 300 ms until it answers, then `open_url`. Ctrl-C
in that terminal stops simd, which stops everything it started. Port 9011 is
already published for the testbed and nothing else changes in the container's
port map.

`--dev` runs the Quasar dev server for `sim/ui/` beside simd inside the
container, with Vite proxying `/ws` and `/api` to simd, and opens the dev
server's port instead. Without it, simd serves `sim/ui/dist/spa/`, and the
verb runs `npm install` and `quasar build` in `sim/ui/` first when
`node_modules` or `dist` is missing or older than the sources. `dist/`,
`node_modules/` and `.quasar/` are gitignored, as for `web-interface/`.

simd itself takes `--elf`, `--fixed`, `--bind` (default `0.0.0.0:9011`) and
`--ether` (default `127.0.0.1:7000`), with the same defaults as the launcher
has now. It builds nothing.

## 3. The scenario directory

```
sim/scenarios/<name>/
  scenario.yaml
  nodes/<node name>/state/      the station's /state, as the firmware keeps it
```

```yaml
origin: [0.0, 0.0]                  # lat, lon of the map's centre
physics: { exponent: 2.7, noise_figure_db: 6, capture_db: 6 }
setup:                              # CLI lines every station is given, in order
  - auth passwd admin admin
  - set s.rnsd.transport_enabled 0
  - lora up
  - lora 0 freq 869.525
  - lora 0 sf 8
  - lora 0 bw 125
nodes:
  alpha:
    id: 1
    pos: [0.0, 0.0]
    setup:                          # this station's own lines, after the scenario's
      - set s.rnsd.transport_enabled 1
  bravo:   { id: 2, pos: [0.0, 0.0090] }
  charlie: { id: 3, pos: [0.0, 0.0180] }
obstructions:
  - { between: [alpha, charlie], db: 40 }
```

- **A node is a name and a numeric id.** The name is the hostname the
  firmware is given and the label on the map; the id is the station's
  loopback address, `127.0.0.1<id>`, and its `SPANGAP_NODE_ID`. Ids are
  allocated once when a node is created and never reused inside a scenario,
  so a node's identity survives renames. The proxy routes both
  `<name>.sim.localhost` and `<id>.sim.localhost`.
- **Positions are latitude and longitude in degrees.** The ether projects
  them to metres on an equirectangular plane around `origin` for path loss.
  At the scales a LoRa scenario spans that projection is exact enough, and
  a scenario placed on real ground needs only its origin moved.
- **The live run is a copy.** Load copies the scenario directory to
  `sim/run/` and starts the stations there; they write their state into the
  copy. Save copies `run/` back over the scenario. Reload stops the
  stations, copies the scenario over `run/` and starts them again. Save As
  copies `run/` to a new scenario directory and makes it the loaded one.
  New makes an empty scenario, saved at once so it has a directory. Logs
  and the ether's record are run outputs and live only in `run/`.
- **Load and Save stop nothing.** Stations keep running across a Save; the
  copy is taken while they run, which for a store that commits whole files
  is the same guarantee a power cut gives a board. Reload and Load are the
  only verbs that stop stations.
- **The scenario is dirty** from the first change after a load or save. A
  moved node, a new node, a removed node or a physics change sets it; the
  menu shows it and Load, New and Reload ask before discarding it.

Scenario directories are on the workspace bind mount, so they survive the
container and can be copied about. The plan's rule that run output stays off
the bind mount is for batch runs and stays there; a testbed is a person's
pace.

## 4. The ether: positions and path loss

The `Topology` of stated links goes. The ether holds, per station, a
position in metres and an antenna gain, and per pair an optional obstruction
in dB. The level a frame from `a` arrives at `b` is

```
L = P_tx + G_a + G_b − PL(d)
PL(d) = FSPL(1 m, f) + 10·n·log10(d) + obstruction(a, b)
```

exactly simulation.md §4.2: the free-space term at the carrier for the first
metre, the scenario's exponent `n` beyond it, and the pair's obstruction.
The transmitter's power is what its `state` message says its slot is set to;
the carrier is the frame's. Two stations at the same point are held at 1 m.
A frame arriving more than 30 dB below the receiver's noise floor is not
delivered at all and no `rx_begin` goes out, which is today's "no link".
The rest of §4.2, the per-station noise, the sensitivity thresholds and the
CRC band, is not part of this plan; the verdict rule stays the capture
margin against the other frames in the air, as the ether rules now.

Positions and obstructions arrive at the ether from simd by direct call: the
ether is an `Ether` object in simd's event loop, not a subprocess, and simd
sets `ether.place(sid, x_m, y_m, gain_db)` when a node is created, loaded or
dragged, and `ether.obstruct(a, b, db)` for obstructions. The UDP wire to
the stations is unchanged. `ether.py` keeps a `main()` so it still runs alone
for the tests, taking positions from a scenario file on the command line.

Events the ether raises for the page, as callbacks simd subscribes to:
`on_tx(sid, eid, freq, t_start, t_end)`, `on_rx(sid, rsid, eid, verdict,
level)`, `on_station(sid, state)`. Each becomes one websocket message.

The line-of-three hidden terminal is the shipped example scenario:
three stations a kilometre apart in a row, with the outer pair obstructed by
enough dB to be out of reach. `test_ether.py` places stations instead of
naming links and asserts the same verdicts.

## 5. simd

One asyncio loop, one process. What it holds:

- the `Ether` and its UDP endpoint;
- the `Station` set, each a firmware process on a pty with a log and a
  supervisor that restarts it on exit, lifted from `run.py` unchanged;
- the proxy server, routing by name and by id;
- the aiohttp application: static files, `/api/*` for one-shot calls,
  `/ws` for the page, `/ws/console/<name>` for a terminal;
- the loaded scenario, the run directory and the dirty flag.

**The websocket to the page.** On connect the page gets one `snapshot`: the
scenario as loaded, every node's status, the physics, the dirty flag. After
that, deltas:

```
simd → page   snapshot, node {name, id, pos, status, transport}, node_gone,
              tx {name, eid, t_start, t_end}, rx {name, from, eid, verdict, level},
              scenario {name, dirty}, error {text}
page → simd   node_add {name, pos}, node_move {name, pos}, node_remove {name},
              node_restart {name}, start_all, stop_all,
              physics {…}, setup {lines}, node_setup {name, lines}, apply_setup,
              scenario_new {name}, scenario_load {name},
              scenario_save, scenario_save_as {name}, scenario_reload
```

Status is one of `stopped`, `starting`, `setup`, `up`, `restarting`.
`up` is the station answering on its TCP CLI. `transport` is read live: simd
runs `rnstatus -j` over each station's TCP CLI every few seconds and
forwards `transport_enabled`, so a toggle made in the station's own web UI
shows on the map without the scenario knowing.

**Setup.** Scenario-wide settings are a list of CLI lines, `setup:` at the
top of the scenario, and each node may carry its own `setup:` list that
follows it. The lines are what a person would type into the station's
console, so every setting the firmware has or grows is reachable without
simd knowing its name, and since they are settings, sending them again is
harmless. simd sends `hostname <name>` ahead of both lists on its own: the
name is the node's identity in the scenario and never drifts from it.

The lists are sent at two moments. When a station comes up with no
`state/boot` in its directory, simd waits for its TCP CLI on
`127.0.0.1<id>:8081` and sends the hostname, the scenario list, then the
node's list, so a node made by a click is a working station and not a dot
waiting for someone to type. And on the menu verb **Apply setup**, simd
sends the same to every running station, which is how a change to the
hailing channel reaches a network that is already up. A station with state
is otherwise left alone; its state is the scenario's. The LXMF identity is
not in the shipped lists: who a station speaks as is the person's business,
as on a board.

**Console.** `/ws/console/<name>` bridges the station's pty master to a
websocket, bytes both ways, with a resize message for the terminal size. It
is the launcher's `--console` over a socket. The page opens it in a terminal
window; `nc` to the TCP CLI and `tail -f run/nodes/<name>/log` stay as the
other doors.

**Node lifecycle.** `node_add` allocates the next free id, writes the node
into the run scenario, makes `run/nodes/<name>/`, places it in the ether and
starts its supervisor. `node_remove` stops the process, drops it from the
ether and the scenario and deletes its run directory. `node_move` updates
the ether and the scenario. All three mark the scenario dirty.

## 6. The page

A Quasar 2 app on Vue 3 with Pinia, at `sim/ui/`, modelled on
`web-interface/`: the same `quasar.config.ts` shape, `spangap-browser` as a
`file:` dependency for `FloatingWindow.vue` and the shared Vite plugins, dark
theme, svg-material-icons. It has no service worker and no router beyond
the one page.

**Store.** One Pinia store, `sim`, owns the websocket and everything it
carries: `scenario`, `nodes` keyed by name, `frames` in flight, `physics`,
`connected`. Every component reads the store; every action is a store method
that sends one message. Reconnecting replays the snapshot.

**Layout.** A QLayout with a slim QHeader holding the menu and the scenario
name with its dirty mark, and one page with tabs. The first tab is the map;
later tabs, such as the live sequence diagram, are more components on the
same store.

**The map** is a canvas inside a Vue component, redrawn from the store on
a `requestAnimationFrame` while anything moves and on demand otherwise.
Drawn, not DOM, because a grid, hundreds of pulses a minute and a drag at
60 Hz are cheaper that way.

- **View.** Equirectangular around the scenario origin, metres per pixel as
  the zoom. Drag on the background pans; the wheel zooms about the cursor.
  The view is kept in `localStorage` per scenario so a reload comes back
  where it was.
- **Grid.** Lines every 1, 2 or 5 times a power of ten metres, whichever
  gives 60 to 150 pixels between lines at the current zoom, labelled in
  metres along the edges, with the latitude and longitude of the origin
  axes marked. No map tiles; the projection is ready for them.
- **Nodes.** A dot with its name below. A transport node has a second ring.
  Colour follows status: grey stopped, amber starting or in setup, white
  up. Dragging a dot moves it and sends `node_move` on release, with the
  ether updated live during the drag at a few Hz so a person can watch a
  link fade.
- **Frames.** A `tx` draws an expanding ring from the transmitter for the
  frame's duration; each `rx` flashes the receiver in the verdict's colour,
  green clean, red CRC failure. A hover over a node shows who it hears and
  at what level, from a `levels` message simd sends on request.
- **Click on a node** opens the info card beside it: name, id, position,
  status, transport, and buttons **Web UI** (opens
  `http://<name>.sim.localhost:9011/` in a new tab), **Console** (opens the
  terminal window), **Restart**, **Remove**.
- **Right click or long press on the background** opens a QMenu with **New
  node here**, which asks a name in a QDialog and sends `node_add` with the
  clicked position.

**Console window.** A `ConsoleWindow.vue` of the app's own: xterm with the
fit addon inside spangap-browser's `FloatingWindow`, over `/ws/console/<name>`.
spangap-web's `TerminalWindow.vue` is bound to the WebRTC session and is not
reused, but its zoom and geometry handling are the model.

**Menu.** Scenario: New, Load (a dialog listing `sim/scenarios/`), Save,
Save As, Reload. Simulation: Start all, Stop all, Apply setup, and Scenario
settings, a dialog with the exponent, noise figure and capture margin and
the scenario's setup lines as a text area. A node's own setup lines are
edited from its info card. Each verb is one store method.

## 7. Order of work

1. **Ether.** Positions, gains, obstructions, the path-loss level, the
   event callbacks, the scenario loader for standalone runs. Tests rewritten
   against positions. The stated-link code is deleted.
2. **simd.** `stations.py` from `run.py`, the proxy as a module routing by
   name and id, the aiohttp server with the websocket protocol above, the
   scenario module with all five verbs, setup on first boot and on Apply,
   the transport poll,
   the console bridge. aiohttp into the Dockerfile. `run.py` and
   `topology.txt` deleted. The host verb `spangap sim` with the probe and
   `open_url`, and `--dev`.
3. **Page.** The Quasar app: store, layout, map with grid, pan and zoom,
   nodes, info card, context menu, console window, menu with the scenario
   and simulation verbs.
4. **Frames and levels on the map.** Pulses, flashes, hover levels, drag
   with live ether updates.
5. **Docs.** `sim/README.md`, `sim/INTERNALS.md` and the ether's two
   rewritten; the `reticulous/README.md` pointers checked.

Steps 1 and 2 are verifiable without the page, over the websocket with a
few lines of Python; step 3 is verified in the browser against a loaded
line-of-three.

## 8. Rules for the executing agent

- The firmware does not change for any of this. A station is the same
  process the launcher starts today, given the same environment.
- Nothing in simd blocks: every wait is an awaitable, the TCP CLI is spoken
  over asyncio streams with a timeout, and the pty is a reader on the loop.
- The wire between station and ether is unchanged. Positions are the
  ether's business and the stations never learn theirs.
- The scenario directory format above is the format; there is no version
  field and no migration.
- Nothing under `sim/scenarios/`, `sim/run/`, `sim/ui/dist/` or
  `sim/ui/node_modules/` is committed.
