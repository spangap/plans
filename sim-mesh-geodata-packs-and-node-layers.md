# sim-mesh: building geodata packs, and nodesets as layers

sim-mesh makes its own ground from public sources, moves it between machines
as a **sim-mesh geodata pack**, and treats nodesets as layers that can be
shown, hidden, imported from the public node maps and merged. After this,
nothing in sim-mesh needs Sergey's planner repository.

Status: agreed 2026-09-27, ready to build in the order at the end.

## The Geodata tab

```
Geodata                        [New synthetic…] [Build from sources…] [Import zip…]
  berlin-city   pack, EPSG:32633 · 10 layers    52.415…52.605 N, 13.193…13.606 E
  flat-20km     synthetic, flat, exponent 3.5   20 km square at 0°, 0°
  brandenburg   building… downloading WorldCover 3/4 (112 of 150 MB)       [Cancel]
```

Three buttons, one per way of getting ground. Clicking a row opens it on
the map as now, and that view's toolbar has **Export zip**. Nothing else
creates or moves geodata.

### A sim-mesh geodata pack

A zip holding `geodata.yaml` at the top and, for ground built from sources,
the planner pack under `pack/`, with the yaml's `pack:` pointing there. A
synthetic geodata exports as the yaml alone. **Import zip…** takes this and
also a bare planner pack (a `manifest.json` at the top or inside one
directory), which becomes pack geodata. The name is asked, defaulting to the
one in the zip.

**Nodes are never part of a sim-mesh geodata pack.** The compiler is never
given a nodes CSV, export leaves out any `Nodes` layer and its manifest
entry, and import drops one found in a bare planner pack (as
`packs/berlin-city/nodes.bin` has now). Nodes belong to nodesets, which
stand on any ground that holds them.

A pack holds only redistributable data. The ITU-R (International
Telecommunication Union, radiocommunication sector) P.1812 maps stay in the
cache; the manifest carries the two numbers taken from them (ΔN, N0), which
is what the compiler already does. Each source's licence notice is in the
manifest, and the map shows the notices of the geodata it draws in its
bottom corner; OpenStreetMap's Open Database Licence (ODbL) and Copernicus's
notice require it.

### Build from sources…

A view of its own, like the preview, with **‹ Back**:

```
page ── GET /osm/<z>/<x>/<y>.png ──────────────► front ── (cache miss) ──► tile.openstreetmap.org
page ── POST /api/geodata/build {name, bbox, res_m, terrain, buildings, population} ──► front
front ── fetch into packs/.cache/<source>/ ─────► source hosts (below)
front ── spawn planner-job pack-build, JSON on stdin ─► planner-job
planner-job ── one JSON progress line per step ──► front ── geodata_progress ──► page
planner-job ── packs/<name>/ ──► front writes geodata/<name>.yaml ── catalog ──► page
```

