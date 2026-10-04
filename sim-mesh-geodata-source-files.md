# sim-mesh: geodata sources as files

Status: agreed 2026-10-04. Built: steps 1 and 3 of the Order, step 2 for
GeoTIFF terrain and surface, CityJSON and population grids, step 4 for
regional sources (AHN), and the `index` finding method that the Dutch
sources needed.

A **source** is where a pack's ground comes from: GLO-30, WorldCover,
OpenStreetMap, Berlin's 1 m data and so on. Sources are data: entries in one
YAML file that only pick and fill in methods sim-mesh has, and never carry
code. sim-mesh's own file is in its repository and grows by issues and pull
requests; a person adds more beside it, and an index can list them as it
lists geodata and nodesets.

```
sim-mesh/sources/sources.yaml ─┐   global, then continent ▸ country ▸ sources
testbed/sources.yaml ──────────┼─► the sources known here, by id
indexes' sources ──────────────┘
      │
page ── GET /api/geodata/sources?bbox=&res_m= ─► front: the plan
      │    for each layer, every source whose coverage meets the rectangle, by that layer's priority
      │    for each chosen source, its `find` method: which files or windows the rectangle needs
front ── read method (whole file │ range window), first mirror that answers ─► the source's host
      │    into geodata/.cache/<source id>/
front ── planner-job pack-build {layers: [{layer, source, format, files, params}…]} ─► compiler
      │    each file read by its `format`'s reader; the layers combined by the layer rules
planner-job ── packs/<name>/ with the manifest naming each source and its entry's sha256
```

## Why data, and why no code in it

What a pack is built from changes far more often than how a kind of data is
read: a new country's lidar is a new place with the same feed standard, the
same tile scheme and the same file format as one sim-mesh reads already. So
a source is the parameters, and the methods and readers are sim-mesh's.

A source can never run code. Indexes come from anyone, and a source that
could carry code would make adding an index running a stranger's code. A
dataset no method fits is a new method or reader in sim-mesh, which every
source can then use.

## The file

```
sim-mesh/sources/
  sources.yaml                 every source sim-mesh ships, grouped
  outlines/<id>.geojson        each regional source's coverage
```

```yaml
global:                        # sources with data everywhere
  - { id: glo30, … }
  - { id: worldcover, … }
europe:
  DE:                          # ISO 3166-1 alpha-2
    name: Germany
    sources:
      - { id: zensus, … }
      - { id: berlin-dgm1, … }
  NL:
    name: Netherlands
    sources: […]
north-america:
  US: { name: United States, sources: […] }
```

- The top keys are `global` and the continents: `africa`, `antarctica`,
  `asia`, `europe`, `north-america`, `oceania`, `south-america`. `global`
  holds sources directly; a continent holds countries by their ISO code,
  each with its `name` and `sources`. A source that is one city's or one
  state's (Berlin's) is its country's, its title saying where.
- Ids are unique across the file. A regional source's outline is
  `outlines/<id>.geojson`, beside it.
- A person's own sources are `testbed/sources.yaml`, the same shape, with
  outlines in `testbed/sources/outlines/`. An id sim-mesh ships is refused
  there: a different address for the same data is a mirror, in the shipped
  entry.
- An index lists sources as it lists geodata: entries naming a file of this
  shape by `url` and `sha256`, installed beside the person's own.

A pack's manifest records each source it was built from by id and by the
sha256 of that source's entry as written (its YAML, normalised), so a pack
says what it came from after the file changes.

### Adding to it

An issue proposes a dataset; a pull request adds its entry under its
country and its outline. The repository's tests read the whole file and
refuse one that does not: every entry parsed, ids unique, every outline
there and valid, every regional source with one, every `find` method's
parameters complete. A pull request is reviewed for the licence above all:
only data a pack may carry is `redistributable: true`.

## A source

```yaml
id: glo30                         # a usable name; its cache is geodata/.cache/<id>/
title: Copernicus GLO-30 surface model
holds: >-                         # what its files hold: the page's tooltip
  Surface heights at 30 m …
licence: Copernicus DEM licence, attribution required
notice: "© DLR e.V. 2010-2014 and © Airbus …"   # what the map and the manifest carry
redistributable: true             # false: only values derived from it enter a pack
layers: { surface: 10 }           # each layer it feeds, with its priority there
resolution_m: 30                  # breaks a tie in priority: the finer wins
coverage: worldwide               # or an outline (Coverage)
find: …                           # which files a rectangle needs (Finding)
read: whole                       # how a file is fetched (Reading)
format: …                         # what a file is (Formats)
```

### Layers

What a source can feed, and the rule that makes a pack layer from the
sources that feed it. The rules are sim-mesh's; a source says which layers
it feeds and its priority in each, higher winning.

