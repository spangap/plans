# SIMesh: one page for several simulations, with a finish estimate

## Goal

Several simulations run at once on one host, started by a person or by an
agent, and every one of them is watchable from the browser on the one port the
container publishes. The page lists what is running, says how far each run is
and when it will be done, and lets the viewer pick which simulation the map,
the consoles and the menus act on. Agents stop choosing ports and address
ranges by hand.

## What exists

One simd process is one simulation: one ether, one scenario, one set of
stations on one loopback /22, one control page and one station proxy on the
port it binds, plus a WebRTC relay on that port's UDP side. The container
publishes 9011 (TCP and UDP) and nothing else, so a simd on 9013 is reachable
only from inside the container. simd knows T and the observed pace but not the
run's plan: only the driver knows when the traffic hour ends.

## The shape

```
browser ── ws  localhost:9011/ws ───────────────► front ── ws ──► child simd (lora)   control, per selected simulation
browser ── ws  localhost:9011/ws ───────────────► front ── ws ──► child simd (supe)
browser ── http alpha.lora.sim.localhost:9011 ──► front ─────────► child (lora) ─► station alpha :80
browser ── ws  alpha.lora.sim.localhost:9011/webrtc ► front ─────► child ────────► station        signalling
browser ── udp localhost:9011 ───────────────────► front ── udp ─► child ── udp ─► station        the DataChannel
driver  ── ws  localhost:9011/ws  {sim: "lora", type: "plan", phases: [...]}  ► child          the plan
```

- **One front process on 9011**, `SIMesh/testbed/front.py`. It owns the page,
  keeps the registry of simulations, allocates each one a control port, an
  ether port and a loopback /22, and starts one **child simd** per simulation
  as a subprocess with today's flags. A child is today's simd, unchanged in
  what it does; it keeps its own asyncio loop and its own pace, and a crash in
  one simulation ends that one. Nothing is folded into one process: the loop
  is already the pace limit at 99 stations.
- **The page** grows a top level. A **Running simulations** tab lists every
  child: name, scenario, mode and pace, T, phase, estimated finish in wall-clock
  time, station counts by status. A tab bar under it selects one simulation,
  and the map, the consoles, the station cards and the Scenario, Snapshot and
  Simulation menus act on that one, over a websocket the front opens to that
  child. Reconnects replay the child's `snapshot` as today.
- **Station web UIs** carry the simulation's name in the hostname:
  `<station>.<sim>.sim.localhost:9011`. The front routes on the middle label
  to the child, the child routes on the first label to the station as now.
  Bare `<station>.sim.localhost` keeps working when exactly one simulation
  runs, so existing bookmarks and the README's examples hold.
- **WebRTC** is relayed twice. The front terminates the signalling websocket
  on both sides as the child does, rewrites the child's answer to point at
  9011, and demultiplexes the published UDP port by the ufrag it read from that
  answer, forwarding each flow to the child's UDP port. The child sees the
  front as the browser. Same mechanism as the child's own relay, one level up.
- **The plan and the estimate.** A driver sends `plan {phases: [{name, until}]}`
  to its simulation: phase names and the T each ends at, in microseconds.
  simd stores the plan, broadcasts it in its `clock` message, and the page
  shows "traffic 23 of 60 min, done about 22:40" from the pace over the last
  two minutes of wall. A driver updates the plan when it changes the recipe. A
  run with no plan shows T and pace only.

## What changes for drivers and agents

- `front.py` takes a `sim_new {name, scenario?, snapshot?, time: "real"|"max"|"<k>x"}`
  over its websocket and answers with the child's name, control URL, ether
  address and network, so a script that speaks to a child directly still can.
  `sim_stop {name}` ends one. `sims` lists them.
- Messages to a child through the front carry `sim: <name>`; a message
  without it goes to the page's selected simulation.
- `SIMesh/testbed/simctl.py` grows `new`, `stop`, `list` and `plan` verbs, and
  `traffic.py` sends its plan at start (warm-up, traffic, drain, with their
  end instants) and at every change.
- Agents run `front.py` if none is running and ask it for simulations; they
  no longer pick 9013 or 127.0.8.0/22. The `--bind`, `--ether` and `--net`
  flags stay on simd for a child started by hand.

## Stages

1. **Front, registry, children, plan.** `front.py` with the registry, the
   child launcher and port and network allocation, the control websocket
   multiplexer, `plan` in simd and in its `clock` message, `simctl.py` and
   `traffic.py` sending plans. Page: the Running simulations tab, the tab bar,
   the estimate. Station hostnames with the simulation label, HTTP only.
   Acceptance: two simulations from one page, switching between them, the
   estimate within a minute of the actual finish on a smoke7 run at max and at
   2x, a child crash leaving the other running and the tab saying so.
2. **WebRTC through the front.** The double relay for signalling and UDP.
   Acceptance: a station's web UI with its live panes through
   `alpha.lora.sim.localhost:9011` while a second simulation runs.
3. **Docs.** SIMesh/README.md (running it, the page, the hostnames, the
   driver API), SIMesh/INTERNALS.md (why a front and children, the relay),
   the tools' docstrings. The wire section opens with the message sequence.

## Out of scope

Anything that changes what a child does with its stations: the conductor, the
kinds, the record. A pause and single-step verb for the map is a separate,
small addition to simd once the front exists.

## Files

- new: `SIMesh/testbed/front.py`, tests beside it
- changed: `SIMesh/testbed/simd.py` (the `plan` message, `clock` carrying it),
  `simctl.py`, `traffic.py`, `ui/src/stores/sim.ts`, `ui/src/layouts/MainLayout.vue`,
  a page component for the simulations tab, `SIMesh/README.md`, `SIMesh/INTERNALS.md`
