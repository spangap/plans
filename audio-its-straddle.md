# `spangap/audio` — generic I2S audio + ITS packet server

A new **generic** straddle that drives audio in/out over I2S and stands up an ITS
packet server. Any peer that connects to the server gets the live microphone as
seccam-compatible `[av_hdr][payload]` packets; anything peers send to the port is
mixed together and played out the speaker (timestamps ignored). Also exposes a
local function to play WAV files at the device sample rate.

Target build for v1: `reticulous/reticulous --with spangap/hw-tdeck` (LilyGo
T-Deck Plus: ES7210 mic + MAX98357A speaker). Written board-agnostic so other
boards contribute only data + a tiny codec shim.

This plan is self-contained: a fresh context should be able to build both halves
from it without the originating conversation. All file:line references were
verified against `/straddles` on 2026-06-22.

---

## 1. Goal & confirmed decisions

**What it does**
- One **audio device**: single sample rate, single block size, both in `s.audio.*`.
- **Mic → network**: every connected ITS client receives fixed-size blocks of mic
  audio, each framed `[av_hdr_t (12B)][payload]`, header byte-identical to seccam's
  live DataChannel format (so existing seccam-side decoders work unchanged).
- **Network → speaker**: all audio any client sends to the port is decoded and
  **mixed** (sum + saturate) into the speaker output. Header optional on inbound;
  **timestamps are ignored** on playback.
- **WAV playback**: `audioPlayWav(path)` streams a WAV from the filesystem into the
  same output mixer.

**Confirmed simplifications (from design discussion)**
- **One rate, one block size, global.** `s.audio.rate` + `s.audio.block_ms`. Capture,
  streaming, mixing and playback all operate in `block_ms` units at `rate`. Every
  consumer is aligned to that block cadence.
- **No integer-fraction decimation.** If a consumer wants a lower rate, that is its
  own downstream concern. The connect payload carries only a codec selector.
- **No resampler in v1.** `audioPlayWav` requires the file already be at the device
  rate; mismatched files are rejected. Inbound network audio is assumed at device
  rate (decoded per its codec byte, not resampled).
- **Full-duplex confirmed feasible** (see §4) — simultaneous mic capture + speaker
  playback on two independent I2S controllers.
- **Block size default = 20 ms** (seccam's RTP path used 20 ms; divides evenly at all
  common rates: 16 k→320, 48 k→960 samples). Sane range 10–40 ms. Seccam captured at
  10 ms and re-chunked per consumer; we collapse to one global block.

**Deferred (not v1)**
- RNS/mesh reachability (would need an `rnsdDestOpen` bridge). DataChannel + TCP only.
- A browser UI panel (mic monitor / push-to-talk). The DC port works without it.
- Arbitrary-rate resampling.

---

## 2. Two-straddle split & responsibility boundary

The key architectural call: **the generic straddle owns the I2S engine.** Putting
I2S read/write in each board straddle would duplicate the channel/DMA/loop code
across every audio board (MAX98357A, PCM5102A, ES7210 are all plain I2S). The board
contributes only what is genuinely board-specific.

```
┌──────────────── spangap/audio (generic, prefix "audio") ─────────────────┐
│  I2S engine (i2s_std + i2s_pdm, pins from Kconfig)                        │
│  ITS packet server + per-client fan-out                                  │
│  raw mic ring (PSRAM)   output mixer (sum+saturate)                       │
│  codecs: µ-law / PCM16 / IMA-ADPCM  (encode AND decode)                  │
│  av_hdr framing (lifted from seccam)   WAV parse + streamed playback      │
│  storage vars / CLI / Kconfig                                            │
│                                                                          │
│  needs from a board:                                                     │
│    • CONFIG_AUDIO_* pin/mode values  (data)                              │
│    • OPTIONAL: audioRegisterCodec(ops) for a non-I2S control plane       │
└──────────────────────────────────▲───────────────────────────────────────┘
                                    │ when: spangap/audio
┌──────────────── spangap/hw-tdeck (board, prefix "tdeck") ─────────────┐
│  kconfig:  CONFIG_AUDIO_* pins + IN_MODE + rate default  (data only)      │
│  esp-idf/conditional/spangap-audio/tdeck_audio.cpp:                       │
│       ES7210 I2C bring-up → audioRegisterCodec(&es7210_ops)  (~50 lines)  │
│  straddle.yaml: init hook `tdeckAudioInit` gated `when: spangap/audio`    │
└──────────────────────────────────────────────────────────────────────────┘
```

