# flashmon: workspace builds tree, dev-server serving, settings panel

Moves the image catalogue out of the flashmon straddle into a workspace-level
`builds/` tree with one subdirectory per catalogue, serves both `flashmon/` and
`builds/` from `spangap dev` on a fixed host port, replaces the `make` wrapper
with a `spangap make-builds` verb, and gives the flasher page a settings panel.

## 1. Layout

```
<workspace>/
  flashmon/flashmon/          web root: index.html, flashmon.js, vendor/, detect/
  builds/
    index.html                generated — links every listed subdir
    default/
      builds.yaml             tracked — what to compile (project, url, builds[])
      index.html              generated — lists this subdir's zips
      timestamp               generated — datecode of the newest zip here
      reticulous_hw-heltecv4_20260805095349.zip
      …
    bleeding/
      .unlisted               present → kept out of builds/index.html
      builds.yaml
      …
```

`builds/` is a sibling of `flashmon/` here and on deployments, so the page
reaches a catalogue at the relative `../builds/<name>/` in both places and
nothing in the flasher needs a base URL.

Nothing in the tree is tracked — not the zips, not the generated files, not the
configs. It is a local artefact directory that `spangap make-builds` fills, and
a deployment is a copy of it.

`default` is the catalogue the page uses; `?build=<name>` selects another.
A subdir carrying `.unlisted` still builds and still serves — it is only absent
from `builds/index.html`, so it is reachable by name and not by browsing.

`builds/<name>/index.html` is the version oracle. The zips are named
`<slug>_<hw>_<datecode>.zip`, so the newest image for a board is the
highest-sorting href matching that board's name — no stamps in the config, and
what the page offers is exactly what is on disk.

## 2. Serving under `spangap dev`

```
browser → GET /                        → Vite (the SPA, as now)
browser → GET /flashmon/               → <ws>/flashmon/flashmon/index.html
browser → GET /flashmon/flashmon.js    → <ws>/flashmon/flashmon/flashmon.js
browser → GET /builds/default/timestamp   → <ws>/builds/default/timestamp
browser → GET /builds/default/index.html  → <ws>/builds/default/index.html
browser → GET /builds/default/<slug>_<hw>_<stamp>.zip
```

Vite has one static root (`publicDir`), so the two extra trees mount as
middleware. A Vite plugin exported from `spangap-web/browser` (every
web-interface already depends on it as a `file:` dep) registers them in
`configureServer`, reading `SPANGAP_WORKSPACE` — which `spangap dev` already
passes into the container. Each consumer's `quasar.config.ts` adds one entry to
`build.vitePlugins`; nothing else in the config moves.

Mounts registered in the `configureServer` body run ahead of Vite's own static
and transform middlewares, which is what keeps `/builds/...` from being resolved
as an app asset. Directory requests serve `index.html`; no SPA history fallback
on either mount, so a missing zip is a 404 and not the app shell.

## 3. `spangap dev` owns its ports

`docker run` publishes the range `127.0.0.1::9000-9009` (ephemeral host side) — a
fixed publish would wedge every other workspace's container for as long as it
idles. One in-container port per concurrent run, so a workspace can serve two
devices at once.

Each run brings up one `dev-forward` process holding every relay it needs, and
kills it on the way out:

    host 127.0.0.1:9000+  → (relay) → host 127.0.0.1:<ephemeral> → container :900N
    host 0.0.0.0:<eph>    → (relay) → device:443      ← what the container's proxy dials
    host 0.0.0.0:443,80,22,2323 → (relay) → device    ← best-effort, for `spangap cli`

The dev-server relay **scans upward** from 9000, so a second run lands on 9001
rather than failing. The device's own relay is on a port private to the run, so
two runs can drive two devices; the well-known ports are a bonus, taken when free
and skipped in silence when a monitor or an earlier run already has them.

This is what makes a monitor unnecessary — which is the point, since the monitor
holds the serial port that the flasher this dev server serves wants for itself.

`spangap dev [<addr>]` takes the device address bare as well as via `-h`.

## 4. `spangap make-builds`

Replaces `builds/Makefile` (deleted) and the direct `make-builds.py` call.

