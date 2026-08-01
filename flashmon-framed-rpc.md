# flashmon: framed serial RPC instead of log tailing

Near-term work. Independent of `cli-overhaul.md` and needs none of it.

## Problem

flashmon learns everything about a device by tailing the boot log with regexes.
That is fragile in three ways: the interesting lines scroll past once and never
repeat, the log is suppressed the moment the user presses a key (the console
switches to a CLI session), and every fact depends on a log string nobody thinks
of as an API. One of the couplings is already documented on the device side —
`spangap_init.cpp:141` carries a comment saying a flasher tails that line.

It already does the right thing once, the wrong way: on connect it *sends*
`show s.net.hostname` and then scrapes the answer back out of the log stream
(`flashmon.py:838`, matched at `flashmon.py:828`). The transport is the only
thing missing.

## Wire format

A framed side-channel on the existing console port, interleaved with log and CLI
traffic:

```
host → device   <magic:4> <id:1> <len:2> <command bytes>
device → host   <magic:4> <id:1> <len:2> <reply bytes>
```

- 4-byte magic, 1-byte query id (see below), 2-byte length. The magic won't occur
  by chance, and it drops any constraint on which byte values may appear.
- Never echoed, never enters the line editor, never flips the console into CLI
  mode.
- flashmon swallows frames out of the byte stream and displays the rest
  unchanged, so log and interactive CLI keep working exactly as now.

Decide once, and define the constant in one place per side:

- **Magic** — `0xF5 'S' 'G' 0x01`. The leading byte cannot appear in valid UTF-8,
  so every non-frame byte in the stream fails the match on byte one.
- **Length** — 2 bytes, big-endian, counting the payload only. Cap is therefore
  0xffff; allocate on demand when the length is read and free when the frame is
  done. Take the reply buffer from PSRAM
  (`heap_caps_malloc(…, MALLOC_CAP_SPIRAM)`) — 64 KB of internal DRAM is far too
  much on a device where every task stack comes out of it. On a board without
  PSRAM that allocation fails outright; fall back to a small internal buffer
  (8 KB is generous — every reply flashmon actually reads fits in a screenful)
  rather than making PSRAM a dependency of the transport. Truncation already
  has defined behaviour below.
- **Truncation is not signalled.** A reply that would exceed the cap is simply
  cut, because flashmon only ever issues bounded queries and its reader already
  treats a missing key as unknown. Cut at the **last complete line**, though: a
  mid-line cut turns `state=ap` into `state=a`, which parses as a valid but wrong
  value, whereas dropping the partial tail leaves only the missing-key case that
  is already handled.
- **Frame assembly timeout.** A corrupted length — a flaky cable, not an
  adversary — otherwise leaves the device allocated and waiting for bytes that
  never arrive. If the payload doesn't complete within about a second, abandon it
  and resync on the magic. Same reasoning as the safety deadline on
  `cliReadLine`: the failure being guarded against is a wedge.
- **Empty reply** — a command that prints nothing still answers, with a
  zero-length frame. Otherwise "no output" and "device didn't answer" are
  indistinguishable.
- **Failure** — a command that fails still answers with whatever it printed. The
  frame carries no status of its own; that arrives later with exit codes
  (`cli-overhaul.md`).

No integrity checking beyond that, deliberately. The peer on the serial console
can already send `reset factory`; there is nothing to defend against here, only
noise to recover from.

### Query id

Keep it strictly one frame in flight; nothing here needs concurrency. But a
host-side timeout otherwise returns a *wrong* answer rather than no answer: if
flashmon gives up and the device replies late, that reply is in the stream when
the next query goes out and gets taken as its answer. `show sys.build` times out,
`auth -O` receives the build subtree, finds no `admin=`, and reports the password
state as unknown — with no error anywhere. That is the scraping failure mode
reappearing behind a transport that looks reliable, so it is worth one byte to
close.

The frame carries a one-byte **query id**, echoed in the reply:

```
host → device   <magic:4> <id:1> <len:2> <command bytes>
device → host   <magic:4> <id:1> <len:2> <reply bytes>
```

It identifies *what was asked*, not when. So retrying an unanswered read reuses
the same id, and a late reply to the first attempt is a perfectly good answer to
the second — no discarding, no waiting for the link to go quiet. Duplicate
replies for one id are either reprocessed harmlessly or ignored; a reply whose id
doesn't match the outstanding query is dropped.