**Why this boundary is correct against the hardware matrix:**

| Part | Board contribution |
|---|---|
| MAX98357A (speaker, I2S) | Kconfig pins only — no code |
| PCM5102A (DAC, I2S) | Kconfig pins only — no code |
| PDM mic (XIAO/seccam) | Kconfig pins + `IN_MODE=pdm` — no code |
| ES7210 (T-Deck mic, I2S + I2C ctrl) | Kconfig pins + ~50-line `audio_codec_ops_t` slice |
| A7682E cellular voice | not I2S at all → advertises no I2S capability; out of scope |

So almost every audio board is pure Kconfig data; only an actively-configured codec
(ES7210) adds a shim. The I2S engine exists exactly once.

---

## 3. Codec-ops HAL (the only board↔generic interface)

Defined in `spangap/audio`'s public header; the board *optionally* registers an
implementation. Covers exactly the non-I2S control plane (I2C/AT register
programming). I2S pins/mode are NOT in this struct — they come from Kconfig.

```c
/* audio.h (spangap/audio) */
struct audio_codec_ops_t {
  /* Program the input codec over its control bus (I2C/SPI/AT) for `rate`.
   * Called by the audio task immediately BEFORE it enables I2S RX, and again
   * if the rate changes. Return the rate the codec actually clocked (may be
   * clamped to a supported value); 0 on failure. No-op codecs return `rate`. */
  uint32_t (*in_init)(uint32_t rate);
  void     (*in_deinit)(void);
  /* Optional output codec control (most amps need none → leave null). */
  uint32_t (*out_init)(uint32_t rate);
  void     (*out_deinit)(void);
};

/* Board calls this from a `when: spangap/audio` init hook. Stores the pointer;
 * the audio task applies it lazily on first capture (so hook ordering between
 * the board init and audioInit doesn't matter). If never called, the engine
 * runs raw I2S with no codec programming (correct for MAX98357A/PCM5102A/PDM). */
void audioRegisterCodec(const audio_codec_ops_t* ops);
```

**Ordering note:** `spangap/audio` does not `require:` the board and the board gates
its hook with `when:` (no `requires:` either), so the relative order of `audioInit`
and the board's `tdeckAudioInit` in the generated dispatcher is not guaranteed.
This is fine because the audio task starts I2S **lazily** (only when a client
connects or playback is requested — see §5), long after all init hooks have run.
`audioRegisterCodec` just stows the pointer in a global.

---

## 4. Full-duplex analysis (T-Deck) — RESOLVED: feasible, no conflict

Findings (verified):
- **ESP32-S3 has two independent I2S controllers** — `SOC_I2S_NUM = 2`
  (`/opt/esp/idf/components/soc/esp32s3/include/soc/soc_caps.h:228`). Mic on one,
  speaker on the other; they clock independently.
- **The two pin sets are fully disjoint** (`/straddles/hw-tdeck/docs/tdeck.md:285-298`):
  - ES7210 mic: MCLK `48`, SCK/BCLK `47`, LRCK/WS `21`, DIN/SDOUT `14`.
  - MAX98357A speaker: BCK `7`, WS `5`, DOUT `6`.
- **The "MCLK shared with ES7210 LRCK at GPIO 21" warning in the docs is a
  non-issue:** the MAX98357A is a self-clocking Class-D amp with **no MCLK input** —
  it derives everything from BCLK+LRCLK. GPIO 21 is purely the ES7210's word-select;
  the amp never touches it. Nothing contends.
- **LilyGo's factory "mutually exclusive" behaviour is a software choice, not
  silicon.** Two separate controllers on disjoint pins run concurrently by design.

