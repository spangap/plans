# sim-mesh: geodata sources as files — what is still open

Sources as data are built and documented: sim-mesh's README ("Sources",
"Build from sources") and INTERNALS ("Building a pack, and the node maps").
What follows is what the agreed design (2026-10-04) has not reached yet.

## The manifest names each source and its entry's sha256

So a pack says exactly which entries, as written, it was built from. Today
the manifest carries only `LicenseNotice{source, notice}`.

## An index lists sources

`testbed/indexes.py` knows `geodata` and `nodesets`; a third kind,
`sources`, so a published source file installs the way ground and nodesets
do, by sha256.

## Single-source inputs combined per cell across a border

Population (and any input that takes one source) refuses a rectangle that
two sources meet, so a rectangle over both Germany and the Netherlands is
refused ("the compiler takes one source for population"). The compiler
should take several and fill each cell from the one that covers it, as land
cover now does (worldwide first, regional over it).

## The compiler's readers take their parameters from the source

Still fixed to one layout (`sourcefile.COMPILER_READS`): XYZ (at 1 m) and
CityGML as the German state surveys deliver them, in an ETRS89 UTM zone, a
worldwide surface as EPSG:4326.

## Europe past what reads today

Surveyed 2026-10-08; the open data that fits is shipped. What is left, by
what it needs:

- A projection (no service reprojects these):
  - Luxembourg: 2024 lidar DTM/DSM 0.5 m, one cloud-optimised GeoTIFF
    each (40 GB), EPSG:2169; float64 pixels. CC0.
  - Switzerland and Liechtenstein: swissALTI3D / swissSURFACE3D raster COG
    tiles, EPSG:2056 (oblique Mercator), the year in each tile's address
    (a STAC index would list them). swisstopo open data.
  - England: Environment Agency 1 m DTM/DSM by WCS per 1 km, EPSG:27700
    (British National Grid, Helmert shift). OGL v3. Wales: one COG each,
    27700.
  - Ireland: OPW 2 m flood-survey tiles in ArcGIS layers, EPSG:2157.
  - Brussels: 0.5 m DTM/DSM ATOM tiles, EPSG:31370 with a datum shift,
    georeferenced only by `.tfw`. CC0.
  - Slovenia: ARSO 1 m XYZ (semicolons), EPSG:3794, a block index.
  - Estonia's 1 m DSM per sheet (EPSG:3301); Cyprus 1 m / 0.5 m (6312/4258).
- A reader:
  - ESRI ASCII grid: Poland's DSM (WCS `image/x-aaigrid`), Trento, Friuli,
    Toscana, the Basque Country.
  - Map-sheet codes as a template field: Finland's 2 m DTM on CSC's Funet
    mirror (EPSG:3067, already a UTM alias).
  - Population from several sources combined per cell (above) before any
    WorldPop country grid can ship: one more population source makes every
    rectangle across that border refuse.
- Choosing within a pair: two terrains of one projection overlapping
  (Spain's national 5 m under Andalucía's 1 m, Emilia-Romagna's 5 m under
  its 1 m surveys) give whichever tile a sample meets first; a priority
  within a pair would let the national ones ship as fallbacks.
- Not open: Sweden, Denmark and Portugal need an account or key; Hungary,
  Serbia and the Western Balkans sell theirs; Valencia's 1 m tiles are up
  to 1.4 GB each (whole only), left out for that.

## Finding and reading

- Hessen's LoD2: one CityGML file per municipality (483 zips, 10 GB), from
  the HVBG Downloadcenter's REST tree, whose download addresses carry a
  date valid only today and yesterday; no tiles, no static address. Its
  terrain and surface come from the INSPIRE WCS; its buildings are
  OpenStreetMap's until a method reads that tree (or its INSPIRE
  Buildings 3D WFS).
- 3DEP's 1 m DEMs: 10 km tiles per collection project, each project its
  own folder and NAD83 UTM zone, so no one template names them; an index
  (USGS's per-project footprints) would.
- Vienna's LoD2.1 roof model (CityGML, EPSG:31256, Wiener Null heights):
  served only per 500 m square from the Geodatenviewer, no feed or tile
  addresses, data from 2013. BEV's 2025 lidar pair measures Vienna's
  buildings meanwhile; footprints there are OpenStreetMap's.
- `stac`: a SpatioTemporal Asset Catalog collection, each item's footprint
  saying where its file is:

  ```yaml
  find:
    method: stac
    catalog: "https://earth-search.aws.element84.com/v1"
    collection: cop-dem-glo-30
    asset: data
  ```

- `window` for the worldwide rasters, so GLO-30 and WorldCover fetch only
  the rectangle (built for regional sources; `_check` refuses it for a
  worldwide one).
- Windowed readers past the starting set, as a region asks for them: `copc`
  point clouds, `flatgeobuf` and `geoparquet` features, kept as the same
  sparse copy with `.ranges` that `cog` windows use. `copc` is the way to a
  measured US surface (3DEP's lidar point clouds; USGS publishes no DSM).
- A US building source with heights: USA Structures (FEMA/ORNL, per-state
  geodatabases) and Microsoft's footprints carry none, so OpenStreetMap's
  stand there today.

## The page's grouped list (or drop it)

The design had the sources section grouped as the file groups them —
Global open, each continent and country folded with its count, a pinned
point unfolding only the groups with a source there. The list is flat
today; decide whether the grouping is still wanted.