- In a `builds/<name>/`: build every entry in that subdir's config, write the
  zips there, rewrite `index.html`, write `timestamp`.
- In `builds/`: every subdir holding a config, `.unlisted` ones included, then
  rewrite `builds/index.html` from the subdirs without `.unlisted`.
- Trailing arguments select entries within the subdir, as `make-builds.py` takes
  today; a subset run leaves the other zips in place and still rewrites
  `index.html` and `timestamp`.

`timestamp` holds the datecode of the newest zip written into that subdir — the
same `SPANGAP_BUILD_DATETIME` stamp baked into the images — and is what the page
polls.

The config loses `version:`, `flash_floor_kb:` and `image_bytes:` — `index.html`
carries the version and the fit check needs neither (§5). What stays is
`project:`, `url:` and `builds[]` with `name:`, `invocation:` and optional
`image:`. With the generated fields gone the file is hand-written only, so
nothing rewrites it in place.

`flashmon.py`'s branded script and offline bundle are out of scope here. They
lose their `make` targets with the Makefile and stay hand-run until they get
their own pass.

## 5. `<project>.esptool` in the zip

`write_flasher_zip` writes an esptool argfile named `<project>.esptool` — the
`flash_project_args` the ESP-IDF build already produces, carrying the offsets,
the images and the flash mode/frequency/size flags. flashmon parses it for the
offset→image map and the flash settings, and `flasher_args.json` goes.

No fit numbers ride along, because the check never needed them. What an image
would overwrite is the probe's answer: the detector reports `DETECTED: spangap
state partition at 0x<addr> size 0x<size>` for the chip actually on the desk,
and `stateOverlaps()` compares that against each unpacked image's own address
and length. Both halves are already in hand at the point the warning is raised,
and a compile-time floor could only ever describe where the state partition
would land in a fresh layout, not where this chip keeps it.

## 6. flashmon.js

- Catalogue: `builds[]` from `../builds/<sel>/builds.yaml` for names, images and
  branding; versions from `../builds/<sel>/index.html`.
- `<sel>` is `?build=` or `default`.
- Poll `../builds/<sel>/timestamp` every 15 s (replacing the 60 s config
  re-read). On change, fetch `index.html`, and if the newest stamp for the
  attached board beats the running one, offer the flash — or start it, with
  auto-flash on.
- `buildRel()` resolves to the newest href for the board from the index.
- Flash plan from `<project>.esptool` (§5); the state-overlap warning is
  unchanged.

## 7. Settings panel

A gear at the top right of the page opens a panel holding what the monitor
bar carries today plus the new options:

- **FNB58 power meter** — moved off the monitor bar.
- **Baud** — moved off the monitor bar. Hidden when `getInfo()` reports a native
  Espressif USB device (vendor `0x303A`; the existing `USB_NAMES` table already
  names the USB-Serial-JTAG `0x1001` and CDC `0x4002` products), where the baud
  is not a real setting.
- **No reset** — checkbox; the `?noreset` query parameter goes away.
- **Auto-flash** — checkbox; a new image for the attached board starts flashing
  without a click.
- **Re-select port** — always visible, whatever the port is doing (§8).
- **Set as defaults** — stores the current panel state in `LocalSettings`, read
  back at page load.

## 8. Port outages

The rescan loop holds the configured port and its grant and waits, indefinitely
and silently. `offerRepick()` goes: no `still no port` line, and no reveal of the
button at the ~30 s tick, because the button lives in the settings panel and is
there the whole time. A board that comes back on the same object is picked up as
it is today, however long it took; a board that comes back as a fresh object is
the user's click, whenever they make it.

The backoff stays — polling a device that is off the bus buys nothing — and so
does the rule that this loop raises no dialog.

## 9. Order

1. `builds/` tree + `spangap make-builds` + `<project>.esptool` (the producers).
2. Dev-server mounts + the 9000 forwarder (the serving).
3. flashmon.js catalogue/poll rework (the consumer).
4. Settings panel, and the port-outage quieting that follows the always-visible
   re-select button.
5. `spangap` pulls `flashmon` alongside a project, through the same
   `fetch_missing_deps` / `clone_spec` path the other dependencies take.
