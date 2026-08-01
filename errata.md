# Errata — known defects and loose ends, not yet fixed

Small things found by inspection that are real but were left alone: each is
either a behaviour change that wants a device test, or cleanup outside the
scope of the work that surfaced it. One section per item, newest first. Delete
a section when it lands.

## LoRaMon records our own air protocol one byte short (iface-lora)

`handleRxDone` passes `pktLen - 1` for **every** rx record, but the tx path
strips the split-framing header only for non-`LORA_PKT_OURS` frames. A 4-byte
sweep frame is therefore recorded as 3 B on receive and 4 B on transmit — the
same frame, two byte counts, depending on which end logged it.

Cosmetic today: the LoRaMon bar width comes from airtime, not from the byte
count, so nothing is mis-drawn. It is a one-line fix, but it changes recorded
telemetry values and wants a device test before it lands.

Documented as-is in `iface-lora/INTERNALS.md` §12, which states the tx-only
rule rather than claiming a symmetry that does not hold.

## LoRaMon zoom may be unreachable behind a collapsed Actmon (iface-lora + spangap-web)

`LoraMonWindow.vue`'s press handler guards on `focusedWindowId`, which assumes
an occluding window can be raised by clicking it — press once to raise, press
again to zoom. `FloatingWindow` pins chromeless windows to `ON_TOP_Z`, and a
collapsed Actmon is chromeless, so it is permanently front-most: every press on
the plot is dropped, not deferred to a second press.

Found by reading spangap-web, not reproduced on device. The fix is a decision
about which layer gives — either the guard learns about always-on-top windows,
or a collapsed Actmon stops claiming `ON_TOP_Z` — so it is not a one-liner.

## demo tracks four generated app icons (demo)

`demo/web-interface/src/app-icons/{cli,gear,log,viewer}.svg` are tracked, but
`stage_web_icons()` in `spangap/build-system/spangap-inside` `rmtree`s and
rebuilds that directory on every build of a buildable, copying in each staged
straddle's `assets/lcd-icons/*.svg`. The tracked copies are build output whose
source of truth lives in the straddles that ship them.

`reticulous`, `iface-lora` and `lxmf` all ignore this path (and
`browser/src/boot/*.gen.ts` alongside it). demo needs the same sweep:
`git rm --cached` the four icons and add the two ignore patterns.