**Concrete I2S assignment** (do NOT use same-controller full-duplex pairing — that
forces a shared BCLK/WS the two parts don't share):

```
I2S0  → RX only  → ES7210     pins {MCLK 48, BCLK 47, WS 21, DIN 14}, master, rate
I2S1  → TX only  → MAX98357A  pins {BCK 7, WS 5, DOUT 6},            master, rate
```

Both master, same `rate`, same bit width (16-bit). Capture and playback loops run
concurrently on the one `audio` task. **Bench verification step:** bring both up and
confirm clean simultaneous capture + playback in `spangap log` (no clock glitching).
Expected to pass; the doc warning was about a pin not actually wired to the amp.

(Note: T-Deck mic is an I2C-configured **I2S codec** (ES7210, `i2s_std`), unlike
seccam's XIAO mic which was a **PDM** mic (`i2s_pdm`) needing no control bus. The
engine supports both modes; `CONFIG_AUDIO_IN_MODE_*` selects.)

---

## 5. The `audio` task (single task, core 1)

One FreeRTOS task named **`audio`** owns the ITS server *and* both I2S channels, so
ITS handles are never sent cross-task and capture/playback stay phase-locked. The
task name `audio` is also what makes the DataChannel label `audio:1` route (§8).

**Idle/active gating (mirror seccam audio.cpp:474-540):** I2S is APB-clocked and
gated in light sleep, so the task holds a `PM_NO_LIGHT_SLEEP` lock only while active,
and idles on `itsPoll()` when there's nothing to do. The two directions are gated
independently:
- **RX (mic) active** when client count > 0 (a connected client implicitly wants
  the mic — "anyone who connects gets the mic").
- **TX (speaker) active** when there's pending playback (inbound mix non-empty OR a
  WAV stream active).

**Main loop (active), one iteration per `block_ms`:**

```
loop:
  if RX active:
      i2s0 read one block  ──► raw PCM16 ring
      for each connected client slot:
          encode block (slot.codec) into [av_hdr][payload]
          itsSend(slot.handle, pkt, len, 0)        // non-blocking; drop on backpressure
  drain inbound + playback:
      while (itsPoll(0)) {}                          // fires onRecv → decode → push to mixer
      pull one block from WAV streamer(s) if any → push to mixer
  if TX active:
      mix accumulator (sum + saturate, apply out_gain)
      i2s1 write one block
  else:
      itsPoll(blockTicks)                            // block until next event/timeout
```

- **Fan-out** is the log.cpp pattern: per-client slot array holds the ITS handle;
  guard with `itsConnected(h)` then non-blocking `itsSend(h, …, 0)`
  (`/straddles/spangap-core/esp-idf/src/log.cpp` drain loop ~line 283).
- **av_hdr** per outbound block: `type=AV_TYPE_AUDIO`, `codec=slot.codec`,
  `epoch_ms`/`utc_offset_min` of the block's first sample (`gettimeofday` +
  `utcOffsetMinutes`, same as seccam audio.cpp:511-515).
- **Mixer:** an `int32_t` accumulator over one block; each source (every client's
  decoded inbound + every active WAV) is summed in, then clamped to int16 with
  `out_gain` (8.8 fixed) applied. Empty accumulator → silence (or skip TX).
- **WAV streaming** is pre-buffered: a small PSRAM ring is filled ahead by `fs_*`
  reads so flash I/O never stalls the audio loop and never runs LittleFS off a PSRAM
  stack (platform hazard — always use `fs_*`, never raw LittleFS).

**Block math:** `samplesPerBlock = rate * block_ms / 1000`. PCM16 ⇒ `2 * samplesPerBlock`
bytes raw; encoded payload size depends on codec (§7).

---

## 6. Rate & block model (nothing is negotiated)

- One device rate `s.audio.rate`; mic-in and speaker-out both run at it.
- On first capture the codec shim's `in_init(rate)` may **clamp** to the nearest rate
  it can clock; the resolved value is published read-only as `audio.sample_rate`.
  That is the only adjustment — a hardware fact, not a peer negotiation. No
  connector, first or otherwise, sets the rate.
- One block size `s.audio.block_ms`. Capture, fan-out, mix and WAV all use it.
  Inbound packets that don't align to a block boundary are buffered per-connection
  until a full block accumulates, then mixed.

---

## 7. Codecs & wire format (reuse seccam, ADD decoders)

**Lift verbatim from seccam** `/straddles/seccam/esp-idf/main/`:
- `av_hdr.h` → `spangap/audio/esp-idf/av_hdr.h` (12-byte packed header, `static_assert`
  on size; `avHdrFill`). Keep identical for seccam compatibility.
