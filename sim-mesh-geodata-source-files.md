# sim-mesh: geodata sources as files — what is still open

Sources as data are built and documented: sim-mesh's README ("Sources",
"Build from sources") and INTERNALS ("Building a pack, and the node maps").
What follows is what the agreed design (2026-10-04) has not reached yet.

## A pack credits its own sources (a live bug)

`planner-pack/src/build.rs` (around 1143–1153) writes Berlin's notices for
any XYZ or CityGML input, and `packbuild.params` passes no notice for
`lod2_dir` or `berlin_1m_dir`. A pack built from Brandenburg's or
Mecklenburg-Vorpommern's LoD2, or Mecklenburg-Vorpommern's DGM1/DOM1, is
credited "Berlin LoD2 3D building models" / "Berlin DGM1 + bDOM". Each input
should carry its source's own `notice` into the manifest.

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
should take several and fill each cell from the one that covers it.

## The compiler's readers take their parameters from the source

Still fixed to one layout (`sourcefile.COMPILER_READS`): XYZ and CityGML as
the German state surveys deliver them in EPSG:25833, land cover as
WorldCover's classes, a worldwide surface as EPSG:4326.

## Finding and reading

- `template` for projected grids: `size_m` with `crs`, and `{x}`/`{y}` in
  the tile's units, beside today's whole-degree EPSG:4326 tiles.
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
  sparse copy with `.ranges` that `cog` windows use.

## The page's grouped list (or drop it)

The design had the sources section grouped as the file groups them —
Global open, each continent and country folded with its count, a pinned
point unfolding only the groups with a source there. The list is flat
today; decide whether the grouping is still wanted.
