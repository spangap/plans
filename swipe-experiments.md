# Swipe typing on the panel — what was built, what it scored, why it was dropped

> **Verdict: abandoned, and the code reverted.** A complete gesture keyboard was
> built for `spangap-lcd`, measured against real strokes off a WisMesh TAP V2,
> and reached **7 of 15 words right on the first guess, 9 of 15 in the top
> three**. That is not a keyboard. The commit it was built on is
> `spangap-lcd@e72864f`; nothing of it survives in the tree except the touch
> sampler fix in §6.1, which is worth keeping on its own account.
>
> This file exists so the next attempt does not re-run the eleven experiments in
> §7 that changed nothing. The **labelled traces in §10 are the valuable part**:
> code can be rewritten, but that is a record of someone's actual fingers, and
> without it any future attempt is back to guessing about people.

## 1. What was built

Gesture typing by the SHARK² method (Kristensson & Zhai, 2004), which is what
every swipe keyboard is: resample the finger's path to a fixed number of points
spaced evenly *by distance*, score it against the path each candidate word would
have drawn, and take the best.

```
finger down on H … drags over E, L, L, O … lifts
  → resample to 32 points → score every plausible word → "hello"
```

Four pieces:

| piece | what it was |
|---|---|
| `lcd_swipe.c/.h` | the recognizer. No LVGL, no ESP-IDF — points and key centres only |
| `lcd_swipe_dict.c/.h` | 40,000 English words, generated |
| `scripts/gen-swipe-dict.py` | the generator |
| `scripts/swipe-selftest.c` | host harness: synthetic gestures, and replay of real ones |

Plus the wiring in `lcd_keys.c`: capture the path, decide press-versus-gesture,
emit the winning word as characters.

**The recognizer knowing nothing about LVGL was the single best decision.** It
is what allowed the whole thing to be compiled and measured on a laptop, and
later to replay real traces off the device. Any future attempt should keep that
property whatever else it changes.

### 1.1 Scoring

Three terms, summed, lowest wins:

- **shape** — both paths moved to a common centre and scaled to a common size,
  then compared point by point. Survives sloppy aim; does the real work.
- **location** — the same comparison without that normalisation. Shape alone
  cannot separate words drawing the same figure in different places.
- **frequency** — how common the word is. "hello" and "helo" draw the *same*
  path (a doubled letter adds no motion), so nothing else can separate them.

### 1.2 The word list

Bucketed by **(first letter, last letter)** — 676 buckets — because a swipe
starts on its word's first letter and ends on its last. Reading a handful of
buckets turns 40,000 words into a few hundred before any real work happens.

A word is stored without its first and last letter (the bucket carries them),
which is over a quarter of the blob for free. Frequency rides in a parallel byte
array so the sweep is two dense forward reads.

Everything `const`, so it linked into `.rodata` and was read out of
memory-mapped flash. Verified with `size`:

```
lcd_swipe_dict.o    text 299260   data 0   bss 0      <- 292 KB flash, zero RAM
lcd_swipe.o         text   3756   data 0   bss 3252   <- 3.2 KB internal DRAM
```

The trace buffer (1 KB) lived in the widget, on the heap, i.e. PSRAM. The work
buffers were deliberately `static` and therefore internal DRAM, because the lcd
task's stack is in PSRAM (`lcd.cpp` spawns it `STACK_PSRAM`) and this was the one
inner loop in the UI tight enough to care.

Sources: Peter Norvig's `count_1w.txt` (333k words with corpus counts,
<https://norvig.com/ngrams/>) intersected with dwyl/english-words `words_alpha.txt`
(Unlicense). Neither alone works — the count file's tail is not English
("bizjournalshire" outranks "embryo"), and the word list has no idea what is
common. **The count data's redistribution terms are not stated at the source and
would need settling before shipping.**

## 2. The panel is the hard part

2.8" at 320x240, so 0.178 mm/px. Measured off the real layout:

| | px | mm |
|---|---|---|
| key pitch (horizontal) | 31.88 | 5.67 |
| row pitch (vertical) | 26.5 | 4.72 |
| all three letter rows together | 53 | 9.4 |

A phone's keys are roughly 7 x 10 mm. **The vertical squeeze is the story**: a
finger pad is 8–10 mm across, which is wider than the entire vertical span from
the Q row to the Z row. Horizontal discrimination is fine; vertical is close to
guesswork, and every confusion seen in testing was a column confusion or a
same-shape word.

