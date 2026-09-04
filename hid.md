# A Bluetooth keyboard and trackpad: HID-over-GATT as a spangap-ble consumer

## Status — analysed 2026-09-04; nothing built

What it would take to pair a foldable Bluetooth keyboard/trackpad combo and
have it drive the on-device UI. The radio side is largely in place; the HID
side does not exist at all, and the input plumbing needs one real change.

## The flow

```
device                                   the combo (peripheral)
  │ scan, filter on 16-bit UUID 0x1812  ← ADV_IND (HID service)
  │ bleConnect(addr)                    →
  │                                     ← connected, ATT MTU exchange
  │ discover 0x1812 svc                 →  Report Map 0x2A4B, Report 0x2A4D ×N
  │                                        + Report Reference 0x2908 on each,
  │                                        Protocol Mode 0x2A4E, Battery 0x180F
  │ write CCCD on each input report     →
  │                                     ← ATT error 0x05 INSUFFICIENT_AUTHENTICATION
  │ ble_gap_security_initiate           →  pairing; the keyboard is KeyboardOnly
  │ (passkey shown on our panel)        ← typed on the keyboard, Enter
  │ bonded, link encrypted; retry CCCDs →
  │                                     ← notify: keyboard reports (8-byte boot,
  │                                        or report-ID multiplexed) and mouse
  │ usage→LVGL key, rollover diff,      →  lcdInputGroup()
  │ host-side typematic repeat
  │ dx/dy integrated → cursor, wheel    →  the pointer indev / lcdScroll()
```

## What already exists

**spangap-ble** carries the central role (`CONFIG_BT_NIMBLE_ROLE_CENTRAL=y` in
its `straddle.yaml`), `bleConnect()` (`esp-idf/include/ble.h:243`), a shared
duty-cycled scanner, `bleOnNotifyRx`, a bond store in `/state/ble/bonds.bin`, a
connection budget, and the sanctioned pattern of a consumer calling
`ble_gattc_*` itself. `iface-ble/esp-idf/src/ble_gattc.cpp` (315 lines) is the
working template for GATT discovery against a peer this device dialled.

**spangap-lcd** already treats a keyboard as a consumer's business: "the
hardware keyboard, if any, is a CONSUMER concern … the consumer creates its own
keypad indev, joins it to `lcdInputGroup()`, and drives it via `lcdRun()`"
(`esp-idf/src/lcd_ui/lcd_lvgl.cpp:701`), with the T-Deck's I2C keyboard as the
worked example (`hw-lilygo-tdeck/esp-idf/conditional/spangap-lcd/src/tdeck_lcd.cpp:863`).
`lcdSetHasKeyboard()` is a live bool read at draw time, so it can flip when a
keyboard connects and again when it drops.

## What is missing

### In spangap-ble (~150 lines, plus verification)

- **The scanner matches 128-bit UUIDs only** — `advMatches()`
  (`esp-idf/src/ble_gap.cpp:263`) and the `uuid128[16]` filter in
  `ble_scan_req_t` (`ble.h:219`). HID is the 16-bit `0x1812`; the request
  struct needs a `uuid16` field and the matcher a second arm.
- **One global security policy**: `BLE_HS_IO_NO_INPUT_OUTPUT`, `sm_mitm = 0`
  (`esp-idf/src/ble_sec.cpp:40`). Just Works is enough for a fair number of
  combos, but a keyboard advertising KeyboardOnly wants Passkey Entry, and that
  means `BLE_HS_IO_DISPLAY_ONLY` + `sm_mitm = 1` + a
  `BLE_GAP_EVENT_PASSKEY_ACTION` handler and a panel dialog showing six digits.
  Since the policy is one value for the whole host, that either changes how
  phones pair too or is flipped for the duration of the pairing window — which
  already serialises pairing, so the flip is defensible, but it is a change to a
  straddle that deliberately states one policy.
- **Nothing initiates security.** No call to `ble_gap_security_initiate()`
  exists; and `sm_bonding` is gated on the pairing window, so it is worth
  checking that an encryption *restart* from a stored LTK on reconnect is not
  caught by the same gate.
- **Peer address resolution is unproven.** Keyboards rotate their address. The
  build runs host-based privacy (`spangap-ble/esp-idf/project_include.cmake`),
  so resolving a bonded peer's advertisement against its stored IRK happens in
  `ble_hs_resolv` — needs proving on hardware, because if it does not resolve, a
  bonded keyboard is never recognised in a scan and reconnect never happens. The
  safer design sidesteps it: do not drop the link. Hold it with high slave
  latency (~30) and a 4 s supervision timeout via `bleConnParams()`, and let the
  keyboard sleep on a live connection.