The device never interprets the id — it copies it from request to reply. All the
policy is in flashmon, so the id can simply be derived from the command string
with no state on either side.

**Mutating commands need no exception.** A reply is only ever produced by an
execution, so a reply carrying this id proves some send of exactly this command
ran. Retrying a timed-out `save` re-sends the same bytes, and a late reply to
the first attempt confirms the same effect as a prompt reply to the second —
which execution answered is immaterial, for writes as for reads. The residual
case — state changed by other means between two sends of the same command — is
not worth a mechanism at one query in flight.

### Devices that don't speak it

flashmon flashes old firmware too, and anything built before this lands will
never answer a frame. Worse, a frame sent blind at old firmware is *typed at
the console*: any first keystroke opens a CLI session (`handleChar`,
`cli.cpp:1854`), which suppresses the log — exactly the stream the scraping
fallback reads. So capability is advertised, never probed. The device prints
one marker line the moment the sniffer arms, very early in boot:

    serial: framed rpc v1

flashmon uses frames only after it has seen the marker this session. No
marker, no frame ever leaves the host, and everything behaves as today.

This is one more load-bearing log line, of exactly the kind this plan exists
to remove — accepted deliberately: one line, one bit plus a version, emitted
before anything can be in flight, documented as an API on both sides.

The consequence: flashmon attached to an already-running device has not seen
the boot, so it stays in legacy mode until the next reboot even on firmware
that speaks frames. That is the normal condition of log scraping anyway — the
interesting lines have already scrolled past — and the main flow (flash,
watch it boot) sees the marker every time.

**On no marker, fall back to today's log scraping** rather than failing. That
keeps the existing parsers alive for one release cycle. They can be deleted
once the oldest image the catalogue still offers speaks frames, which is why
that deletion is the last step of the order below rather than part of the
conversion.

## Device side

Sniff the preamble in `handleChar` (`spangap-core/esp-idf/src/cli.cpp`). That is
the right place: it sits ahead of both the line editor and the log/CLI switch, so
a frame arriving mid-line doesn't disturb the editor and one arriving in log mode
doesn't flip modes.

Precedent exists — the same function already sniffs a magic byte in-band (`0xC0`
opens a serial-handler attach on USB-Serial-JTAG, where there is no DTR to
watch).

The sniffer needs a small state machine rather than a buffer compare: `portRead`
delivers 128-byte chunks and a preamble can straddle two of them. `handleChar` is
called from two places — the idle blocking-read path and the active drain path —
so the state has to live outside it, alongside the other serial-task state, or
frames break depending on which path happened to receive them.

Three rules for that state machine:

- **A failed match replays, not drops.** Bytes withheld while the magic was
  partially matched belong to the normal path — when a byte disagrees, run the
  held bytes through the rest of `handleChar`. The 0xF5 lead makes false starts
  rare (it cannot occur in UTF-8 text), but paste garbage exists.
- **Mid-frame swallows everything, including 0xC0.** The frame state machine
  runs ahead of the 0xC0 attach check, so an id or length byte of 0xC0 must not
  open a handler session. Idle, the 0xC0 check keeps its place.
- **Frames are dead while a handler owns port 0** — bytes go to `hdlPump` and
  never reach `handleChar`. Deliberate: the handler mechanism exists for
  Reticulum clients (RNode), and a port claimed for one is for-sure not
  flashmon.

**Reply frames must not interleave with the log.** In log mode the log task
echoes lines straight to stdout from its own task (`logVprintf`,
`log.cpp:435`), while the serial task writes through `serialEmit`, which on
USB-Serial-JTAG bypasses the VFS entirely (`cli.cpp:1503`). Two tasks, two
write paths, no shared lock: a log line can land mid-frame, and because the
payload is length-counted the host then swallows log bytes as reply and shows
displaced reply bytes as garbage — resync-on-magic can't help once the length
has been read. The direct echo itself is not the thing to fix: it is
deliberate (`log.cpp:432` — logs must reach the wire even when the serial
task is wedged or not yet up), and it is what lets the idle serial task park
indefinitely on the driver's RX ring (`cli.cpp:1930`) instead of running a
notify-and-poll loop. Keep it, and add a console-write mutex taken around the
echo's `fwrite` and around each whole reply frame. Cost: the echo can stall
for the duration of one frame write, bounded by the host draining the port.