| layer | what it is | rule |
|---|---|---|
| `surface` | heights of whatever is on top, ground and roofs and crowns | the highest-priority source covering each cell |
| `terrain` | the bare ground's height | the highest-priority source covering the cell at the same priority as the cell's `surface`; else derived from `surface` by morphological opening, so a 1 m terrain is never taken from a 30 m surface |
| `landcover` | a class a cell, in the pack's clutter classes | the highest-priority source covering each cell |
| `buildings` | footprints with heights | every source's buildings, except where a higher-priority source covers the ground: there its buildings alone, judged by each building's centroid |
| `population` | people per area | the highest-priority source covering each cell |
| `roads`, `places` | lines and points for the map and the gazetteer | the highest-priority source covering the rectangle |
| `radio-climate` | ΔN and N0 | values at the pack's centre, into the manifest |

Clutter height, the pack's own layer, is `surface` less `terrain`, with the
buildings' heights merged in where they stand, as the compiler does now.

### Coverage

Where a source has data, which decides whether it is offered for a
rectangle and what the page's map tints. A source's coverage is its own: no
source's depends on another's.

```yaml
coverage: worldwide      # everywhere a build may go (80° S to 84° N); the `global` group's
coverage: outline        # outlines/<id>.geojson, a Polygon or MultiPolygon in degrees
```

Inside its outline, a source that lists its files (`atom`, `stac`,
`regions`) has data where a listed file is; the outline is what is known
without asking.

### Finding

Which files a rectangle needs. Each method's parameters are all a source
says; the method is sim-mesh's.

**Mirrors.** Every address a method takes (`url`, `feed`, `index`,
`catalog`) may be a list: the same data at each, tried in order, the first
that answers taken, the rest only when it fails. A mirror is one source:
one id, one cache, one entry in a pack's manifest.

**`template`**: tiles whose address follows from their corner.

```yaml
find:
  method: template
  url: "https://example/{tile}.tif"      # or a list of mirrors
  tile: "{ns}{lat:02}{ew}{lon:03}"       # named by its south-west corner
  size_deg: 1                            # or size_m, with crs
  crs: EPSG:4326
  missing: no-data                       # a 404 is a tile with nothing in it (open sea),
                                         # for every method unless `error`: a failed build
```