- From `audio.cpp`: `linearToUlaw` (185-198), IMA-ADPCM tables + `encodeAdpcm`
  (200-320), `encodePcm16`/`encodeUlaw`, and the format helpers `audioOutputRate`,
  `audioOutputBits`, `audioWavFormat`, `audioBlockAlign`, `audioRawSamplesForBytes`,
  `audioBytesForMs`, `audioMsForBytes`, `audioCodecFromConfigString` (605-634).
- The `audio_codec_t` enum (`audio.h:22-27`): `AUDIO_PCM16, AUDIO_ULAW_8K,
  AUDIO_ULAW_16K, AUDIO_ADPCM`.

**NEW — decoders** (seccam only encoded mic→wire; we also need wire→PCM for inbound +
WAV playback):
- `ulawToLinear(uint8_t) -> int16_t` (standard G.711 µ-law expand).
- IMA-ADPCM decode (nibble → PCM16, using the same `imaStepTable`/`imaIndexTable`,
  per-stream predictor/step state).
- PCM16 passthrough.

**Wire format, both directions:** `[av_hdr_t (12B)][payload]`.
- Outbound (device→client): `type=1`, `codec=<client's codec>`, real timestamps.
- Inbound (client→device): header is OPTIONAL. If the first 12 bytes parse as a
  valid `av_hdr` (`type==AV_TYPE_AUDIO`, plausible codec), strip it and decode the
  payload per its codec byte; otherwise treat the whole packet as raw PCM16 at the
  device rate. Timestamps ignored either way.

---

## 8. ITS packet server & network reachability

**One server port, full-duplex per connection.**

```c
static constexpr uint16_t AUDIO_STREAM_PORT = 1;   /* DC label "audio:1" */
```

Setup in the audio task:
```c
itsServerInit();
itsServerPortOpen(AUDIO_STREAM_PORT, ITS_PACKET,
                  /*maxHandles=*/CONFIG_AUDIO_MAX_CLIENTS,
                  /*toCap=*/<≈4 blocks>, /*fromCap=*/<≈4 blocks>,
                  /*depth=*/0, /*maxMsg=*/<1 block + hdr>);
itsServerOnConnect(AUDIO_STREAM_PORT,    onConnect);
itsServerOnRecv(AUDIO_STREAM_PORT,       onRecv);
itsServerOnDisconnect(AUDIO_STREAM_PORT, onDisconnect);
```

Callbacks (signatures from `/straddles/spangap-core/esp-idf/include/its.h:83-97`):
```c
struct audio_connect_t { uint8_t codec; };   /* optional connect payload */

// return >=0 (slot index) to accept → becomes serverRef; <0 to reject
static int onConnect(int handle, const void* data, size_t len) {
    int s = allocSlot();                      // per-client state array
    if (s < 0) return -1;                     // full
    slots[s].handle = handle;
    slots[s].codec  = (data && len >= 1) ? ((const audio_connect_t*)data)->codec
                                         : defaultCodecFromConfig();   // s.audio.codec
    // reset per-stream encoder state (adpcm predictor, hpf, agc)
    return s;
}
static void onRecv(int handle, size_t /*avail*/) {
    uint8_t buf[<maxMsg>];
    size_t n = itsRecv(handle, buf, sizeof(buf), 0);   // one packet body
    if (n) pushInbound(handle, buf, n);                // strip optional hdr, decode, buffer→mixer
}
static void onDisconnect(int ref) { if (ref>=0) freeSlot(ref); }
```

Fan-out (in the task loop): iterate `slots`, `itsConnected(h)` then
`itsSend(h, pkt, len, 0)`. `itsServerActive(AUDIO_STREAM_PORT)` gives the live client
count for `audio.clients` and RX gating.

### Reachability per transport

| Transport | What the client does | Device-side requirement |
|---|---|---|
| **Browser (WebRTC DC)** | open a DataChannel labelled **`audio:1`** | **automatic** once the port is open — the web straddle parses `task:port` and `itsConnect`s. No code. (`/straddles/spangap-web/esp-idf/src/webrtc_task.cpp:702-754`) |
| **TCP / LAN** | connect `<ip>:<port>`, speak `[av_hdr][payload]` | register one endpoint with net (below); user sets `s.net.audio_port=<port>` to open the listener |
| **RNS / mesh** | — | **deferred**: needs `rnsdDestOpen("audio.stream", …)` bridging to the ITS port |