## 3. What it scored

Against 15 labelled traces from real use (§10):

```
top 1:  7/15   (47%)
top 3:  9/15   (60%)
```

Synthetic gestures, for comparison, at an aim error of 0.45 key pitches:
about 63% top-1. The synthetic number is an upper bound and should be
distrusted — see §8.

Timing on the ESP32-S3, measured on-device: **30–76 ms before the optimisation
in §5, 2.6–24 ms after.**

## 4. Where the errors actually were

The instinct is that the strokes were bad. **They were not.** Measuring each
stroke against its own letters — minimum distance from the path to each key
centre, in key pitches:

```
testing     worst 0.84    is      worst 1.04    fun     worst 0.78
when        worst 1.05    you     worst 0.76    do      worst 1.23
together    worst 0.76    weird   worst 0.72    are     worst 0.53
populating  worst 0.76    cities  worst 0.99    government worst 0.94
on          worst 0.36    it      worst 1.27
but         worst 1.80   <- the only stroke that genuinely missed a letter
```

Every stroke but one passed within about a key of every letter it needed. The
recognizer had the information and did not use it.

What the errors *did* correlate with is **word length**:

- 6 letters or fewer — usually right (`is`, `fun`, `when`, `do`, `on`, `it`, `cities`)
- 7 letters — marginal (`testing`, rank 3)
- 8+ letters — usually wrong (`together`, `populating`, `government`)

The reason is that a ten-letter word asks for seven or eight changes of
direction and a quick stroke makes four. The path passes *near* all the letters
— a long sweep passes near a whole row incidentally — but its **shape** is not
the word's shape. That is a property of the gesture, not of the scoring, and no
weight fixes it.

### 4.1 The two ends are not alike

The most useful single measurement taken. Distance from the stroke's endpoints
to the letters they were supposed to be, in key pitches:

```
             start    end
mean          0.36    1.12
worst         0.60    2.78
```

**People aim at the start and trail off at the end.** `government` lifted 2.78
pitches from its last letter — so far out it could not even be a candidate under
a symmetric radius of 1.3.

This invalidated an obvious-looking idea: charging the score for how far the
endpoints fell from their letters. Applied symmetrically it made things *worse*
(7 → 5 of 15), because it penalises the true word more often than its rivals.
Split — tight radius plus a cost on the start, wide radius and no cost on the
finish — it did exactly what was predicted on the individual cases and still did
not move the total.

## 5. The performance finding, which is reusable

Per candidate the recognizer was doing about 200 `sqrtf` calls, at 98 µs each
candidate. The cause is specific and worth remembering for anything numeric on
this chip:

> **The Xtensa LX7 floating-point unit has no square-root and no divide
> instruction.** Both are software routines of roughly a hundred cycles. Code
> that would be free on any desktop is the whole cost here.

Fixes, in order of payoff:

1. **Hoist everything that does not depend on the candidate.** Key centres in
   key-pitch units (a divide per letter per word), a 26x26 letter-pair distance
   table and its reciprocals (so a word's ideal path needs *no* square root at
   all — 325 once instead of eight per word), and a per-letter "how close did
   the path come" table (turning a 32-point scan into one comparison).
2. **Mean squared distance instead of mean distance** in the scoring loop —
   removes 64 roots per candidate and is a monotone transform, so it loses
   nothing. It also weights one badly-wrong point above two slightly-wrong ones,
   which is arguably better.
3. **Resample by precomputed segment lengths.** The obvious formulation
   re-measures the remaining distance after each emitted point, costing a root
   *and* a divide per output point.

Together: 30–76 ms → 2.6–24 ms.

## 6. Two real bugs found, unrelated to swipe

### 6.1 A single missed read ended a drag — FIXED

`lcd_touch.cpp`'s sampler declared a release on the **first** read with no
finger. A capacitive panel drops the occasional sample, so one drag arrived as
two or three, and anything reading a whole stroke got it in fragments. This was
the largest single cause of bad results — a ten-letter word truncated at its
fifth letter matched a five-letter word, exactly as it should have.

Fixed independently of swipe: a finger must now be missing for `kLiftReads`
(three reads, 30 ms) before it counts as lifted. **This matters for any future
gesture — pinch, swipe, drag-to-reorder — not just this one.**

### 6.2 Keys commit on press, not release — STILL OPEN

