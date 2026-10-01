# sim-mesh: firmware as zips, drivers in the zip, run-time radios, sim-mesh.net

Agreed with rop 2026-09-30/10-01. sim-mesh holds nothing specific to any one
firmware project; every firmware arrives as a zip and is installed under
`firmware/`.

## Decisions

1. **Drivers.** Each zip carries a Python driver implementing its category's
   interface (`reticulum` first; `meshcore`, `meshtastic` later). How it
   applies things (settings file + restart, framed RPC, …) is the driver's.
   sim-mesh hands a driver a small documented host surface and nothing else.
   Cross-protocol layers (a `messages` layer) live in sim-mesh's scripting
   libraries later.
2. **Radios at run time.** Firmwares link a virtual radio's shared library,
   which sim-mesh provides when it runs the station (as it provides the time
   shim). Several radios (SX1262 now, LR2021 later); `node.yaml` names which.
   Host glue registers itself at run time (`simradio_set_services`); the
   ESP-IDF glue moves out of sim-mesh to the firmware's side. The ether's UDP
   protocol is itself a public contract, radio-neutral (`mod` on state/tx).
3. **What sim-mesh guarantees:** libc, the radio libraries, the time shim, a
   Python for the driver. A zip brings everything else (rns/lxmf included).
4. **Names:** `<base>_<arch>_<version>`, version `YYYYMMDDhhmmss` or semver;
   base opaque (radio in it by convention). `<base>_latest` = newest of that
   exact base for this machine's arch. No file-date fallback; a base never
   mixes stamps and semvers. Zip and installed directory share the name.
5. **Delete** refused while a paused run or snapshot uses the firmware;
   pause and stop get distinct symbols in the UI.
6. **node.yaml** gains `category`; no band.
7. **Pre-builts:** release assets in the sim-mesh repo (tag `firmware`),
   published by sim-mesh.net's Pages workflow under `/firmware/` with a
   generated `index.html` carrying each zip's facts as `data-*`;
   `deploy-firmware` script modelled on reticulous `builds/deploy-builds`.
8. **Repo cleanup:** per-firmware drivers, stations, compiled builds, tests
   and zip scripts move to the sister dir `../sim-mesh-firmware/<project>/`.
   Scripts declare typed inputs; a firmware input may be filtered by category
   and shows as a dropdown above the script.

## Work, in order

A. **Firmware store** (`testbed/firmware.py` replacing `devices.py`):
   `firmware/<name>/`, name parsing, `_latest`, add (zip or URL), list,
   delete with -f and the run/snapshot guard, pre-built listing from
   `$SIM_MESH_FIRMWARE_INDEX` (default `https://sim-mesh.net/firmware/`).
   CLI `sim-mesh firmware add|list|delete`. Front API + Firmware page
   (add from zip, add from pre-built). No more catalogue survey, no builds/.
B. **node.yaml / zip spec**: `name`, `arch`, `version`, `category`,
   `radio`, `driver`, `exec`, `fixed`, `env`, display facts.
C. **Driver interface** (`testbed/driver.py`): `reticulum` interface class,
   host surface, loader importing the zip's driver; simd/stations/script
   library go through it. Port the four kinds to drivers in the sister dir.
D. **Radio library**: public `simradio_set_services`, posix default backend,
   per-radio shared library name (`libsimradio-sx1262.so`), sim-mesh puts
   its directory on `LD_LIBRARY_PATH` and names it in `SIM_MESH_RADIO_LIB`;
   ESP-IDF backend moves to iface-lora (reticulous side), public C API only.
   Ether `mod` field.
E. **Script inputs**: `inputs(...)` with `Firmware(label, category=…)`;
   page dropdown; `--set name=value`; values in run.yaml.
F. **Zip scripts** in the sister dir: reticulous (from a make-builds image),
   sergeyculum, microreticulum, standard-reticulum.
G. **Site**: `sim-mesh.github.io` repo, CNAME sim-mesh.net, README as site
   like reticulous.net, spec page (zip, node.yaml, driver interface, ether
   protocol, virtual radio and how to compile against it), firmware index,
   Pages workflow, `deploy-firmware`.
H. Docs (README, INTERNALS, STATION.md; NODE.md folds into the spec), tests.