Transport-agnostic: it sits above both USB-Serial-JTAG and CDC, unlike the
`0xC0` attach, which is gated on `!consoleOnCdc` because it substitutes for a DTR
signal that only CDC has. flashmon may be on either.

A frame arriving while the user has a CLI session open must still work — the
sniff happens before the byte is forwarded to the CLI, and the RPC runs on its
own ITS session, so the user's line in progress is untouched.

**Execution** reuses the existing one-shot exec path, so no core changes: open a
second ITS client connection to `CLI_PORT_TCP` with
`cli_connect_t{CLI_LINE, from_usb_serial=0, CLI_NO_COLOR, no_prompt=1, login=0}`,
send `"<cmd>;\n"`, read until the trailing `;` closes the session, frame the
bytes back out. This is what ssh `exec` already does
(`sshd/esp-idf/src/sshd_session.cpp:923`).

Session accounting — there is no headroom, it has to be made:

- The serial task's `itsClientInit(2)` (`cli.cpp:1702`) is a hard per-task cap
  (`its.cpp:1676` fails the connect), and both slots are spoken for in the
  worst case: a console CLI session plus a handler session on port 1. The RPC
  exec is a third concurrent client connection — raise the cap to 3.
- Server side, the `CLI_PORT_TCP` pool is 6 (`cli.cpp:1357`), shared with the
  console session and every ssh shell and exec. One more consumer class; bump
  it to 8 so a full house of ssh sessions can't starve the RPC.

Two guards on the relay:

- **An exec deadline.** The frame-assembly timeout covers the inbound half; a
  command that never finishes would wedge the relay — and the console with
  it — on the outbound half. Bound the read-until-`;` the same way, generous
  (a few seconds), then disconnect the session and reply with whatever had
  been printed.
- **A retry arriving mid-exec needs no handling** — but only because the
  serial task processes frames synchronously: the second frame waits in the
  driver's buffer until the first exec finishes, both get answered, and the
  duplicate-reply rule absorbs the extra. An async implementation would have
  to choose a policy here, which is a reason to stay synchronous.

## Inventory: what flashmon needs, and what replaces the scrape

| Need | Scraped today | Replacement |
|---|---|---|
| Catalogue stamp of the running image (drives the flash offer) | `build: datetime (\d{14})` — `flashmon.py:811`, emitted at `spangap_init.cpp:141` | `show sys.build` — one query for the whole subtree; `datetime` set at `spangap_init.cpp:134` |
| Board the image was built for | not available | `show sys.build` → `hw` — **key to be added** |
| Which distribution the image is | not available | `show sys.build` → `dist` — **key to be added** |
| Is a device password set? | `"No device password set"` — `flashmon.py:822` | `auth -O` |
| WiFi state: down / connecting / AP / joined | `AP ssid=… ip=…` (`flashmon.py:825`) and `Connected "…" ip … dns …` (`flashmon.py:833`) | `net -O` |
| Device IP, for the web-UI URL | same `Connected …` line | `net -O` |
| Hostname | `show s.net.hostname` sent, reply scraped from the log (`flashmon.py:828`, `:838`) | `net -O` carries it; `show s.net.hostname` still works and needs no `-O` |
| Stored networks | not available | `net list` |
| Nearby APs, with open/closed flag | `scan found "…" -NNdBm … open` — `flashmon.py:818`, emitted at `spangap-net/esp-idf/src/net.cpp:802` | **`net scan -O` — to be added** |
| Flash size, floor, `/state` geometry | not available | `show sys.flash` — **keys to be added**; falls back to reading the partition table at `0x8000` |
| Board type | not from the device (esptool chip detection) | unchanged — detection is still needed before the first flash, and whenever the device does not boot |

The *commands* flashmon sends need no change: `auth passwd admin <pw>`,
`hostname <name>`, `net add "<ssid>" "<pw>"`, `save`. Quoting is already handled
for `net add`/`net join` by net.cpp's own `parseArgs`. Only the transport
changes — they go out as frames instead of raw bytes typed at the console, which
is what stops provisioning from colliding with a user who is typing, and gives
each one a reply to confirm against.

## Device-side additions

### `-O`: onboarding output

Don't parse the human formatting. Commands flashmon depends on take `-O` and
print exactly what onboarding needs, nothing else. The flag is the contract: it
says on the device side that this output is depended upon and by whom, so it is
greppable and can't be tidied away by someone improving the status display. The
existing `scan found` comment is reaching for the same thing informally.

