# LCD web-remote — mirror the on-device screen to a browser (plan)

Status: design / not yet implemented.

## Goal

Give `spangap-lcd`'s physical panel a **remote window in the browser**:
the device streams changed screen regions (dirty rectangles) out, and the
browser sends click/drag input back, driving the *same* LVGL context that
the local panel drives. It is a VNC-style mirror of the real device
screen — not a second, independently-rendered UI (that is what
`spangap-web`'s SPA is). Think "look at and poke the device's actual
launcher from a laptop".

Inspiration is danjulio's `lv_port_esp32_web` (websocket → canvas, input
back), reworked to ride the platform's existing WebRTC transport and a
compression scheme matched to this UI's flat, solid-fill content.

## Transport: WebRTC DataChannel, reliable + ordered

Settled — reuse the existing `webrtc` task, no new transport. Rationale:

- The `webrtc` task is a content-free DC↔ITS router. Adding a data path
  is opening a packet-mode ITS server port on the device and naming a
  DataChannel `"lcd:<port>"` on the browser; the router does
  `itsConnect("lcd", port, protocolBytes)` and bridges bytes, knowing
  nothing about the payload.
- One `RTCPeerConnection` per tab already carries `storage`/`log`/`cli`.
  Opening one more DC costs a few SCTP control messages, not a TLS
  handshake — no DTLS renegotiation, one shared retransmit pool.
- The SDP answer already advertises a **WireGuard host candidate**
  (`s.wg.address` while `wg.up`), so the remote-over-WG case is native.
  Over WG with a large in-flight window, one shared SCTP retransmit pool
  beats a per-TCP-connection retransmit buffer.
- Per-channel reliability: this channel picks **reliable + ordered**.
  A dirty-rect stream has no keyframe-based loss recovery like a video
  codec, so a dropped rect would corrupt the mirror indefinitely; we want
  guaranteed in-order delivery, and we recover from the *one* remaining
  failure mode (backpressure) by tearing the channel down — see below.

Head-of-line blocking is accepted: a stalled keyframe blocks the small
updates queued behind it, but for a UI that is a few ms and invisible,
and far cheaper than a video-style unreliable + damage-tracking design.

## Architecture: its own straddle + task, two small seams on -lcd

The feature is a **new straddle** named `lcdmirror` — plain, not
`spangap-lcdmirror`: the `spangap-` prefix is reserved for the most
central straddles only. It is not folded into `spangap-lcd`, and it is
not "small enough to ride along".

### Why a separate straddle

- **Dependency direction.** `spangap-lcd` is today self-contained — a
  device can ship a panel with no web stack at all. Folding the mirror in
  would make -lcd drag in `webrtc` and a `browser/` half, bloating the
  minimal panel-only build for a feature most panels won't use. Keep the
  dependency one-way: `lcdmirror` *requires* `spangap-lcd` + `webrtc`,
  never the reverse — the direction every other consumer already points.
- **Presence is the on/off switch.** A separate straddle compiles away
  under `CONFIG_STRADDLE_*` when not staged, giving a clean
  `--no-lcdmirror` / headless / panel-only build for free.
- **It isn't small.** RLE encoder (2D + literal fallback), the
  keyframe / backpressure / drop-the-DC state machine, rect coalescing,
  coordinate mapping, plus a browser canvas app. That is straddle-sized.

### Why its own task

Compression must **not** run on the lcd task. `flushCb` is already the
render bottleneck and is synchronous: it byte-swaps in place, takes the
SPI bus lock that is *shared with LoRa on SPI2*, queues DMA, and blocks
on the DMA-done semaphore before unlocking. Running deflate/RLE there
would stall rendering and, via that shared lock, LoRa TX.

Split:

- **lcd task** (`flushCb`): copy the pre-swap rect into a queue. Cheap
  memcpy, nothing else.
- **lcdmirror task**: drain the queue, RLE-encode, push to the ITS port,
  run the backpressure logic.

### The two seams -lcd must grow

The capture lives *below* -lcd's public plug-in API. `lcdInstall` /
`lcdRegisterSettings` paint tiles and panes; they cannot reach the draw
buffer in `flushCb` or the pointer indev. Only -lcd can. So -lcd source
gains two small, generic, transport-agnostic primitives (they know
nothing about RLE, ITS, or WebRTC — capturing the framebuffer and
injecting a pointer are display plumbing, which -lcd owns):

- `lcdMirrorAttach(sink_cb)` — `flushCb` gains a
  `if (s_sink) s_sink(area, px);` call **before** the in-place
  `lv_draw_sw_rgb565_swap`. One null-check per flush when nobody is
  attached (negligible), and it hands over the little-endian pixels,
  which is what the browser wants (see byte order below).
- `lcdMirrorInjectPointer(x, y, state)` — feeds the existing pointer
  indev (`s_ptrIndev`, already present with a drawn cursor and a
  hide-on-inactivity timer, distinct from the GT911 touch indev).