**TCP registration** (model on `/straddles/sshd/esp-idf/src/sshd.cpp:90-110`,
struct `/straddles/spangap-net/esp-idf/include/net.h:100-113`). NOTE: TCP is
**stream-mode**, so the device must frame packets itself over the byte stream
(length-prefix each `[av_hdr][payload]`); the ITS packet semantics only apply to the
DC path. Call once at init:
```c
net_port_msg_t reg = {};
reg.itsPort     = AUDIO_STREAM_PORT;   // careful: same number as DC port is fine (different namespace)
reg.tcpNoDelay  = 1;
reg.backlog     = 4;
reg.defaultPort = 0;                   // never auto-open; gated by s.net.audio_port
safeStrncpy(reg.nvsKey, "audio_port", sizeof(reg.nvsKey));
itsSendAux("net", NET_PORT_REG_PORT, &reg, sizeof(reg), pdMS_TO_TICKS(500));
```
Because TCP framing differs from DC, **v1 can ship DC-only and defer TCP** if the
length-prefix framing is more than wanted at first; the spec ("anyone who connects")
is satisfied by the browser path alone. Recommend: implement DC first, add the
length-prefixed TCP variant second. Mark this clearly when scoping the build.

---

## 9. Kconfig (`spangap/audio/esp-idf/Kconfig`)

The straddle DEFINES the knobs; a board sets their values. No magic constants in
`.cpp` — every pin/tunable is a `CONFIG_AUDIO_*`.

```
menu "Audio (spangap/audio)"

config AUDIO_OUT_ENABLE          bool "Speaker output"            default y
config AUDIO_IN_ENABLE           bool "Microphone input"         default y

choice AUDIO_IN_MODE             prompt "Mic I2S mode"           default AUDIO_IN_MODE_STD
  config AUDIO_IN_MODE_STD       bool "Standard I2S (codec, e.g. ES7210)"
  config AUDIO_IN_MODE_PDM       bool "PDM mic"
endchoice

config AUDIO_OUT_I2S_PORT        int  "Speaker I2S controller"   default 1
config AUDIO_OUT_BCK_GPIO        int  "Speaker BCLK gpio"        default 7
config AUDIO_OUT_WS_GPIO         int  "Speaker WS gpio"          default 5
config AUDIO_OUT_DOUT_GPIO       int  "Speaker DATA gpio"        default 6

config AUDIO_IN_I2S_PORT         int  "Mic I2S controller"       default 0
config AUDIO_IN_MCLK_GPIO        int  "Mic MCLK gpio (-1 none)"  default 48
config AUDIO_IN_SCK_GPIO         int  "Mic BCLK/SCK gpio"        default 47
config AUDIO_IN_WS_GPIO          int  "Mic WS/LRCK gpio"         default 21
config AUDIO_IN_DIN_GPIO         int  "Mic DATA/DIN gpio"        default 14

config AUDIO_RATE_DEFAULT        int  "Default sample rate"      default 16000
config AUDIO_BLOCK_MS_DEFAULT    int  "Default block ms"         default 20
config AUDIO_MAX_CLIENTS         int  "Max ITS clients"          default 4
config AUDIO_RING_MS             int  "Mic raw ring (ms)"        default 500
config AUDIO_OUT_MIX_MS          int  "Output buffering (ms)"    default 120
config AUDIO_WAV_BUF_MS          int  "WAV read-ahead (ms)"      default 200

endmenu
```

(The ES7210 I2C address is a board concern; keep it in the board's Kconfig or the
board codec shim, not here — see "config belongs in owning straddle".)

---

## 10. Storage vars

| Key | Persist | Default | Meaning |
|---|---|---|---|
| `s.audio.rate` | flash | `CONFIG_AUDIO_RATE_DEFAULT` | requested device sample rate |
| `s.audio.block_ms` | flash | `CONFIG_AUDIO_BLOCK_MS_DEFAULT` | global block size (capture/stream/mix unit) |
| `s.audio.codec` | flash | `ulaw16k` | default outbound codec for clients that don't send one |
| `s.audio.out_gain` | flash | 256 | speaker gain, 8.8 fixed (256 = 1×) |
| `s.audio.mic_gain` | flash | 1 | mic capture gain (integer; >1 amplify, 0 mute) |
| `s.audio.version` | flash | 1 | config-migration guard (seccam pattern: bump → re-`storageDefault`) |
| `s.net.audio_port` | flash | 0 (off) | TCP listen port (only if TCP variant built) |
| `audio.sample_rate` | ephemeral | — | rate the codec actually clocked (status) |
| `audio.clients` | ephemeral | — | live connected-client count (status) |