The letter keys carry `LCD_KEYGRID_CTRL_POPOVER`, and `lcd_keys_update_ctrl_map`
strips it whenever popovers are off (which is always). `lcd_keygrid` fires
`LV_EVENT_VALUE_CHANGED` **on press** for any key with neither `POPOVER` nor
`CLICK_TRIG` — so every letter commits the instant the finger lands.

For swipe this was fatal: the first letter was typed before any drag could be
recognised. On its own merits it is still a wart — committing on release is what
gives a touch key **slide-off-to-cancel**, which this keyboard does not have.
One-line fix (force `CLICK_TRIG` in `lcd_keys_update_ctrl_map`), not applied
because it was not asked for.

## 7. Experiments that changed nothing

Measured on the real traces. This is the section that saves the next attempt its
time.

| experiment | result (of 15) |
|---|---|
| endpoint-distance cost, symmetric | **worse**, 7 → 5 |
| endpoint split: tight start + cost, wide finish | 7 — fixed the predicted cases, moved nothing |
| start radius 0.7 / 0.85 / 1.0 / 1.3 | 7 / 7 / 7 / 7 |
| finish radius 1.3 / 1.8 / 2.6 / 3.4 | 7 / 7 / 7 / 7 |
| single radius 1.0 / 1.3 / 1.8 / 2.4 | 3 / 7 / 7 / 7 (1.3 is a real knee) |
| visit radius 1.3 → 3.2 | flat |
| frequency weight 0.05 → 0.28 | ±1 |
| location weight 0.2 → 1.5 | ±1 |
| dictionary 10k / 20k / 40k words | 6 / 6 / 7 |
| resample points 32 / 48 / 64 / 96 | 7 / 7 / 7 / 7 |
| rounding the template's corners | 8 at one setting, worse either side |
| candidate cap 2500 / 6000 | 7 / 6 |

**Top-3 sat at 9/15 through every single one of these.** When everything you
turn does nothing, the thing you are turning is not the constraint.

### 7.1 The one thing that did move

**Elastic matching (dynamic time warping) in place of the fixed
point-for-point correspondence.** The scoring paired point *i* of the stroke
with point *i* of the template, which assumes both paths spend their length the
same way. A finger cutting corners does not, and from the first mismatch every
later pair compares the wrong parts. Dynamic time warping lets one path stretch
against the other at a cost, restricted to a band near the diagonal.

```
band  0 (i.e. the original)   top1 7,  top3 9
band  3                       top1 8,  top3 9
band  5                       top1 8,  top3 10
band  8                       top1 8,  top3 10
```

First change in eleven to move either number, and it targets precisely the
diagnosed failure. It arrived after the decision to stop, so it was never taken
further. **This is the thread to pull if anyone revisits.** Cost is a banded
32x32 grid per scored candidate — affordable only on top of §5.

## 8. Do not trust a modelled finger

The harness drew synthetic gestures and reported ~63% where reality gave 47%.
Worse, the model was actively misleading twice:

- **First version used a Catmull-Rom spline through the letters.** It overshoots
  every direction reversal by most of a key and draws a path no hand makes.
  "hello" and "people" were unrecognisable and top-1 read 40%. Replacing it with
  straight legs and rounded corners took the same code to 84% — *the recognizer
  had not changed at all*. An entire round of tuning was spent on a bug in the
  test.
- **The synthetic set said touch sample rate barely mattered** (30 Hz vs 100 Hz
  worth three points) and that endpoint tolerance could be tight. Both were
  wrong about real strokes.

The synthetic harness is worth keeping for what it *can* answer — that the
geometry is right, that pruning is not discarding the answer, that a change did
not break something, and how long a match takes. It cannot answer "is this
good", and it should never again be used to choose a threshold that real traces
could choose instead.

## 9. What it would take

Not more hand-tuning of SHARK² — that is exhausted, and §7 is the evidence.

1. **Show candidates.** The right word is in the top three 60% of the time and
   first 47%. Every shipping swipe keyboard has a candidate bar precisely
   because the top guess is wrong this often. It is the cheapest available win
   and it was never built. It does not get you to a good keyboard on its own —
   60% still means one word in three needs retyping.
2. **Elastic matching** (§7.1).
3. **Honestly: a learned model.** Modern swipe keyboards do not score against
   ideal templates; they run a small neural network trained on real traces. That
   is what closes the gap between 60% and the ~95% that feels invisible, and it
   needs a corpus of labelled strokes this project does not have.