**The map** is the OpenStreetMap standard tiles in Web Mercator, drawn on a
canvas as `GroundMap` draws, fetched through the front and cached under
`testbed/osmtiles/` (the tile usage policy asks for caching and a single
origin keeps it one). Existing packs are drawn as outlined rectangles with
their names, and the areas of the sources that do not cover the world
(Berlin's official data, Germany's census grid) as tinted outlines, so it is
visible where they apply. Drag pans, the wheel zooms, Ctrl/Cmd-drag draws the
rectangle, as on the Nodes map. `PlaceSearch` on this map asks Nominatim
(OpenStreetMap's geocoder), one request per search, as its policy allows.

**The side panel** under the rectangle:

- name and resolution (30 m or 10 m), with the grid's size in cells and its
  Universal Transverse Mercator (UTM) zone, the zone of the rectangle's
  centre. A rectangle wider than its zone, or one whose grid would pass a set
  number of cells, is refused with the sentence saying why;
- **terrain**: Copernicus GLO-30 everywhere, or Berlin's DGM1 and bDOM
  (1 m terrain and surface) where they cover the rectangle, with GLO-30
  outside it;
- **buildings**: none (clutter estimated from GLO-30 and land cover),
  OpenStreetMap, or Berlin's LoD2 models. The Berlin choices are offered
  only when the rectangle touches Berlin;
- **population**: the Zensus 2022 grid where the rectangle touches Germany,
  else none;
- always, and not a choice: ESA WorldCover land cover, OpenStreetMap roads
  and places, and the ITU maps;
- for each source, its download size still to fetch (what is in the cache
  costs nothing) and its licence;
- **Build**. The view goes back to the list, where the new row shows the
  step and progress and has **Cancel**; when it ends the row is geodata like
  any other, or shows why it failed.

The build is a child process; the front never waits on it. One build runs at
a time per front, and a second is refused while one runs.

### Sources and the cache

`packs/.cache/<source>/` holds every download, shared by all builds, so a
second region next to the first fetches only what is new. Downloads are
resumable and each file is fetched once.

| source | covers | from |
|---|---|---|
| GLO-30 surface model, 1° tiles | world | `copernicus-dem-30m.s3.amazonaws.com` |
| WorldCover 2021, 3° tiles | world | `esa-worldcover.s3.eu-central-1.amazonaws.com` |
| OpenStreetMap extract (roads, places, sites, buildings) | world | Geofabrik, the smallest extract holding the rectangle |
| P.1812-8 ΔN/N0 maps | world | `itu.int`, kept local, never packed |
| DGM1, bDOM, LoD2 | Berlin | `gdi.berlin.de` ATOM feeds, only the tiles the rectangle needs |
| Zensus 2022 100 m grid | Germany | `destatis.de` |

**OpenStreetMap comes from one Geofabrik extract, not Overpass.** One
protocol buffer file (PBF) per region serves roads, places, peaks and masts,
and buildings alike; Geofabrik's index gives every extract's outline, so the
smallest one holding the rectangle is chosen (Berlin 80 MB, Brandenburg
about 150 MB). Overpass would take four queries per region, rate-limits,
and times out on a city's buildings.

**The Berlin tiles are chosen, not mirrored.** The ATOM feeds list every
tile with its extent; only those meeting the rectangle are fetched, so a
district costs megabytes rather than the city's 12 GB.

### The compiler

`planner-pack`'s `build` is the compiler sim-mesh already carries. What is new:

- **`planner-job`**, a binary crate in `sim-mesh/planner`: `pack-build` takes
  `BuildParams` as JSON on standard input and writes one JSON line per step to
  standard output (`{"step":"clutter","done":3,"total":12}`), then the
  manifest's path. It holds no fetching; the front hands it files.
- **OpenStreetMap from the extract**: roads, places and sites read from the
  PBF (a pure-Rust reader) into the layers `roads.rs` and `places.rs` write
  now.
- **Buildings from OpenStreetMap**: `way[building]` and building
  multipolygons into the same `buildings.jsonl`, clutter-height merge,
  `building_top` and `built_fraction` that LoD2 fills. Height is the
  `height` tag; else `building:levels` × 3 m plus a roof; else the class
  default. `planner-buildings`, which holds those rules and the per-building
  height source, comes into `sim-mesh/planner` for it. Each building keeps
  where its height came from, and the pack's `DataQuality` layer says which
  cells rest on tagged heights and which on defaults.

## Nodesets as layers

The Nodes tab is always the map. The nodeset list becomes the **Layers**
panel on the left, above Tags:

```
Layers                               [New] [Import…] [Save visible as…]
 ● 👁 town-core          42   •           (active: edited, selected, saved)
 ○ 👁 meshcore-2026-09   318              (shown, drawn hollow in its colour)
 ○ ·  potatomesh         77               (hidden)
```

Every nodeset holding a node on the geodata is a row. The eye shows or hides
a layer. **One layer is active**: clicking a node, the selection, Tags, the
editor, the links and coverage layers and **Save** are the active layer's.
Every other shown layer is drawn in its own colour, hollow, and names its
nodes on hover; clicking one of its nodes makes that layer active. Making
another layer active with unsaved edits asks first, as leaving a nodeset
does now.

- **New** asks a name and adds an empty layer, active.
- **Import…** makes a new layer from a source and makes it active (below).
- **Save visible as…** asks a name and writes one new nodeset from every
  shown layer as it stands, unsaved edits included, top of the panel first.
  With one layer shown it is that layer's Save as; with several it is the
  merge, and there is no second way to do either. Nodes keep their tags and gain their layer's
  name as a tag, so a script can still tell them apart; a name taken by an
  earlier layer gets the layer name appended; an id taken gets the next free
  one; two nodes within 5 m of each other are one node, the earlier layer's.
  Offsets come along where both ends do. The new layer is active and the
  layers it came from are hidden.

The live map of a running simulation is unchanged: it shows the run's own
nodeset and no layers.

### Import…

One dialog with the source first; each makes one new nodeset of the nodes
inside the geodata's extent.

- **MeshCore map**: the node list at `map.meshcore.io/api/v1/nodes`. The
  front fetches it at most once a week, into the cache, under an honest
  user agent; the dialog says the copy's date. Repeaters and room servers by
  default, companions when ticked. An advert older than a set age, or dated in
  the future, is left out, since both ends of that range are clocks never set.
- **PotatoMesh**: an instance's `/api/nodes`, by its address; Meshtastic
  and MeshCore nodes both. A position whose precision was cut on purpose is
  left out.
- **Planner sites** (`sites.csv`) and **deployed-network CSV**, as the
  Import CSV dialog does now.

The public Meshtastic maps are not a source: their positions are truncated on
purpose, and PotatoMesh already carries the Meshtastic nodes that are there.

Every imported node gets the default device, the height the dialog asks
where the source has none (marked assumed), and tags for its source, its
kind (repeater, room server, companion, router) and its position quality.
The parsing is `planner-import`'s, brought into `sim-mesh/planner` and run as
`planner-job nodes-import` in the same way as `pack-build`.

Measurements (Meshtastic range-test CSVs, PotatoMesh neighbour and trace
reports, sensing-node logs) are not nodes. They are where a nodeset's
offsets are to come from, and are not in this plan.

## What goes

- The Nodes tab's nodeset list and its **Start empty** line: the Layers panel
  replaces both.
- **Import CSV…** on the Nodes tab: it is one of Import…'s sources.
- **Import…** on the Geodata tab, which becomes **Import zip…**.
- The README's "packs are still compiled by Sergey's planner" and
  INTERNALS's "Building packs from their sources is not among them yet".

## Order

1. sim-mesh geodata pack: Export zip, Import zip, Nodes layers dropped,
   attribution on the map.
2. `planner-job` with `pack-build` over what the compiler reads today, the
   cache, the fetchers for GLO-30, WorldCover, the ITU maps, Geofabrik, and
   the Build from sources view.
3. Buildings from OpenStreetMap, and the Berlin and Zensus fetchers.
4. Layers on the Nodes tab, Save visible as.
5. `planner-job nodes-import` and the Import… sources.

Each step ends with host tests: the pack round trip, a small real build
(a few square kilometres of Berlin from the cache), the merge rules, and each
importer against a saved copy of its source.