`audioInit` seeds defaults under a version gate (mirror seccam audio.cpp:547-552):
```c
if (storageGetInt("s.audio.version", 0) < AUDIO_VERSION) {
    storageDefault("s.audio.rate", CONFIG_AUDIO_RATE_DEFAULT);
    storageDefault("s.audio.block_ms", CONFIG_AUDIO_BLOCK_MS_DEFAULT);
    storageDefault("s.audio.codec", "ulaw16k");
    storageDefault("s.audio.out_gain", 256);
    storageDefault("s.audio.mic_gain", 1);
    storageSet("s.audio.version", AUDIO_VERSION);
}
```

---

## 11. Public API (`spangap/audio/esp-idf/audio.h`)

```c
void audioInit();                              // init: hook (auto-dispatched)

// Board codec control registration (optional; see §3)
void audioRegisterCodec(const audio_codec_ops_t* ops);

// Play a WAV from the filesystem, mixed into the speaker output.
//   - PCM16 (fmt 1) or µ-law (fmt 7); sample rate MUST equal the device rate
//     (rejected otherwise — no resampler in v1).
//   - Non-blocking: streams in the background via fs_* read-ahead.
//   - Returns a handle (>=0) or <0 on error (missing file / bad fmt / wrong rate).
int  audioPlayWav(const char* path);
void audioStopWav(int handle);                 // cancel one (-1 = all)

// Format helpers carried over from seccam (codec → rate/bits/wav-fmt/sizes).
uint32_t audioOutputRate(audio_codec_t);
audio_codec_t audioCodecFromConfigString(const char*);
// ... (full set from seccam audio.h:64-72)
```

No `extern "C"` around `audioInit` (the generated `spangapInitStraddles()` dispatcher
emits a C++-linkage forward decl — wrapping it in `extern "C"` breaks the link).

---

## 12. CLI (`spangap/audio`)

Register via `cliRegister` from `audioInit`:
- `audio status` — rate, `audio.sample_rate`, block_ms, client count, mixer peak,
  RX/TX active flags.
- `audio play <path>` — `audioPlayWav` for a quick speaker test over `spangap cli`.
- `audio stop` — `audioStopWav(-1)`.

(CLI commands are silent on success per platform convention.)

---

## 13. `spangap/audio/straddle.yaml`

```yaml
schema_version: 1
name: spangap/audio
prefix: audio
version: 0.1.0
firmware: esp-idf
platforms: [idf]
# net is needed only for the optional TCP listener; the DC path comes via web,
# which pulls net transitively anyway. Hard-require keeps TCP registration valid.
requires:
  - spangap/spangap-net
init:
  - call: audioInit
```

(No `browser:` half in v1. Add `browser_register:` later for a mic/PTT panel.)

---

## 14. `spangap/hw-tdeck` additions

**(a) Pin/mode values** — append to the existing `kconfig:` block in
`/straddles/hw-tdeck/straddle.yaml` (board straddle is non-buildable, so its
`sdkconfig.defaults` is ignored — hardware values MUST go in `kconfig:` to survive
`--with`; this is the same reason its LCD/SD/LoRa pins live there):

```yaml
  # Audio (consumed by spangap/audio when staged). ES7210 mic on I2S0 (std),
  # MAX98357A speaker on I2S1. Pin sets are disjoint → true full-duplex.
  - CONFIG_AUDIO_OUT_ENABLE=y
  - CONFIG_AUDIO_IN_ENABLE=y
  - CONFIG_AUDIO_IN_MODE_STD=y
  - CONFIG_AUDIO_OUT_I2S_PORT=1
  - CONFIG_AUDIO_OUT_BCK_GPIO=7
  - CONFIG_AUDIO_OUT_WS_GPIO=5
  - CONFIG_AUDIO_OUT_DOUT_GPIO=6
  - CONFIG_AUDIO_IN_I2S_PORT=0
  - CONFIG_AUDIO_IN_MCLK_GPIO=48
  - CONFIG_AUDIO_IN_SCK_GPIO=47
  - CONFIG_AUDIO_IN_WS_GPIO=21
  - CONFIG_AUDIO_IN_DIN_GPIO=14
  - CONFIG_AUDIO_RATE_DEFAULT=16000
```