Format: `key=value`, one per line, value running to end of line. No quoting, no
escaping, trivial to emit and to parse. Frames are length-prefixed, so the
transport imposes no delimiter constraints of its own.

    net -O
      state=ap|sta|connecting|down
      ssid=…
      ip=…
      hostname=…

    auth -O
      admin=set|unset|locked

    net scan -O
      count=<n>
      ap=<rssi> <open|closed> <ssid>

Readers ignore keys they don't know and treat missing keys as unknown; neither is
an error. `auth -O` reports whichever realms exist and onboarding reads only
`admin`, so that set can grow without breaking anything.

The one edge case is an SSID containing a newline. `net scan -O` emits `count`
first so the reader knows how many records to expect; entries whose SSID is not
representable on one line are skipped rather than corrupting the stream, and
`count` is computed after that filter, so it always equals the number of `ap=`
lines. `ap=` puts the SSID last so spaces need no quoting.

`show` is already machine-shaped (`s.net.hostname = value`) and takes a prefix,
so nothing under `sys.build` or `sys.flash` needs `-O`.

Cost: a second output path per command, which no human ever looks at and can rot
silently. Mitigations are to keep `-O` output minimal, and that onboarding
working at all is the test.

### `net scan`

The AP list is only ever published as `scan found …` log lines. Add a subcommand
that prints the cache the ordinary scan cycle has already built. It does **not**
start a scan: the connect path scans anyway, and the result is available as soon
as that has run once.

Most of this exists. `scanForKnown` (`spangap-net/esp-idf/src/net.cpp:762`)
already sorts by RSSI descending, skips hidden SSIDs, and dedupes per SSID across
every scan this boot in `loggedSsids[]` — whose comment already names the
flasher's connect helper as the consumer. It just discards the RSSI and the open
flag after printing, because the array holds the SSID only.

- Widen `loggedSsids[64][33]` to a record of `{ssid, rssi, open}`. About 200
  extra bytes of static.
- On a repeat sighting, keep the loudest RSSI and refresh the open flag. That
  update has to happen *before* the already-logged check, which currently skips
  repeats outright to keep the `scan found …` line once per boot per SSID.
- `net scan` prints the cache, loudest first. Sorting ≤64 entries at print time
  is free.

Accumulate-across-scans stays as it is: a network that only appears in a later
scan still lands in the cache, so the environment fills in over time rather than
being whatever the most recent single scan happened to see.

### `sys.build.hw` and `sys.build.dist`

`show` takes a prefix and walks the subtree (`storage.cpp:2729`), so
`show sys.build` returns the whole set in one round trip. Two keys are missing.

**`sys.build.hw`** — the board the image was built for. It exists today only as
free text inside `sys.build.args`
(`spangap build reticulous/reticulous --with spangap/hw-heltecv4`), so reading it
means regexing an invocation string — the problem `-O` exists to avoid. Extract
it once, at build time, in
`spangap-core/esp-idf/scripts/write-build-info.py`: pull `--with spangap/hw-*`
out of `SPANGAP_BUILD_ARGS` and emit it as another symbol. The generator already
reads discrete env vars and emits symbols, so the `spangap build` side needs no
change. The value then matches the catalogue exactly —
`reticulous-builds/builds.yaml` writes its invocations in the same form — so
selection is string equality with no mapping table.

**`sys.build.dist`** — the catalogue entry name, free-format, for the
several-dists-per-board case below. Unlike `hw` this is not derivable from the
invocation; `make-builds.py` knows the entry name and exports it as
`SPANGAP_BUILD_DIST`, matching the existing `SPANGAP_BUILD_DATETIME` pattern.

This is the minimum for onboarding. The wider mess of build-info at several
levels is a separate overhaul.

### `sys.flash.*` — the device publishes its flash geometry

A booted device knows more than detection does: not just the chip size but the
floor of the image actually on it, the `/state` geometry that resulted, and —
implicitly — that this image boots at all.

Everything needed is already computed at mount (`fs.cpp:1050`–`1076`). Publish it
as ephemeral `sys.flash.*`; keys outside the persisted prefixes are in-memory
only and recomputed every boot (`storage.h:19`), which is what these are:

    sys.flash.size          real chip size (SFDP)
    sys.flash.floor         top of the on-flash partition table — the minimum
                            chip size this image needs
    sys.flash.state_start   where /state begins (floor, 4K-aligned)
    sys.flash.state_size    /state size, phys - start