- **Budget**: `CONFIG_BT_NIMBLE_MAX_CONNECTIONS=4`. A keyboard holds one
  permanently, so the mesh loses a peer slot on the same build.

### In spangap-lcd (~100 lines)

- **There is no runtime pointer device.** The pointer indev, the cursor object,
  its auto-hide and its glide are created at init and only if the board
  registered `pointer_read` (`lcd_input.h`; `lcd_lvgl.cpp:872`), and
  `lcdSetInput()` must be called before `spangapInit()`. The mirror pointer path
  deliberately draws no cursor — "No cursor — the remote pointer is drawn by the
  viewer's own browser" (`lcd_lvgl.cpp:85`). A trackpad arriving at runtime needs
  an `lcdPointerAttach(read_fn)` that builds that same indev and cursor lazily.
- **The injection API is the viewer's.** `lcdMirrorInjectKey` /
  `lcdMirrorInjectPointer` are named for the browser mirror and deliberately
  skip the inactivity timer; typing must poke it. Either generalise the pair
  (the viewer keeps its no-poke behaviour as a flag) or have the HID straddle own
  its indevs and call `lcdNotifyActivity()` itself — the latter is what the
  T-Deck keyboard does and needs no lcd change beyond the pointer.
- **Trackball policies live in board code.** Arrow-key mode
  (`lcdScrollwheelArrowsActive`), caret drive, edge-pan `lcdScroll()` are all
  implemented in `tdeck_lcd.cpp`. A trackpad wants the same behaviours, so they
  are either reimplemented in the new straddle or lifted into the component.

### A new consumer straddle (the bulk, ~1,200–1,800 lines)

spangap-ble's doctrine is that it knows nothing about what rides the link, so
HID-over-GATT belongs in a consumer — `hid-ble`, conditional on
`CONFIG_SPANGAP_LCD`:

- GATT client: discovery, Report Reference descriptors, CCCD subscribes with the
  authentication-retry loop (~400).
- Report decode. **This is the fork in the road.** Boot protocol (write Protocol
  Mode = 0, subscribe `0x2A22`/`0x2A33`) needs no descriptor parser at all —
  fixed 8-byte keyboard, 3–4 byte mouse, and the combo's own firmware has
  already turned two-finger scroll into wheel bytes. Report protocol needs a HID
  report-descriptor parser (usage pages, report IDs, bit offsets) at +400 lines,
  and if the touchpad is a precision digitizer it also means gesture recognition
  over absolute contacts. Start boot-only; add the parser for the devices that
  refuse boot mode.
- Usage→LVGL key translation: a US layout table, shift/AltGr, 6-key rollover
  diffing, and **host-side typematic repeat** — HOGP sends no repeats, so a timer
  owns that (~250). `LCD_KEY_CTRL` (`lcd_input.h`) only encodes Ctrl+letter, so
  other combinations need an encoding extension if the terminal should see them.
- Pointer integration: dx/dy accumulation, acceleration, clamp to
  `lcdDisplaySize()`, buttons, wheel→`lcdScroll()` (~150).
- Settings pane (scan, pair, forget, battery, layout), CLI verbs, and the chosen
  device persisted under `s.ble.hid.*` — the one Bluetooth namespace every
  consumer keeps its keys in (~300).

## The tail nobody estimates

Per-device quirks. Combos differ on boot-mode support, on whether they demand
MITM, on report-ID layout, and on whether they will reconnect to a host that is
not advertising. Budget a couple of real devices and a week of chasing them.

## The standing cost

Bluetooth up means no light sleep on these boards — no external 32.768 kHz
crystal, so the controller's own power locks hold the system awake whether idle
or connected (`spangap-ble/README.md`, "Power and light sleep"). A permanently
attached keyboard means the radio never stops. This is a plugged-in or
large-battery feature, not a backpack one.

## Open questions

- Whether host-based privacy resolves a bonded peer's rotating address in a scan
  on this target. The answer decides whether reconnect-by-scan is available at
  all, or whether the link must simply be held open with slave latency.
- Whether flipping the IO capability for the duration of the pairing window is
  acceptable in a straddle that states one security policy, or whether the
  passkey path wants a per-connection policy that NimBLE does not offer.
- Whether the trackball policies (arrow mode, caret drive, edge pan) move into
  spangap-lcd when a second consumer wants them, or stay duplicated.