**(b) Init hook** — add to `hw-tdeck/straddle.yaml` `init:` (gated, so it's only
emitted/linked when the audio straddle is staged; pattern mirrors the existing
`tdeckLcdInit ... when: spangap/spangap-lcd`):

```yaml
init:
  - call: tdeckAudioInit
    when: spangap/audio
  # ... existing gpsInit etc.
```

**(c) Codec shim** — new file
`/straddles/hw-tdeck/esp-idf/conditional/spangap-audio/tdeck_audio.cpp`
(the `conditional/<dep>/` dir is auto-compiled only when the dep is staged; mirrors
`conditional/spangap-lcd/`):

```c
// ES7210 quad-mic ADC control over I2C0 (addr 0x40). Programs the register set
// for `rate` and 16-bit I2S slave-to-S3-master, then hands back the clocked rate.
static uint32_t es7210InInit(uint32_t rate) { /* I2C reg sequence */ return rate; }
static void     es7210InDeinit(void)        { /* power down */ }
static const audio_codec_ops_t es7210_ops = { es7210InInit, es7210InDeinit, nullptr, nullptr };

void tdeckAudioInit(void) {   // C++ linkage, no extern "C"
    audioRegisterCodec(&es7210_ops);
}
```
Recommended: implement the ES7210 register sequence via the
`espressif/esp_codec_dev` managed component (it ships an ES7210 driver) rather than
hand-rolling registers — add it to the conditional slice's `idf_component.yml`. The
shim runs over the shared I2C0 bus (same bus the GT911 touch/other peripherals use;
the board already brings I2C up).

---

## 15. File layout

```
spangap/audio/
  straddle.yaml
  README.md                       # how to consume, wire format, storage vars
  esp-idf/
    CMakeLists.txt                # REQUIRES ${SPANGAP_REQUIRES}; include spangap_requires.cmake
    Kconfig
    idf_component.yml
    audio.h                       # public API + audio_codec_t + audio_codec_ops_t
    audio.cpp                     # task, I2S engine, ITS server, mixer, fan-out, CLI, init
    av_hdr.h                      # lifted from seccam (verbatim)
    codec.cpp / codec.h           # µ-law/PCM16/ADPCM encode + DECODE, format helpers
    wav.cpp / wav.h               # WAV parse + streamed playback into the mixer
  docs/audio.md

spangap/hw-tdeck/
  straddle.yaml                   # + audio kconfig block; + tdeckAudioInit when: spangap/audio
  esp-idf/include/tdeck.h         # (optional) BOARD_* audio pin constants for docs/cross-ref
  esp-idf/conditional/spangap-audio/
    tdeck_audio.cpp               # ES7210 shim → audioRegisterCodec()
    idf_component.yml             # espressif/esp_codec_dev dep (if used)
```

CMake idiom (consumer): `include(${CMAKE_CURRENT_LIST_DIR}/spangap_requires.cmake)`
then `REQUIRES ${SPANGAP_REQUIRES} driver esp_driver_i2s ...`. Never hand-write a
sibling straddle's repo name in `REQUIRES` (staging lint rejects it).

---

## 16. Build & verify

Build (per the build conventions — never from a sub-straddle, no `--flash-size`):
```
spangap build reticulous/reticulous --with spangap/hw-tdeck --with spangap/audio
spangap flash         # signals the host monitor
```
Iterate via `spangap log -f` (not cat/tail) and `spangap cli`.

**Verification ladder:**
1. **Boot/link:** `spangap validate` then a full build; confirm `audioInit` and
   `tdeckAudioInit` land in the generated dispatcher and the conditional slice
   compiled (grep build output for `tdeck_audio`).
2. **Speaker only:** put a device-rate PCM16 WAV on the FS; `spangap cli audio play
   /path.wav` → hear it. Confirms I2S1 TX + mixer + WAV path independent of mic.