4. **Or a bigger keyboard.** Landscape with a taller key area, or fewer rows.
   4.72 mm of row pitch is the root physical problem and no software fixes it.

A fair reading is that swipe on a 57 mm keyboard may simply be worse than swipe
on a phone, and the ceiling found here is close to what this method gives on
this glass.

## 10. The labelled traces

Points are display pixels, `x,y` space-separated, in order. Captured off a
WisMesh TAP V2 (320x240, key pitch 31.88 px) with the on-screen keyboard's text
layout. The key centres they were matched against:

```
a( 25,171) b(168,198) c(104,198) d( 89,171) e( 80,145) f(121,171) g(153,171)
h(185,171) i(238,145) j(217,171) k(249,171) l(281,171) m(232,198) n(200,198)
o(270,145) p(302,145) q( 16,145) r(111,145) s( 57,171) t(143,145) u(206,145)
v(136,198) w( 48,145) x( 72,198) y(175,145) z( 40,198)
```

Intended word, then the stroke:

```
testing	128,147 114,151 110,152 106,153 95,158 91,160 85,162 83,163 81,164 80,164 80,165 82,165 83,166 84,166 97,163 101,162 105,161 107,160 108,160 109,160 113,158 114,158 120,156 135,151 141,149 159,148 171,147 183,146 192,146 200,146 205,147 209,149 212,152 213,156 213,162 213,167 212,171 210,174 208,176 204,178 199,179 193,179 188,179
is	222,145 209,148 206,148 179,154 163,157 144,160 128,164 114,168 104,170 96,172 90,174
fun	131,173 141,167 143,166 146,164 161,157 168,154 175,151 180,149 184,149 188,151 191,154 193,157 195,161 197,166 198,169 199,173
when	64,153 78,153 82,153 102,154 113,155 125,156 134,156 141,157 146,157 150,158 151,158 150,159 142,160 131,161 126,161 119,162 116,162 110,162 107,162 105,162 104,162 103,162 102,162 116,165 126,168 137,172 147,175 156,178 164,181 170,183
you	185,149 189,149 204,149 209,149 214,149 217,149 222,149 224,149 235,149 238,149 242,149 243,149 245,149 246,149
do	108,170 126,168 146,164 164,162 183,160 198,159 210,158 219,158 227,158 233,158
together	145,153 160,153 164,153 172,153 191,153 202,153 213,153 221,153 227,153 232,153 236,153 239,153 242,153 244,153 245,153 246,153 247,153 244,153 238,155 230,157 221,160 212,163 204,165 199,167 196,168 194,168 193,169 192,169 188,170 187,170 182,172 177,173 171,174 164,175 157,176 141,176 135,176 129,176 116,172 110,170 104,167 100,165 97,164 96,162 95,162 94,161 93,161 97,160 102,159 108,159 114,159 117,159 123,159 125,159 130,159 132,159 137,159 139,159 141,159 142,159 143,159 140,159 136,159 134,159 129,159 127,159 125,159 124,159
weird	47,151 71,151 97,151 113,151 133,151 148,151 161,151 171,151 179,151 185,151 190,151 194,151 197,151 200,151 203,151 205,151 207,151 209,151 211,151 213,151 214,151 215,151 216,151 215,151 211,149 202,149 192,148 180,148 168,148 159,148 151,148 146,148 142,148 139,148 137,148 135,148 134,148 133,148 131,148 129,148 127,149 124,150 122,152 120,153
are	30,172 44,168 48,167 65,164 74,163 82,161 88,160 92,159 96,158 98,158 99,158 100,158 99,158 98,158
populating	303,155 300,155 285,155 280,155 263,155 254,155 246,155 240,155 237,155 235,155 234,155 235,155 238,156 242,157 244,158 245,158 246,159 247,159 248,163 239,166 224,167 206,168 180,171 157,172 136,173 117,173 100,174 86,174 74,174 65,174 59,174 54,174 51,174 49,174 49,173 52,170 62,167 74,164 90,160 102,158 113,156 121,155 127,154 131,154 135,154 138,154 141,154 143,154 148,154 158,152 169,151 184,150 198,149 209,149 219,149 226,149 232,149 236,149 239,149 242,149 244,149 245,149 246,149 247,149 248,149 246,154 243,158 238,165 233,170 228,174 222,177 214,178 204,177 194,174 184,170
cities	118,197 122,195 139,189 151,185 163,181 173,177 183,174 191,171 197,169 203,167 208,166 212,165 215,164 218,163 220,163 222,162 223,162 224,162 223,162 221,162 215,162 207,161 199,161 191,161 184,161 179,161 174,161 171,161 168,161 167,161 166,161 169,161 174,161 180,161 186,161 192,161 198,161 202,161 206,161 208,161 210,161 211,161 212,161 211,161 208,160 201,159 192,159 178,159 165,159 159,159 154,159 138,159 133,159 128,159 125,159 123,159 122,159 121,159 117,159 106,160 99,161 93,163 88,165
but	179,191 185,185 186,183 188,181 189,180 194,173 196,171 197,169 198,168 199,167 199,166
government	159,178 176,170 188,165 201,159 212,155 220,151 226,149 231,147 235,145 237,144 239,143 240,143 239,144 232,148 221,156 207,165 201,169 184,181 178,185 172,189 169,191 167,192 162,194 161,195 155,195 150,195 144,193 129,185 121,179 113,174 107,169 102,165 98,163 95,161 94,160 93,159 92,158 91,158 92,157 93,157 95,157 96,157 97,157 98,157 99,157 100,157 101,157 102,157 103,157 104,157 105,156 106,156 107,156 107,155 108,155 109,154 110,153 111,152 112,151 113,150 115,149 116,148 117,148 118,147 119,146 120,146 121,145 122,145 123,144 124,144 125,144 126,143 127,143 128,143 129,143 131,143 132,143 133,143 137,144 151,150 155,152 160,154 174,162 179,165 184,167 186,169 188,169 188,170 189,170 190,171 200,179 203,181 206,184 208,185 209,187 210,188 211,188 212,189 212,190 213,190 214,191 215,191 216,191 217,192 218,192 219,192 220,192 218,192
on	261,152 252,158 250,160 236,170 229,175 222,180 217,184 213,187 210,190 208,192 206,194 205,195 204,196 203,196 203,197 202,197 202,198 201,198 200,198
it	236,152 219,152 215,152 210,152 204,152 201,152 188,152 183,152
```