Placeholders: `{lat}` and `{lon}`, the corner's whole degrees without sign
(or `{x}`, `{y}` in the tile's units for a projected grid); `{ns}` `N` or
`S`, `{ew}` `E` or `W`; `:0n` pads to n digits.

**`atom`**: an INSPIRE download feed listing every tile (the EU's standard:
Berlin, the German states, the Netherlands, Denmark, …).

```yaml
find:
  method: atom
  feed: "https://gdi.berlin.de/data/dgm1/atom/0.atom"
  name: "(?P<x>\\d{3})_(?P<y>\\d{4})\\.zip$"   # where a tile's corner is in its file name
  unit_m: 1000                                 # the corner's unit
  size_m: 2000
  crs: EPSG:25833
  refresh_days: 7
```

**`stac`**: a SpatioTemporal Asset Catalog collection; each item's
footprint says where its file is.

```yaml
find:
  method: stac
  catalog: "https://earth-search.aws.element84.com/v1"
  collection: cop-dem-glo-30
  asset: data
```

**`regions`**: an index of regional extracts, each with its outline, of
which the smallest holding the rectangle is taken.

```yaml
find:
  method: regions
  index: "https://download.geofabrik.de/index-v1.json"
  index_format: geofabrik           # how the index names outlines and files
  file: pbf                         # which of an extract's files
  refresh_days: 7
```

**`file`**: one file for everywhere it covers.

```yaml
find: { method: file, url: "https://…/Zensus2022_Bevoelkerungszahl.zip" }
```

A zip's members are named by `format.members`, a glob; only those are
extracted.

### Reading

How a found file is fetched into the cache:

- `whole`: the file, resumed by range requests when interrupted, kept by
  its name. What every source in the starting set uses.
- `window`: only the rectangle's part of a cloud-optimised file, read by
  range requests through the file's own index: `cog` rasters, `copc` point
  clouds, `flatgeobuf` and `geoparquet` features. Kept as a sparse copy of
  the whole file, a `.ranges` beside it saying what it holds, so windows of
  later rectangles add to the one copy.

A query service (OGC API, WCS, Overpass) is not a read method: their limits
and timeouts make a city's build unreliable.

### Formats

What a file is, with the reader's parameters:

| format | parameters | feeds |
|---|---|---|
| `geotiff` | `band`, `nodata`, `classes` for categorical data: its codes to the pack's clutter classes | surface, terrain, landcover, population |
| `xyz` | `crs`, `spacing_m`, `members` | surface, terrain |
| `citygml` | `lod`, `crs`, `members` | buildings |
| `osm-pbf` | which layers it gives | roads, places, buildings |
| `csv-grid` | `delimiter`, `x`, `y`, `value` columns, `crs`, `cell_m`, `members` | population |
| `itu-p1812-maps` | `members`: ΔN's and N0's files | radio-climate |

The pack's clutter classes are `open`, `water`, `low-vegetation`, `forest`,
`suburban`, `urban`, `dense-urban`, `industrial`.

## The starting set

The eight sources as they are built today, as `sources/sources.yaml`. The
three Berlin and the Zensus outlines go beside it: Geofabrik's outlines of
Berlin and of Germany, the borders a build has always judged "touches" and
"inside" by; inside Berlin's, its feeds' tiles say where there is data.

```yaml
global:
  - id: glo30
    title: Copernicus GLO-30 surface model
    holds: >-
      Surface heights at 30 m: a digital surface model, the ground with
      buildings and trees on it, in 1° GeoTIFF tiles.
    licence: Copernicus DEM licence, attribution required
    notice: >-
      © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018
      provided under COPERNICUS by the European Union and ESA; all rights
      reserved. Produced using Copernicus WorldDEM-30.
    redistributable: true
    layers: { surface: 10 }
    resolution_m: 30
    coverage: worldwide
    find:
      method: template
      url: "https://copernicus-dem-30m.s3.amazonaws.com/{tile}/{tile}.tif"
      tile: "Copernicus_DSM_COG_10_{ns}{lat:02}_00_{ew}{lon:03}_00_DEM"
      size_deg: 1
      crs: EPSG:4326
      missing: no-data
    read: whole
    format: { type: geotiff, band: 1 }

  - id: worldcover
    title: ESA WorldCover 2021 land cover
    holds: Land cover at 10 m, one class a cell, in 3° GeoTIFF tiles.
    licence: CC BY 4.0
    notice: >-
      © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel
      data (2021) processed by the ESA WorldCover consortium — CC BY 4.0
    redistributable: true
    layers: { landcover: 10 }
    resolution_m: 10
    coverage: worldwide
    find:
      method: template
      url: "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
      tile: "{ns}{lat:02}{ew}{lon:03}"
      size_deg: 3
      crs: EPSG:4326
      missing: no-data
    read: whole
    format:
      type: geotiff
      band: 1
      classes: { 10: forest, 20: low-vegetation, 30: low-vegetation, 40: open,
                 50: urban, 60: open, 70: open, 80: water, 90: low-vegetation,
                 95: low-vegetation, 100: open }

  - id: itu
    title: ITU-R P.1812-8 ΔN and N0 maps
    holds: >-
      The radio-climate maps: ΔN, the refractivity gradient over the lowest
      kilometre, and N0, the sea-level surface refractivity.
    licence: ITU; not redistributable
    redistributable: false
    layers: { radio-climate: 10 }
    coverage: worldwide
    find: { method: file, url: "https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1812-8-202509-I!!ZIP-E.zip" }
    read: whole
    format: { type: itu-p1812-maps, members: { delta_n: DN50.TXT, n0: N050.TXT } }

  - id: geofabrik
    title: OpenStreetMap extract (Geofabrik)
    holds: >-
      OpenStreetMap, one extract per region: roads and rail, places, peaks
      and masts, and building footprints with their tagged heights or levels.
    licence: ODbL 1.0
    notice: © OpenStreetMap contributors, ODbL 1.0 (opendatacommons.org/licenses/odbl)
    redistributable: true
    layers: { roads: 10, places: 10, buildings: 10 }
    coverage: worldwide
    find:
      method: regions
      index: "https://download.geofabrik.de/index-v1.json"
      index_format: geofabrik
      file: pbf
      refresh_days: 7
    read: whole
    format: { type: osm-pbf, gives: [roads, places, buildings] }

europe:
  DE:
    name: Germany
    sources:
      - id: zensus
        title: Zensus 2022 100 m population grid
        holds: Germany's 2022 census, people per 100 m grid cell, one CSV for the country.
        licence: Datenlizenz Deutschland – Namensnennung – 2.0
        notice: "© Statistisches Bundesamt (Destatis), Zensus 2022 — Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0)"
        redistributable: true
        layers: { population: 100 }
        resolution_m: 100
        coverage: outline
        find: { method: file, url: "https://www.destatis.de/static/DE/zensus/gitterdaten/Zensus2022_Bevoelkerungszahl.zip" }
        read: whole
        format:
          type: csv-grid
          members: "Zensus2022_Bevoelkerungszahl_100m-Gitter.csv"
          delimiter: ";"
          x: x_mp_100m
          y: y_mp_100m
          value: Einwohner
          crs: EPSG:3035
          cell_m: 100

      - id: berlin-dgm1
        title: Berlin DGM1, 1 m terrain
        holds: Berlin's terrain model at 1 m, the bare ground's height, as XYZ text in 2 km tiles.
        licence: Datenlizenz Deutschland – Zero – 2.0
        notice: "Geoportal Berlin: ATKIS® DGM1 — Datenlizenz Deutschland – Zero – Version 2.0 (dl-de/zero-2.0)"
        redistributable: true
        layers: { terrain: 100 }
        resolution_m: 1
        coverage: outline
        find:
          method: atom
          feed: "https://gdi.berlin.de/data/dgm1/atom/0.atom"
          name: "(?P<x>\\d{3})_(?P<y>\\d{4})\\.zip$"
          unit_m: 1000
          size_m: 2000
          crs: EPSG:25833
          refresh_days: 7
        read: whole
        format: { type: xyz, crs: EPSG:25833, spacing_m: 1, members: "*.xyz" }

      - id: berlin-bdom
        title: Berlin bDOM, 1 m surface
        holds: >-
          Berlin's surface model at 1 m, matched from aerial images: the height
          of whatever is on top, as XYZ text in 2 km tiles.
        licence: Datenlizenz Deutschland – Zero – 2.0
        notice: "Geoportal Berlin: bDOM — Datenlizenz Deutschland – Zero – Version 2.0 (dl-de/zero-2.0)"
        redistributable: true
        layers: { surface: 100 }
        resolution_m: 1
        coverage: outline
        find:
          method: atom
          feed: "https://gdi.berlin.de/data/bdom/atom/0.atom"
          name: "(?P<x>\\d{3})_(?P<y>\\d{4})\\.zip$"
          unit_m: 1000
          size_m: 2000
          crs: EPSG:25833
          refresh_days: 7
        read: whole
        format: { type: xyz, crs: EPSG:25833, spacing_m: 1, members: "*.xyz" }

      - id: berlin-lod2
        title: Berlin LoD2 building models
        holds: >-
          Berlin's 3D buildings at level of detail 2 (CityGML): each building's
          footprint, roof shape and height, in 1 km tiles.
        licence: Datenlizenz Deutschland – Zero – 2.0
        notice: "Geoportal Berlin: 3D-Gebäudemodelle LoD2 — Datenlizenz Deutschland – Zero – Version 2.0 (dl-de/zero-2.0)"
        redistributable: true
        layers: { buildings: 100 }
        coverage: outline
        find:
          method: atom
          feed: "https://gdi.berlin.de/data/a_lod2/atom/0.atom"
          name: "(?P<x>\\d{3})_(?P<y>\\d{4})\\.zip$"
          unit_m: 1000
          size_m: 1000
          crs: EPSG:25833
          refresh_days: 7
        read: whole
        format: { type: citygml, lod: 2, crs: EPSG:25833, members: "*.xml" }
```

Three things the build decides today by name become rules of this file:
Berlin's terrain and surface over GLO-30, and LoD2's buildings over
OpenStreetMap's, are priority 100 over 10 in their layers; Berlin's data is
only offered inside its outline and where its feed has a tile; the Zensus
grid only inside Germany's outline.

## The page

The Geodata tab's sources section lists the sources grouped as the file
groups them: **Global** open, each continent and each country folded, with
how many sources each holds; a country added by a person or an index is
marked as theirs. Expanding a group shows its sources, each with the layers
it feeds and its priority in each. A click on the map pins a point and
unfolds just the groups with a source there, listing only those. The map
draws a source's coverage and its cache as now. Build's side panel says,
for each layer, which source the rectangle takes and where.

## Order

1. The file, its reader and the repository's tests in sim-mesh, the
   starting set in it with its outlines, and the build planning from it:
   the same packs as today, byte for byte.
2. The compiler taking its inputs as layers with formats, not by name.
   Built for GeoTIFF terrain and surface in any known projection, CityJSON
   and CSV or GeoPackage population grids. Left: XYZ and CityGML beyond
   Berlin's layouts, land cover beyond WorldCover's classes, a worldwide
   surface outside EPSG:4326.
3. `testbed/sources.yaml`, sources listed by indexes, and the page's
   grouped list. Built.
4. `window` reading for `cog`, so GLO-30 and WorldCover fetch only the
   rectangle. Built for regional sources; the worldwide ones still read
   whole tiles.
5. `stac`, and readers past the starting set (`copc`, `geoparquet`), as a
   region asks for them.
