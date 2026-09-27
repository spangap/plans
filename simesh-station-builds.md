# SIMesh: station builds from the catalogues, picked from a menu

Folded into `simesh-world-fleet-config.md`, section "SIMesh on its own",
which supersedes this file where the two differ: SIMesh has its own
launcher rather than `spangap sim`, and the packages land in SIMesh's own
`nodes/` directory.

## Goal

A person who has cloned SIMesh and run `spangap sim` starts a simulation
without building any firmware: the New simulation form offers the published
`stable` and `dev` builds, every local catalogue under `builds/` that has one,
and the workspace's own build, and the front fetches and unpacks the one
picked. Getting started loses its longest step.

## What exists

- A **catalogue** is `builds/<name>/builds.yaml`, a list of `spangap build`
  invocations. `spangap make-builds` builds each one and copies the
  `build.<target>/flasher.zip` it leaves as `<slug>_<entry>_<stamp>.zip`
  beside it, and rewrites `index.html` (links to the zips, `data-onboarding`
  per image) and `timestamp`. `deploy-builds` on the host puts each catalogue
  on a release `catalogue-<name>` in reticulous/reticulous, and the site
  serves `stable` and `dev` at `https://reticulous.net/builds/<name>/`.
  `builds/local` and `builds/rop` stay on this machine.
- A **host build** (`target: linux`, board `spangap/hw-linux`) leaves
  `build.linux/reticulous.elf` and `build.linux/data_merged/` and no zip, so
  `make-builds` cannot publish one today: it fails the entry for leaving no
  `flasher.zip`.
- The ELF is a native binary of the builder: aarch64 on this Mac's container,
  dynamically linked against the container's Ubuntu 24.04 libc, libstdc++,
  zlib and libbsd. It runs in any spangap container of the same architecture,
  which is where SIMesh runs it.
- **flashmon** lists every catalogue entry except `generic` in its manual
  board pick, and tries `hw-*` names by prefix for a detected board. A linux
  entry in `stable` or `dev` would be offered to a real board.
- A SIMesh **kind** takes `elf:` and `fixed:`; simd's `--elf`/`--fixed` give
  the default `reticulous` kind, defaulting to the workspace build.

## The shape

```
page ── sim_new {scenario, time, build: "stable"} ──► front
front ── GET reticulous.net/builds/stable/index.html ──► site          newest simesh-<arch> link
front ── GET …/reticulous_simesh-aarch64_<stamp>.zip ──► site          once per stamp
front: unpack to testbed/cache/builds/stable/<stamp>/{reticulous.elf, data_merged/, station.yaml}
front ── spawn simd --elf …/reticulous.elf --fixed …/data_merged ──► child
front → pages   sims {…, builds: [{id, label, stamp, state}], rows carry build}
```

**The image.** On a `target: linux` build, spangap writes
`build.linux/station.zip` in place of a flasher: the ELF, `data_merged/`, and
`station.yaml` (project, straddle version, catalogue, entry, stamp,
architecture from `uname -m`, the C library's version). `make-builds` takes
either zip.

**The entries.** One per architecture, named `simesh-aarch64` and
`simesh-x86_64`, each with `arch:` in `builds.yaml`. `make-builds` builds an
entry only on a machine of that architecture and says it skipped the others;
a bare run therefore refreshes the images it can build and leaves the rest.
`dev` carries the dev invocation (SUPE, netgraph) for `hw-linux`, `stable`
the stable one (`CONFIG_LORA_NO_SUPE=y`) plus netgraph, which a community
scenario needs. The listing marks them `data-target="linux"`, and flashmon
leaves marked images out of everything it offers a board.

**Where builds come from**, in the order the form lists them:

| Source | Found |
|---|---|
| `stable`, `dev` | `https://reticulous.net/builds/<name>/`; the list is `--catalogue` on the front, repeatable |
| every local catalogue | `<workspace>/builds/*/` with a `builds.yaml` and a `simesh-<arch>` zip |
| the workspace build | `reticulous/esp-idf/build.linux/`, as today |

A source whose newest image is for another architecture, or that has none, is
listed greyed with the reason. The front reads the listings at start and on a
**Check for builds** press, not on a timer.

**Fetching.** The front downloads with aiohttp in its own loop, into a
temporary name, unpacks, checks `station.yaml`'s architecture, and renames
into place, so a cache entry is whole or absent. The page shows progress on
the row being started. The cache keeps the newest two stamps per source.

**Choosing.** New simulation gets a **Build** select; the default is the
workspace build when there is one, else `stable`. `simctl.py new --build
<source>` and `sim_new {build}` do the same. The registry row and the tab show
which build a simulation runs (source and stamp), from `station.yaml`.

**Several builds in one simulation.** A scenario kind may say `build:
<source>` instead of `elf:`/`fixed:`. The front resolves every kind's build
before it starts the child and hands the child the paths, so a stable-against-
dev comparison is one scenario with two kinds.

## Getting started, after

1. Clone this repository. 2. Install spangap. 3. `spangap init`.
4. `spangap sim`. 5. New simulation: `smoke7`, build `stable`, Start.
Building the firmware moves to a "Your own build" section.

## Stages

1. **spangap and the catalogues.** `station.zip` for linux targets,
   `arch:` in `builds.yaml`, `data-target` in the listing, the
   `simesh-<arch>` entries in `dev` and `stable`, flashmon skipping marked
   images. Acceptance: `spangap make-builds` in `builds/dev` produces
   `reticulous_simesh-aarch64_<stamp>.zip` and skips x86_64 with a line
   saying so; flashmon's board pick does not list it.
2. **SIMesh.** `testbed/builds.py` (sources, listings, fetch, cache,
   `station.yaml`), the front's `builds` in `sims`, `build` on `sim_new` and
   `simctl.py new --build`, the page's Build select and the build on each
   row. Tests against a catalogue served from a temporary directory.
   Acceptance: from an empty cache, `stable` fetched and a smoke7 simulation
   up on it; a local catalogue listed; the workspace build still the default
   when present.
3. **`build:` in a kind.** Acceptance: one scenario, a stable kind and a dev
   kind, both up.
4. **Docs.** SIMesh README Getting started without the firmware build;
   INTERNALS on the cache and the architecture rule; spangap build-system
   README on `station.zip`, `arch:` and `data-target`; flashmon README.

## Open

- **x86_64 images.** This Mac builds aarch64 only. The choices are an x86_64
  spangap container under emulation on the Mac (slow but no new machine), a
  GitHub Actions job running `make-builds` for the `simesh-x86_64` entries,
  or aarch64 only until someone on x86_64 needs it.
- **Publishing.** Stage 1's images reach the site only through
  `deploy-builds`, which runs on the host with `gh`.

## Out of scope

The berlinmesh kind: Sergeyculum's binary is built with cargo outside spangap
and is not in any catalogue.