Three more, unlabelled, kept because they are the failure that matters most —
**taps whose finger slid, which were read as gestures and typed two-letter
words** (`gu`, `yr`, `tr`) in place of the letter meant. Spans of 0.85–0.97 key
pitches. Any future attempt needs its tap-versus-gesture threshold above these:

```
160,168 164,168 169,168 175,168 177,168 183,167 185,167 187,167 188,167
166,156 163,156 158,156 152,156 146,156 143,156 137,156 135,156
160,152 157,152 153,152 147,152 145,152 139,152 137,152 135,152 134,152 133,152
```

Corrupting ordinary typing is far worse than guessing a word wrong, and it is
the first thing to get right. A threshold of 1.6 key pitches cleared all three
and cost only the ability to swipe two-letter words between adjacent keys, which
are two taps anyway.

## 11. Rebuilding the harness

The recognizer knew nothing about LVGL, so it built on a laptop:

```
cc -O2 -o swipe-selftest swipe-selftest.c lcd_swipe.c lcd_swipe_dict.c -I. -lm
./swipe-selftest                      # synthetic accuracy, candidates, timing
./swipe-selftest --layout             # key centres, to check against the device
./swipe-selftest --replay < traces    # real traces; a label before a tab scores it
```

The harness reproduced `lcd_keygrid.c`'s `update_map()` layout arithmetic
exactly, integer division included, so the key centres matched the device's to
the pixel — worth re-verifying with `--layout` against a device dump before
trusting any number.

Two things made it useful, and both should be rebuilt first next time:

- **Trace capture on the device.** The keyboard logged its raw path at verbose
  in the replay format. Being able to take a failure off the glass and run it a
  hundred times on a laptop is the difference between measuring and guessing.
- **The two-run reachability check.** Every labelled trace was scored twice —
  once as shipped, once with every prune flung wide open. A word the open run
  finds and the shipped run does not was *thrown away before it could be
  judged*, which is a pruning problem; a word neither run finds was judged and
  lost, which is a scoring problem. They need opposite fixes, and guessing
  between them is how tuning goes in circles. In this project the answer was
  always "scoring", which is what eventually made it clear the approach had a
  ceiling.