Three details the implementation has to get right:

- **The early return.** `statePartitionEnsure` returns immediately when a board
  pins `state` in its own table (`fs.cpp:1041`), so publishing from inside the
  function would leave those boards with no keys at all. Both paths must publish
  — and on those boards `state` *is* an on-flash entry, so the floor rule has to
  skip it or it yields the chip top rather than the firmware floor.
- **Capture before registration, not just before storage.** It runs at the top of
  `fs_init()`, before the mounts, so capture the values into statics there and
  publish them where the other `sys.*` keys are published, the way
  `publishBuildTimes()` already does. This is not only about storage not being up
  yet: `floor` is computed by iterating partitions *before* `state` is registered
  (`fs.cpp:1051`, then `1079`). `state` is external and in-memory and is never
  written to the on-flash table, so anything that recomputes the floor the same
  way after registration sweeps it up and gets the chip top instead.
- **Degenerate cases must stay visible.** When SFDP fails the code assumes
  `phys = floor` and registers no `/state`; when `start >= phys` it warns and
  registers none either. In both, `state_size` is 0 and `size` equals `floor` —
  which flashmon must not read as a confident chip size. Publish them as they
  are; don't smooth them into a plausible number.

## Catalogue side

### What `sys.build.datetime` is

`YYYYMMDDhhmmss`, computed once per `make-builds.py` run
(`flashmon/flashmon/make-builds.py:154`) and used three ways for that one run:
baked into every image built by it via `SPANGAP_BUILD_DATETIME`, written into
each filename as `<slug>_<name>_<datetime>.zip`, and recorded as each entry's
`version:` in the yaml. So it identifies the catalogue run, not an individual
image — every image from one `make` carries the same stamp, and it is the same
value the filename carries.

That is what the flash offer compares. It is not a build time in any other sense:
`sys.buildtime.app` is spangap's own per-image compile epoch, a different level
entirely and not a substitute.

Empty means the running image did not come from a catalogue run. That is a
distinct state, not a missing value — there is nothing to compare it against, so
flashmon should say so rather than fall back to another number.

### Distribution identity must be free-format, and separate from ordering

Soon there will be several builds per board — mostly differing in what is left
out to fit 4 MB of flash. That makes the distribution identity a free-format
string. It cannot be the same field as the build stamp:

- **Identity** — which distribution this image is. Free-format. `sys.build.hw`
  does not cover it: two dists for one board share a `hw`.
- **Ordering** — whether the catalogue holds something newer. `_refresh_flash`
  decides this with `cv > dv` (`flashmon.py:880`), a string compare that works
  only because both sides are sortable 14-digit stamps. A free-format string has
  no order, so collapsing the two leaves only "different → offer", which offers
  downgrades and pointless re-flashes.

Keep them apart:

- catalogue entry `name:` is the free-format identity (already the right shape —
  currently one entry per board, nothing prevents `heltecv4-minimal` /
  `heltecv4-full`)
- catalogue `version:` stays the sortable run stamp
- the device reports the entry name as `sys.build.dist`, alongside
  `sys.build.datetime`

The offer then reads: same dist, newer stamp.

**First flash cannot auto-resolve.** `build_candidates` (`flashmon.py:207`) walks
`hw-` prefixes to a single most-specific match, which assumes one image per
board. A fresh 4 MB board with three variants has no basis to choose — it has to
ask. Only subsequent flashes can match on the device's reported dist.

### The catalogue must carry the flash floor

The catalogue says nothing about how much flash an image needs, so the only way
to find out an image won't fit is to download it. The tdeck build writes
`fixed.bin` at `0x786000` (`flasher_args.json`) and declares a 16 MB container,
so its floor is around 8 MB — it cannot go near a 4 MB board, and today that
costs a 4.7 MB download to discover.

The number already exists: `CONFIG_SPANGAP_MAX_FIRMWARE_KB`, whose help text
defines it as the flash offset where the runtime `/state` partition begins.
`spangap-core/docs/flash-partitions.md` describes the image as size-agnostic and
bootable on any chip at or above that floor, so the floor *is* the minimum chip
size. `0` means "fill the container", in which case the floor is
`CONFIG_ESPTOOLPY_FLASHSIZE`.

Get it into the yaml:

- Both values are in `<repo>/esp-idf/sdkconfig` after the build.
- `make-builds.py` already sits in exactly the right place — it runs each
  `spangap build`, copies `flasher.zip` out of that repo's build dir, and is the
  only writer of the catalogue.