3. **Mic only:** connect a browser DataChannel `audio:1`, confirm blocks arrive with
   correct `av_hdr` (type=1, codec, sane epoch_ms). Confirms I2S0 RX + ES7210 shim +
   fan-out + framing.
4. **Full-duplex:** stream mic to a client AND play a WAV simultaneously; watch
   `spangap log` for clock glitches / underruns. This is the one bench check that
   confirms §4's analysis on real hardware.
5. **Mixing:** two clients each send audio to the port → both play, summed.

**Watch-outs (platform hazards):**
- WAV reads through `fs_*` only — never raw LittleFS (PSRAM-stack-during-flash crash).
- ITS sync objects (queues/stream-buffers) and FreeRTOS control structures must NOT
  be in PSRAM. The ITS server's own buffers are handled by ITS; any sync object the
  audio task creates itself must be internal-DRAM.
- I2S DMA needs internal DRAM; large audio sample buffers (rings, mix, WAV
  read-ahead) go in PSRAM via `heap_caps_*(MALLOC_CAP_SPIRAM)`, DMA descriptors do not.
- Hold the `PM_NO_LIGHT_SLEEP` lock only while RX or TX is active (seccam pattern);
  I2S is APB-gated in light sleep. CPU_FREQ_MAX is not needed.

---

## 17. Open items at build time

1. **GPIO-21 duplex bench confirmation** (§4 step 4) — expected pass.
2. **ES7210 register sequence** — use `espressif/esp_codec_dev`; confirm the I2C
   address (`0x40` default) and that the board's I2C0 is already initialised by the
   time `tdeckAudioInit`/first-capture runs.
3. **TCP framing** — DC-first; add length-prefixed TCP as a second pass (§8). Decide
   scope before starting.
4. **Inbound header heuristic** — finalise how `onRecv`/`pushInbound` distinguishes a
   12-byte `av_hdr` from raw PCM16 (validate `type`+`codec`, else treat as PCM16).

---

## 18. Key source references (for the building context)

- ITS public API: `/straddles/spangap-core/esp-idf/include/its.h`
  (server: `itsServerInit` 115, `itsServerPortOpen` 143, `itsServerOnConnect/OnRecv/
  OnDisconnect` 151-154, `itsServerActive` 158; data: `itsSend`/`itsRecv`/`itsConnected`;
  aux: `itsSendAux` 244; kinds `its_port_kind_t` ~64; cbs 83-100).
- ITS packet-server template (fan-out): `/straddles/spangap-core/esp-idf/src/log.cpp`
  (DC port open + per-consumer slot fan-out).
- ITS packet-server template (per-conn recv): `/straddles/lxmf/esp-idf/src/lxmf.cpp`
  (`onLinkInboxConnect/Recv/Disconnect`).
- DataChannel↔ITS bridge: `/straddles/spangap-web/esp-idf/src/webrtc_task.cpp:702-754`.
- TCP↔ITS bridge + `net_port_msg_t`: `/straddles/spangap-net/esp-idf/include/net.h:86-126`,
  registration example `/straddles/sshd/esp-idf/src/sshd.cpp:90-110`.
- Audio reference (capture, ring, codecs, av_hdr, PM lock): `/straddles/seccam/esp-idf/main/`
  (`audio.cpp`, `audio.h`, `av_hdr.h`; live framing `live.cpp:140-150`,
  `LIVE_AUDIO_CHUNK` 35; RTSP 20 ms chunk `rtsp.cpp:391`).
- Board conditional-slice + when-hook pattern: `/straddles/hw-tdeck/straddle.yaml`
  (`start:`/`init:` with `when: spangap/spangap-lcd`) and
  `/straddles/hw-tdeck/esp-idf/conditional/spangap-lcd/src/tdeck_lcd.cpp`.
- T-Deck audio pins + duplex notes: `/straddles/hw-tdeck/docs/tdeck.md:280-298`.
- ESP32-S3 I2S caps: `/opt/esp/idf/components/soc/esp32s3/include/soc/soc_caps.h:228`
  (`SOC_I2S_NUM=2`); IDF std driver `/opt/esp/idf/components/esp_driver_i2s/include/driver/i2s_std.h`.
- Manifest/hooks/conditional schema: `/straddles/spangap/build-system/schemas/straddle.schema.json`.
```
