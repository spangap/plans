# sim-mesh: indexes of ground and nodesets, sizes, and row actions

Status: agreed 2026-10-03, being built.

## An index

One YAML file lists geodata packs and nodesets that can be fetched, so a
standard set of ground and nodesets is the same bytes on every machine.
sim-mesh carries the address of its own, `sim-mesh-examples` at
sim-mesh.net/examples/index.yaml; anyone can
publish another, and a person adds its address on either page.

```
page ── index_list ─────────────────────────────► front ── GET <address> ──► host of the index
page ── index_add {index, kind, name} ──────────► front ── GET <entry url> ─► host of the file
front ── index_progress {index, kind, name, fetched, of} ─► every page
front: sha256 and size checked, then installed as Import zip or a nodeset file would be
```

```yaml
index: sim-mesh-examples              # a usable name
title: sim-mesh examples
description: |                        # the collection, printed by the page and `sim index list`
  These examples will soon include some varied geographies and nodesets. …
geodata:
  - name: berlin-mitte
    title: Berlin Mitte, dense flat city
    url: berlin-mitte.zip             # relative to the index's own address
    sha256: <64 hex digits>
    bytes: 9400000
    bbox: [13.36, 52.50, 13.44, 52.54]
    licences: ODbL 1.0; dl-de/zero-2.0; CC BY 4.0; Copernicus
    tags: [standard, urban, flat]
    description: …
nodesets:
  - name: mitte-40
    title: 40 rooftop nodes in Mitte
    url: mitte-40.yaml
    sha256: <64 hex digits>
    bytes: 5210
    geodata: berlin-mitte             # the ground it is made for
    nodes: 40
```

- An entry is immutable: its sha256 is what it is. A changed pack or
  nodeset is published under a new name.
- An address is http(s), a `file://` URL or a local path; one ending in `/`
  means its `index.yaml`. An entry's `url` is relative to it.
- Installed, an entry remembers where it came from (index, address, url,
  sha256). A name already installed with other bytes is refused, with the
  sentence saying so; with the same bytes it is installed.
- Adding a nodeset whose `geodata` is in the same index and not installed
  adds that geodata first.

## The Geodata tab

```
Installed geodata packs                 [New synthetic…] [Build…] [Import zip…]
  [All] [None] [Invert]  [Delete]
  ☐ berlin-mitte   pack · 10 layers   52.50…52.54 N   3 nodesets   48 MB   🗑
  ☐ plain-27       synthetic …                        1 nodeset    1 KB    🗑

Download pre-built geodata packs                                   [Add index…]
  sim-mesh-examples  sim-mesh examples
     These examples will soon include some varied geographies and nodesets. …
     berlin-mitte   Berlin Mitte, dense flat city    9 MB   installed
     alps-valley    …                               14 MB   [Add]
  someone-elses   …                                                 🗑
     …

Geodata sources
  Copernicus GLO-30   CC …    1.2 GB cached   🗑
  …
```

The sources section is the build's cache, one row per source, with its
licence and what it holds; its trash can empties that source's cache,
refused while a build runs.

## Nodesets

The Layers panel's **Nodesets…** opens the same two sections for nodesets:
every installed nodeset (checkbox, nodes, size, trash) and the indexes'
nodesets, each saying the geodata it is made for.

**Import…** gains GeoJSON points, KML placemarks, GPX waypoints, a
Meshtastic node list (`meshtastic --info`'s JSON), and any CSV, whose
columns the dialog asks to be named (which is latitude, longitude, name,
height, power, tags).

## Sizes

Every row that is something on disk says how much: runs, geodata, nodesets,
firmware, snapshots, source caches, and an index's entries (what a download
would take).

## The Simulations tab

- **done** is a simulation its script paused, **paused** one a person
  paused (the page, `sim pause`). Both have ▶; done is drawn apart.
- Play, pause and stop are icons, each in a narrow column of its own so
  they line up; ⋯ and Report after them; the trash can last, on every row.
  A running one's warns that it is stopped first.
- A checkbox begins each row. Above: All, None, Invert, and Stop, Pause and
  Delete for the selection, each skipping rows it does not apply to, with
  one confirmation saying what happens.

The same checkbox row and bulk Delete go on Firmware, installed geodata and
nodesets.

## The CLI

```
sim index list | add ADDRESS | delete NAME
sim geodata list | offered [SUBSTRING] | add NAME|ZIP… | delete [-f] NAME…
sim nodeset list | offered [SUBSTRING] | add NAME… | delete [-f] NAME…
```
