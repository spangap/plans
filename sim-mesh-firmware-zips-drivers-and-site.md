# sim-mesh: firmware zips and drivers — what is still open

Firmware as zips, drivers in the zip, run-time radios, typed script inputs
and sim-mesh.net's pre-built firmware are built and documented: sim-mesh's
README ("Firmware", "The firmware contract", "Publishing firmware") and
INTERNALS ("Drivers, and the rules that come with more than one firmware").
The `meshcore`/`meshtastic` categories and the LR2021 radio are tracked in
INTERNALS "Still to build". What is left is on the firmware projects' side.

## Zip scripts, committed

The reticulous zips installed and published (`reticulous-dev-sx1262`,
`reticulous-stable-sx1262`) carry `driver.py`, `lib/`, `category` and
`radio`, but no committed script makes them: spangap's node package
(`spangap-inside`, `_write_node_package`) has none of those four. Needed:
a script per project that turns its build into a sim-mesh firmware zip —
reticulous (from a make-builds image), microreticulum, standard-reticulum.
Sergeyculum has one: `fw/sim-mesh/make_zip.py` in its own repository
(git.emcomm.cc/berlinmesh/reticulum).

## Drivers, with their source

The reticulous driver exists only inside the installed zips; its source is
in no repository. Sergeyculum's is `fw/sim-mesh/driver.py` in its own
repository. microreticulum and standard-reticulum have no driver.

## Where they live

The design put each project's driver, zip script and tests in a sister
directory, `../sim-mesh-firmware/<project>/`; it does not exist. sim-mesh's
README now says a firmware project makes its own zip, which points at each
project's own repository instead. Decide which, then move the driver and
zip script there.