The `lcdmirror` straddle's `esp-idf/lcd/` slice calls `lcdMirrorAttach()`
from its generated `<prefix>LcdInit()` — so the *wiring* uses the
standard mechanism every straddle uses; only the two capture primitives
are a genuine new seam. Any operator UI (a Settings toggle, an "N viewers
connected" line) also rides the normal `esp-idf/lcd/` slice +
`lcdRegisterSettings`.

## Capture path

The panel runs `LV_DISPLAY_RENDER_MODE_PARTIAL`, so `flushCb(disp, area,
px)` fires once per invalidated sub-area — `area` *is* the dirty
rectangle and `px` *is* its pixels. No digging into `disp->inv_areas[]`.

Two traps, both on the swap line:

1. **Byte order.** `lv_draw_sw_rgb565_swap(px, w*h)` mutates the buffer
   in place to big-endian for the ST7789. Tap **before** the swap: the
   browser converts to RGBA for the canvas anyway, so it wants the
   little-endian pixels. Tapping after yields mangled colors.
2. **Buffer lifetime.** `px` is LVGL's draw buffer and is reused after
   `lv_display_flush_ready`. The sink must **copy** the rect (into the
   queue) before returning; it cannot hold a reference to `px`.

The per-rect header carries **pixel format + geometry**, not just the
bbox: `{x, y, w, h, format, flags}`. `format` because the device is
RGB565 and the canvas is RGBA8888 — negotiate/convert explicitly. One
`flags` bit marks a **full-frame** packet so keyframes are
self-describing.

Because the ITS port is packet-mode ("one SCTP DATA message = one
`itsSend`"), message framing is free — no length prefixes; the packet
boundary is the frame boundary. Header (small, uncompressed) then the
compressed body.

## Compression: 2D RLE with a literal fallback

The shipped theme is flat solid dark fills (`LV_OPA_COVER` backgrounds,
no wallpaper, no gradients), which is near-best-case for RLE — the entire
cost concentrates on **anti-aliased icon and text area**; everything else
is nearly free.

Design points:

- **Literal-run opcode is mandatory** (PackBits-style: alternate
  run/literal). AA edges produce a distinct RGB565 value nearly every
  pixel → run length 1 → 3–4 bytes to encode 2 bytes of pixel. Without a
  literal fallback, an AA-heavy region can exceed raw. With it, noisy
  spans degrade to raw + small overhead.
- **Horizontal RLE + literals gets ~90% of the win.** The "2D" part
  (identical-adjacent-row collapse) mainly compresses the big flat
  background, which is already ~1 run/row and a small slice of the byte
  budget since AA icons/text dominate. Add row-repeat only if cheap;
  don't over-build for it.
- **The estimates are contingent on the flat theme.** A photographic
  wallpaper pushes the launcher toward raw. Note this so nobody "improves"
  the theme into a bandwidth cliff.
- Client-side decode is trivial JS. (If we ever wanted plain gzip instead,
  browsers decode it natively via `DecompressionStream('gzip')` at zero
  library cost — but RLE is ~10× cheaper to *encode* on a 240 MHz Xtensa
  and matches this content better. Profile RLE vs deflate vs raw before
  finalizing.)

### Size estimates (320×240 RGB565, raw = 150 KB)

Whole-screen **keyframe** after 2D RLE, runs ≈ 3–4 B each:

| Screen                | RLE'd     | % raw   | why                                    |
|-----------------------|-----------|---------|----------------------------------------|
| Settings / list panes | 4–8 KB    | 3–5 %   | solid rows collapse; only text costs   |
| Launcher (icon grid)  | 15–25 KB  | 10–16 % | ~12–20 AA icons + labels dominate      |
| Full terminal of text | 10–20 KB  | 7–13 %  | AA monospace glyphs; ~0 when blank     |

Typical ≈ **15 KB (~10 % of raw)**; worst realistic ≈ **25–30 KB**.
Steady-state dirty rects are tiny (a clock digit is a few hundred bytes);
the keyframe is a once-per-connect cost.

## Keyframe, resync, and the one failure mode

The keyframe lives in the ITS `onConnect` handler: every new ITS handle
gets a full frame first (header `flags` = full-frame). This covers every
*connection-identity* case, because each produces a fresh handle:

- **First connect** — obvious.
- **DTLS death / reconnect** — DTLS dies → SCTP dies → every DC dies →
  browser reopens → new handle → keyframe.
- **Takeover** (`?force=1` KICK) — the device runs one DTLS + one SCTP
  association at a time, so admitting the forced session tears the old one
  down and handshakes anew → new DCs → new handle → keyframe. The victim's
  channels are already dead.
- **Backgrounded tab** — reliable+ordered buffers and replays in order,
  the canvas pixels persist, so on resume the client drains the backlog
  onto a still-correct canvas. If the browser actually dropped the
  connection, that is a reconnect = new handle = keyframe.
- **Resize / zoom** — pure client-side: keep a backing canvas at native
  320×240 and CSS-scale it (window kept proportional; last size in
  `localStorage`). The device is not involved.

So there is **no "refresh" opcode and no CRC** for connection events —
`onConnect` covers them all.

### The one remaining failure mode: backpressure → drop the DC

Reliable+ordered gives no lossy escape hatch. If a slow or
suspended-but-still-connected client lets the device's `fromSize` send
buffer fill, the device must not **block** (a wedged remote viewer would
freeze the *physical* panel — unacceptable). So under sustained overflow
the device **tears the channel down**: `itsDisconnect` on the lcd handle,
which the router maps to an SCTP stream reset. The browser sees
`datachannel.onclose`, shows "reconnecting", reopens `"lcd:N"` (a few
SCTP control messages, no DTLS renegotiation), and `onConnect` sends a
fresh keyframe. The stream reset discards the send queue, so there is no
stale-rect replay — clean slate every time. Blast radius is only the
mirror; `storage`/`log`/`cli` survive on the same PeerConnection.

This needs **zero new protocol** — it reuses the existing disconnect
path. The DC-drop *is* the resync signal, unified with the reconnect path
that already exists.

**The trap to design against: keyframe-amplified reconnect loop.** The
keyframe is the single largest payload. If a client genuinely cannot
carry the steady delta stream (2G over WG), naive drop-and-reconnect
answers "link too slow" by sending the *heaviest* thing, forever. Guard
with a **congestion gradient**, so drop-the-DC is the backstop, not the
primary mechanism:

1. **Watch `fromSize` occupancy as the congestion signal** and degrade
   *before* the hard cliff: coalesce overlapping dirty rects, keep only
   the latest state per region (drop superseded intermediate rects for
   the same area), cap update Hz. A transient 200 ms stall never reaches a
   drop.
2. **Backoff on the client reopen** so a truly under-provisioned link
   spins slowly rather than hot-looping keyframes.

## Input injection

One LVGL pointer; single session enforced by the `webrtc` signaling WS
(one active session, BUSY/takeover), so no multi-pointer contention.
Remote click/drag maps onto the existing `s_ptrIndev` pointer path (not
the touch driver): map canvas coords → panel coords (÷ zoom factor ×
`devicePixelRatio`), and push press/move/release through
`lcdMirrorInjectPointer` into a queue the indev `read_cb` drains. Local
touch and remote pointer both drive the same UI — which is the point; it
*is* the device's screen.

## Browser half

- A `<canvas>` backing store at native 320×240; CSS-scale for zoom, window
  kept proportional to panel aspect, last size in `localStorage`.
- DataChannel `"lcd:N"`, reliable+ordered; decode header, RLE-decode body,
  RGB565→RGBA, blit the rect at `(x,y)`.
- On full-frame flag, repaint whole canvas; on `onclose`, show
  "reconnecting" over the last good frame and reopen with backoff.
- Capture pointer events, map to panel coords, send back over the same DC.
- **App icon:** the standard macOS-style **display-mirror** glyph — two
  overlapping rounded-rectangle screens (the AirPlay/Screen-Mirroring
  motif) — so the affordance reads instantly as "this is the device's
  screen, mirrored here". Inline SVG, self-contained (no external asset).

## Sizing / config notes

- **`fromSize`** (per-DC send buffer): size to swallow the AA-heavy
  keyframe worst case without truncating — ~**48–64 KB** (PSRAM is cheap
  here), not the typical ~15 KB.
- The ITS server port: packet-mode, small `maxHandles` (single session),
  `toSize` sized for input packets (tiny).

## Phasing

1. -lcd seams: `lcdMirrorAttach` + `flushCb` tap (pre-swap copy) and
   `lcdMirrorInjectPointer`. Verify the null-check cost is nil when
   unattached.
2. `lcdmirror` straddle skeleton: ITS port, its own task, raw-RGB565
   passthrough (no compression) + keyframe-on-connect. Prove the pixels
   land correctly in a canvas over WebRTC.
3. RLE encoder (horizontal + literal fallback), then 2D row-repeat.
   Profile vs raw and vs deflate.
4. Backpressure gradient (coalesce / latest-per-region / Hz cap) and
   drop-the-DC backstop with client backoff.
5. Input path end to end.
6. Settings toggle + viewer-count surface via the normal lcd slice.

## Open questions

- Exact RLE opcode encoding (run/literal marker, max run length, whether
  runs are byte- or pixel-counted) — settle empirically in phase 3.
- Whether to also expose a whole-frame CRC for the client to self-detect
  desync, or rely purely on the backpressure→drop path (leaning: rely on
  the drop path; add CRC only if a silent-corruption case shows up).
- Coalescing policy granularity: per-rect supersession vs a per-region
  damage accumulator flushed at a capped rate.