- Read them **immediately after each entry's build**, next to the existing zip
  copy. Every entry builds in the same repo directory, so `sdkconfig` reflects
  only the most recent build and is overwritten by the next one.
- Record per entry: the floor in KB (resolved, i.e. substituting the container
  size when the config is 0), and the total bytes actually written, available
  from `flasher_args.json` in the zip or from the file sizes.

No `spangap build` change — this is a read of config the build already produced.

## Fit check: three sources, in order

A device can be flashed with firmware that doesn't boot, which is exactly when
you most need to flash something else. So the fit check must not depend on the
device answering.

1. **The device, if it answers** — `show sys.flash`. Authoritative, and the only
   source that also confirms the image on it actually boots.
2. **The on-flash partition table, read directly.** It lives at `0x8000` and is
   3,072 bytes (`flasher_args.json`), so this is a ~3 KB `read_flash` rather than
   a multi-MB download. Parse it host-side with the same rule
   `statePartitionEnsure` uses — `floor = max(address + size)` over the entries —
   and the number is identical to what a booted device would report. The table on
   flash *is* the firmware footprint: `state` is registered in RAM at runtime and
   never written to the table, and `reserved` exists precisely to carry the table
   top up to the floor. (Skip a `state` entry if one is present — only boards
   that pin it in their own table have one.) Chip size comes from esptool's own
   detection, which reads the flash chip and is correct for this purpose; do not
   assume the bootloader header agrees with it, since esptool warns when it
   doesn't and the running firmware works around the same discrepancy in
   `fs.cpp`.
3. **Neither** — blank or unreadable flash. No fit check; offer normally.

Level 2 is what makes a bricked device recoverable: the floor of whatever was
last flashed stays readable even when nothing runs. It is also the fit-check
fallback for firmware too old to speak frames, so it outlives the scraping
fallback rather than being deleted with it.

## flashmon rework

- Reader thread stops parsing device state. `_net()` goes away; it keeps only
  frame extraction and hands everything else to the display.
- State becomes pull-based: a `query(cmd) -> str` over the frame channel, called
  when flashmon actually wants to know something, plus a light poll for the
  things that change (`net` while waiting to join).
- Provisioning (`guided_setup`, and the curses dialog) stops being driven by
  "wait up to 18 s for the right line to appear" and becomes: query state, ask
  the user, send commands, re-query to confirm. The 18/20-second deadlines and
  the `sent`/`need_passwd`/`ap`/`connected` flag soup go with it.
- Provisioning keeps working while the user is typing in the CLI, which it
  cannot do today.
- Parsing collapses to splitting `key=value` lines. No regex table.

## Order

1. Device: frame sniffer, the capability marker line, the console-write mutex,
   and the one-shot exec relay in the serial task.
2. Device: `-O` on `net` and `auth`; `net scan` plus its `-O`; `sys.build.hw` in
   the build-info generator; `sys.flash.*` from the mount-time geometry.
3. `make-builds.py`: `sys.build.dist` export, plus the flash floor and written
   size per catalogue entry. Independent of the rest and useful on its own.
4. flashmon: frame codec, `query()`, and the marker gate — frames only after
   the marker has been seen this session, log scraping otherwise.
5. flashmon: convert build stamp and hostname first (smallest, and hostname is
   already command-shaped), then password state, then WiFi, then the fit check.
6. Delete `_net()` and the boot-log deadlines — only once the oldest image the
   catalogue still offers speaks frames, since until then the fallback needs
   them.

Adjacent, and gated on dists actually existing rather than on this work:
`_refresh_flash` becomes "same dist, newer stamp" instead of a bare `cv > dv`,
and `build_candidates` gains a "which variant?" prompt for a first flash it
cannot resolve. Until a second dist per board is published, both behave exactly
as now.

## Risk

Nothing here can be verified without a device — the frame path, the `-O` output
and the scan cache all need real hardware, and flashing is driven by the user,
not from here. Expect to land it as "builds clean, reviewed against the code
paths listed above", with the first real run being the actual test. The marker
gate and scraping fallback in step 4 are what make that survivable: a device
that never printed the marker is never sent a frame and behaves exactly as it
does today.

## Later

When commands become functions (`cli-overhaul.md`), the RPC handler calls one
directly instead of dialling `cli` — no ITS round trip, no slot, no
prompt-suppression flags. The wire format does not change.
