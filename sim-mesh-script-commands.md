# sim-mesh: script commands as selections with layers

Agreed with the user, 2026-10-01. Out of scope until decided: the code/data
split, system and user scripts, `sim run` starting the front.

## The library

- Flat: `script_input(name, type=, label=, default=, category=)` (types
  `int float str bool Firmware Run`), `script_include(path)`,
  `script_results(futures)`, `script_loglevel(level)`; `sim_speed`,
  `sim_nodesets`, `sim_now`, `sim_wait`, `sim_until`, `sim_phase`,
  `sim_snapshot`, `sim_pause`, `sim_stop`, `sim_script_loglevel(level)`.
- Selections: `nodes(field=…)`, `node(name)`; one class, combinable as now.
  Methods fan out over members: `facts firmware on_first_boot up exec reset
  factory_reset move radio radio_up`, and layers `.reticulum.role`,
  `.reticulum.path(to=, dest_hash=, iface=)` (whole table without
  arguments), `.reticulum.lxmf.create/identities/announce/send`.
- Acting commands take `after=` and `wait=False`; verbs take `spread=`.
- On the class (`Node.radio(sf=9)`, `Node.reticulum.role("transport")`) a
  verb is a first-boot rule; a string is lines.
- A category layer is inert on nodes of another category; a selection with
  none of that category is an error.
- `max_tx_pwr` is gone: `radio(tx_dbm="max")`.

## Underneath

- Rules and metas carry `{verb, args, category}`; simd skips a rule of
  another category, and filters a meta's stations by it.
- Base `Driver` verbs: name, radio, radio_up, tx_power, diagnostics.
  `reticulum`: role, path(dest, iface) → [{dest, next_hop, iface, hops}],
  peer_tcp, current_role, lxmf.*. `paths` is gone.
- `scripts.log` in the run: T, wall, script, line; levels output,
  commands, debug; simd keeps the simulation's default (`script_loglevel`
  in run.yaml and the snapshot).
