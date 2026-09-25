# SIMesh: tickless stations, and simd's pty side on a thread of its own

Running notes for the reviewer. Two stages, each measured before the next.

## Where the work was done

A scratch workspace, never the live trees:
`/tmp/claude-1000/-home-spangap-reticulous/ac0e3560-2696-47f2-b206-e23e312a62ae/scratchpad/wsT`.
Copies (tar with `esp-idf/build.*`, `target`, `node_modules`, `__pycache__`,
`SIMesh/testbed/run`, `reticulous/sim/run` and `SIMesh/radio/build` left out;
`SIMesh/testbed/ui/dist` kept) of SIMesh, hw-linux, spangap-core, spangap-net,
spangap-web, iface-lora and reticulous, each with the copied state committed
as `baseline` in the copy; a symlink to every other entry of the workspace
root; `.spangap-build` a file of its own (a build rewrites it).

Runs: `simT.sh` in the scratchpad, slot c `--bind 0.0.0.0:9017 --ether
127.0.0.1:7006 --net 127.0.24.0/22`, slot d `9018 / 7007 / 127.0.28.0/22`,
`--run` under the scratchpad. `smoke7.yaml` names the internet station by an
address in 127.0.0.0/22 (`tcp peer add 127.0.0.11:4965`, twice), so the runs
use `smoke7c.yaml` / `smoke7d.yaml`, the same file with that address moved to
the slot's own internet station (127.0.24.5 / 127.0.28.5); both are listed in
the copy's `.git/info/exclude` and are not in the patch.

### An error of mine, at the start

The first build in the copy (21:56 UTC) ran without `SPANGAP_WORKSPACE`
pointing at the copy. The container sets `SPANGAP_WORKSPACE=/home/spangap/
reticulous` for every process, and `spangap` takes the workspace from it
before it looks at the working directory, so that build ran in the **real**
workspace: it rebuilt `reticulous/esp-idf/build.linux/reticulous.elf` there
(hw-linux target, from the real trees as they stood, which include
`spangap-core` `cli.cpp` 21:38 and `log.cpp` 21:15, both newer than the 20:41
ELF) and rewrote the real `.spangap-build`. No simd was running on 9013/9014
at that moment (checked with `ps` just before). `.spangap-build` was put back
to what it held (`reticulous/reticulous --with reticulous/lxmproxy --with
reticulous/netgraph --with spangap/hw-waveshare-p4-43`, 110 bytes). The ELF
cannot be put back: the 20:41 one is gone, and the rule is not to build in the
real workspace. Every build since runs with `SPANGAP_WORKSPACE=<wsT>`, and its
log shows only paths under the copy.

## Baseline (the copy as copied, built 21:57, hw-linux target)

Host: 16 cores; another simd (the snapshot check, 9013, max) was running for
part of the time.

Idle station, real time, `mixed.yaml` on slot c, a minute after all five were
up:

- `strace -f -c -p <alpha>` for 10 s:

  | syscall | calls | what |
  |---|---|---|
  | futex | 7489 | the port's task switches |
  | rt_sigprocmask | 9898 | critical sections |
  | times | 2107 | run-time stats, per switch |
  | rt_sigreturn | 1002 | the 100 Hz tick |
  | clock_nanosleep | 976 (all EINTR) | the idle task's 15 ms sleep, cut by every tick |
  | recvfrom | 500 (all EAGAIN) | the webrtc task, 50 Hz |
  | pselect6 | 36 | |
  | epoll_pwait | 2 | the descriptor controller |

- CPU, `/proc/<pid>/stat` over 30 s, twice: alpha 0.63 / 0.57 %, bravo
  0.60 / 0.57 %, charlie 0.60 / 0.57 %.

soak30 at max, run D's recipe (`soakdrive.py 9018 soak30 … --subnet 28`,
defaults: warm 2040 s, 361 messages every 4–6 s of T, drain 600 s), with a
py-spy sample of simd from 150 s after start for 90 s at 200 Hz (blocking
mode, same in every soak30 run so the cost is the same):
`soak30-before`: 4449.2 s of T in 702.8 s of wall, **6.33x**, 1 920 826
barriers, **431.7 per s of T**, 2734 per wall s. Delivery 60/361. Host load
average 4.5–9.7 during it (another simd, the snapshot check, ran on 9013).

## Stage 1: tickless

### Decisions

- **The kernel's own tickless idle, not the idle-hook fallback.** IDF's
  Kconfig ties `FREERTOS_USE_TICKLESS_IDLE` to `PM_ENABLE`, and `PM_ENABLE` does
  not exist for the linux target (the build warns "unknown kconfig symbol
  'PM_ENABLE'"), so the straddle's sdkconfig cannot turn it on. But
  `FreeRTOSConfig.h` only does `#define configUSE_TICKLESS_IDLE
  CONFIG_FREERTOS_USE_TICKLESS_IDLE` and, under it,
  `configEXPECTED_IDLE_TIME_BEFORE_SLEEP CONFIG_FREERTOS_IDLE_TIME_BEFORE_SLEEP`;
  sdkconfig.h never defines either on this target. So hw-linux's CMakeLists
  adds both as build-wide compile definitions (`idf_build_set_property
  (COMPILE_DEFINITIONS …)`, generator-expression evaluated, so every component
  gets them whatever the order). `FreeRTOS.h` takes `portSUPPRESS_TICKS_AND_SLEEP`
  only if defined before it, and the linux portmacro defines none; a function-
  like macro with a prototype does not survive CMake's list quoting, so the
  freertos component alone gets `-include src/suppress.h` (prototype + macro).
  Nothing in /opt/esp/idf is edited. `vTaskStepTick`, `eTaskConfirmSleepModeStatus`
  and the suppress call in `prvIdleTask` are in the ELF (checked with nm and
  objdump: `bl vPortSuppressTicksAndSleep` in the idle task).
- **The port's idle hook** (`esp_vApplicationIdleHook` in port_idf.c, a 15 ms
  `usleep` every pass, a strong symbol in the same object as `main`) is
  replaced with `-Wl,--wrap`. The kernel calls it before deciding whether to
  suppress, and only suppresses for ≥ 2 ticks (FreeRTOS.h refuses a smaller
  threshold). The wrapper returns at once on the first pass after a suppress;
  a second pass without one means a task is due at the very next tick, and it
  sleeps until an interrupt (the tick included). No spin, no timer of its own.
  `CONFIG_FREERTOS_USE_IDLE_HOOK` is dropped: nothing uses `vApplicationIdleHook`
  any more.
- **Real mode**: the suppress stops the itimer with signals blocked
  (`vPortEnterCritical`), aborts if a SIGALRM is already pending or the kernel
  says abort, sleeps in `ppoll(NULL, 0, until tick n, empty mask)` — the mask
  argument opens signals atomically, so a SIGUSR2 that raced the decision ends
  the sleep — then steps `min(passed, expected)` with `vTaskStepTick` and any
  excess as pended ticks (`xTaskIncrementTick` while suspended), and restarts
  the itimer on the phase it had. esp_timer's head is not a separate deadline
  in real time: its task waits in ticks, so it already is the first task due.
- **Virtual mode: the tick is a function of node time, stepped where T moves,
  not in the suppress.** The suppress runs with the scheduler suspended, so
  the ether reader task cannot apply a grant while it runs; a grant ends the
  suppress (SIGUSR2) and is applied by the reader afterwards. So the conductor
  got an advance hook (`simradio_on_advance`, called in `advanceTo` after T
  moves, before any due wake or model timer runs, no lock held), and the
  board's `hwLinuxClockMoved()` brings the tick count up to the clock there
  with `xTaskCatchUpTicks`. A `run` that lands mid-way through a long idle
  steps exactly the ticks crossed (floor), and the esp_timer wake then runs
  due timers in order. The port's itimer is stopped for good at board start
  (`setitimer(0)`, which the shim turns into clearing its wake), so no tick
  is ever counted twice. Hook contract changes (hwlinux.h):
  `hwLinuxClockStart()` returns nonzero when the clock is not the host's;
  new `hwLinuxClockTickAt(us)` (a second wake in services.cpp: the next tick a
  task waits for); new board-offered `hwLinuxClockMoved()`;
  `hwLinuxClockIdle()` is now called from the suppress (after setting the tick
  wake to the tick the first task is due at) and from the wrapper's short
  path, never from `vApplicationIdleHook`.
- **The busy watchdog is unchanged**, and so is what it reports: while any
  task runs the tick wake is the next tick, so a busy station moves T a tick
  at a time as before.
- **Tick grid aligned across stations.** First version: tick n at
  join + n·10 ms of each station's clock. smoke7 max: 23.5 barriers per s of T.
  Since every station's clock counted from its own join instant, their ticks
  never coincided. Now services.cpp counts the station's clock from the whole
  second of node time it joined in, and the board puts ticks on whole
  multiples of 10 ms of that clock, so all stations' ticks are on the same
  10 ms grid of T: 17.0 barriers per s of T on the same run (−28 %).
- **spangap-web**: `webrtc_task.cpp` gets a weak `hwLinuxWait` (as cli.cpp
  has) and, while quiet (no peer, no DTLS, no SCTP, no signalling WS), blocks
  on its UDP socket and inbox with no timeout instead of `vTaskDelay(1)` +
  `itsPoll(1)`. With `hwLinuxWait` absent (a chip) the loop is byte-for-byte
  the old one.
- **spangap-net**: host `netRelayWait` waits with no bound; the host task
  subscribes to `s.net.` so a port change wakes it (the pass is the handler).
  The one remaining bound: the relay leaves a backpressured client out of the
  read set and probes it for hangup "on the select timeout's beat", and the
  owner frees ITS space without notifying the relay, so `netRelayWait` got a
  `held` argument (the chip's definition ignores it) and the host waits 1 s
  while it is true.
- Left as they are: 1 Hz loops of the firmware's own (the log task, tcp,
  lxmf: `itsPoll(100 ticks)`; rnsd `itsPoll(800)`, cron `itsPoll(3000)`,
  storage). They are timers of the application, not polls at tick rate, and
  the same code runs on a chip. Found with `gdb -batch -ex "thread apply all
  bt"` on an idle station (FreeRTOS task threads all read `reticulous.elf`,
  so the task function in the backtrace is the only name).

### Builds (all in the copy, `SPANGAP_WORKSPACE=<wsT>`, target hw-linux)

| when | what | result |
|---|---|---|
| 21:57 | baseline (`elfs/before.elf`) | OK |
| 22:24 | stage 1 | OK; `vPortSuppressTicksAndSleep`, `__wrap_esp_vApplicationIdleHook`, `vTaskStepTick`, `xTaskCatchUpTicks` in the ELF; sdkconfig has `# CONFIG_FREERTOS_USE_IDLE_HOOK is not set` |
| 22:46 | stage 1, tick grid aligned (`elfs/stage1b.elf`) | OK |
| 23:57 | + the netgraph wait fix (`elfs/netgraphfix.elf`, below) | OK |

Radio library: `cmake --build SIMesh/radio/build` in the copy.

### Verification

`pytest SIMesh/radio/tests SIMesh/ether SIMesh/testbed` in the copy: 79 passed
after stage 1 (one new conductor test: the host learns every move of T, before
what is due at it, and not when T stands); 82 after stage 2 (three pty-thread
tests).

**Idle station, real time** (`mixed.yaml`, slot c, final binary, alpha, a
minute or more after all five were up):

| syscall, 10 s | before | after |
|---|---|---|
| rt_sigreturn (the tick) | 1002 | 28 |
| clock_nanosleep (idle's 15 ms sleep) | 976 | 0 |
| recvfrom (webrtc, EAGAIN) | 500 | 0 |
| futex | 7489 | 433 |
| rt_sigprocmask | 9898 | 3930 |
| times | 2107 | 141 |
| ppoll (the suppress) | — | 24 |
| setitimer / rt_sigpending (the suppress) | — | 30 / 15 |
| all | 22 333 | 4 915 |

What is left is the firmware's 1 Hz tasks: one burst a second, the tick
running for the few ticks around it, then suppressed again.

CPU over 30 s, twice: before alpha/bravo/charlie 0.63/0.60/0.60 % and
0.57/0.57/0.57 %; after 0.07/0.13/0.07 % and 0.07/0.07/0.07 %.

Real-time function, final binary: all five up; `lxmf send` alpha → charlie
delivered with proof (stage-1 binary: `DIRECT delivered mid=o_176491_36fe`
after one "proof lost" resend); the berlinmesh stations up and announcing;
a console window over `/ws/console/<name>` answers typed commands (stage 2).

**Virtual time, one idle station** (`solo-r`: one reticulous station, no
neighbours, at max): before 100 barriers per s of T, 38–44x; after 2.17 per s
of T, ~790x. One idle berlinmesh station (`solo-b`): 41 barriers per s of T
(its own ~25 ms loops; not this work's to change).

**smoke7, real against max** (drive.py with the four sends, 1500 s of run;
compare.py `--until 1500` — without it a max run's record runs on past 1500
s while simd is being stopped, and its announce counts come out inflated).
drive.py now takes the run's T from a command result before scheduling the
sends: the clock messages come once a wall second, which at 90x is ~90 s of
T stale, and the first max run of pass A sent every message ~9 s of T late.

| pass | binary | real / max wall | frames | pairs by hops 1/2/3/4 | delivered (real; max) | result |
|---|---|---|---|---|---|---|
| A | stage 1, grid not aligned | 1500.8 / 17.1 s | 252 / 243 | 8/6/3/1 ; 8/6/4/2 | 221 384 472 571 ; 279 378 410 513 | passes |
| B | stage 1 | 1500 / 16.4 s | 264 / 259 | 8/6/4/2 ; 8/6/4/2 | 221 416 472 558 ; 218 381 472 555 | passes |
| C | stage 1 | 1500 / 17.6 s | 246 / 251 | 8/6/4/2 ; 8/6/4/2 | 287 382 413 608 ; 269 386 470 598 | passes |
| D | stage 1 + stage 2 | 1500 / 15.1 s | 252 / 300 | 8/6/4/1 ; 8/6/4/2 | 287 379 461 547 ; 512 1033 1079 1140 | passes (below) |
| E | D + the netgraph fix | 1500 / 6.7 s | 274 / 269 | 8/6/4/1 ; 8/6/4/2 | 228 473 511 1115 ; 377 472 511 1004 | passes |

B and C are two passes in a row on the final firmware. Announces originated
per LoRa station within 1500 s: 5–7 in every run, real and max alike, first
announce per station within 1 s. D: every milestone reached in both, same
path profile, 4/4 delivered in both, but three of max's four messages took a
second or third try ("proof lost", resent over the link; the record has 20
LINKREQUEST and 53 PROOF frames against 14 and 39) — a run's own luck with
collisions, of the kind pass 5 of the virtual-time notes had (290 / 1151).
Outputs: `scratchpad/s7-cmp-{A,B,C,D}.txt`.

**soak30 at max**, same recipe, each row its own run; the host was shared
with another agent's 99-station runs for part of the time, so the rows are
paired as they ran back to back:

| run | binary / simd | wall s | pace | barriers per s of T | per wall s | load avg |
|---|---|---|---|---|---|---|
| soak30-before | before | 702.8 | 6.33x | 431.7 | 2734 | 4.5–9.7 |
| soak30-s1 | stage 1 (grid not aligned) | 309.4 | 14.42x | 378.0 | 5452 | 1.3–3.5 |
| soak30-before2 | before | 785.2 | 5.66x | 409.4 | 2319 | 22 |
| soak30-s1b | stage 1 | 381.5 | 11.65x | 400.8 | 4677 | 22 |
| soak30-s2 | stage 1 + stage 2 | 288.1 | 15.40x | 323.1 | 4991 | 7 → 3.5 |
| soak30-s1c | stage 1 | 307.3 | 14.49x | 385.9 | 5591 | 3.5 → 2.9 |

Stage 1 is 2.1–2.3x faster in wall time on soak30. Barriers per second of T
hardly move: soak30's eight berlinmesh stations each wake the run ~41 times
per second of T, unaligned, which is ~330 of the ~380; what stage 1 removed
is the cost of each barrier (every station woken on every tick), so the
barriers per wall second doubled. Delivery 60–67 of 361 in every run.

**city99-lora at max, first 300 s of wall from the load** (99 reticulous
stations, boot and warm-up; `cityrun.sh`, clock sampled every 30 s):

| binary | T reached | pace | barriers per s of T | slow idles per wall s |
|---|---|---|---|---|
| before | 755 s | 2.5x | 110 | 19 |
| stage 1 + 2 | 2987 s | 10.0x | 84 | 47 |
| + netgraph fix | 15 480 s | 51.4x | 92 | 4 (boot only) |

### The netgraph spin (found here, outside the brief)

After stage 1 a single idle station ran at ~790x with 1.5 % of its grants
answered by the busy watchdog, and one thread at 66 % of a core. gdb on it:
netgraph's task, looping in `itsPoll(0)` / `cfgEnable()`. Its wait is
`itsPoll(delta <= 0 ? 0 : pdMS_TO_TICKS(delta))` on a millisecond deadline:
`pdMS_TO_TICKS` rounds down, so anything due inside the current tick is a wait
of 0 and the task spins until it is due — up to 10 ms of CPU on a chip, and in
virtual time a station that never idles, so the watchdog's 20 ms of wall per
occurrence. NG_SCAN_MS is 30 s: one per station per 30 s of T, which at 99
stations is the whole of the slow idles above. Rounded up (`+ 1`, as rnsd's
`nextDeadline` already does): the single station runs at ~2500x with no slow
idles, smoke7 1500 s in 6.7 s (223x, 18.1 barriers per s of T), soak30 in
187.5 s (23.8x), city99 at 51x. It changes chip behaviour (a sub-tick spin
becomes a one-tick sleep), which the brief keeps out, so it is a separate
patch, `patches/netgraph.patch`, not part of the set.

## Stage 2: simd's pty side on a thread

### The profile first

py-spy 0.4.2 (pip, in a scratch venv). The live simd on 9013 (another
agent's snapshot check, max) was sampled read-only (`--nonblocking`) for the
16 s it had left before it exited: 68 % ether (barrier and medium), UDP
`sendto` 54 % of all samples, JSON 11 %, a snapshot's `copytree` most of the
rest, pty ~0. The same split on my own soak30 runs (blocking mode, 200 Hz, 90
s from 150 s after start; `prof.py` buckets each sample by the first module
in its stack, and tags json / record / sendto / pty across buckets):

| of simd's samples | before | stage 1 | stage 1 + 2 |
|---|---|---|---|
| ether: barrier + medium | 88.5 % | 72.0 % | 73.0 % |
| — of which UDP `sendto` | 74.1 % | 39.7 % | 32.5 % |
| — `recvfrom` (loop's `_read_ready`) | 7.2 % | 11.1 % | 10.5 % |
| — `next_instant` | 1.3 % | 4.9 % | 6.3 % |
| JSON encode + decode, anywhere | 7.2 % | 12.3 % | 16.4 % |
| record writing | 0.1 % | 4.1 % | 4.5 % |
| pty drain + demux + log + console | 0.0 % | 0.6 % | 0.3 % (on the pty thread) |
| websockets / http / proxy | 0.1 % | 2.5 % | 2.7 % |
| event loop and the rest | 11 % | 20.6 % | 19.3 % |

(before: `soak30-before`; stage 1: `soak30-s1b`; stage 1+2: `soak30-s2`. The
before window falls in the warm-up, the others in the traffic phase, which is
why record, websocket and JSON shares grow.)

So in these runs the pty side is not where simd's time goes: under 1 %. The
barrier's own `sendto` is: the system call that puts each `run` on the
loopback, which includes waking the station that receives it. Stage 1 cut it
by waking far fewer stations per barrier. JSON is 12–16 % and the record 4–5 %; the wire
is not changed here. `next_instant` (a scan of every station's `until` per
barrier) is 5–6 % at 40 stations and grows with the count.

### Built

`stations.Ptys`: an asyncio loop in a daemon thread, started on first use.
Per start of a station a `Drain` on that loop: reads the master, runs the
`FrameDemux` and its resync timer, appends to the log, watches for the marker
(`rpc.MarkerWatch`, split out of `RpcClient.on_text`), and hands the main
loop, with `call_soon_threadsafe` in read order, `rpc.on_frame`,
`rpc.on_marker`, `station.console_out` (only while `station.watchers > 0`,
which simd keeps equal to the open console websockets) and
`station.pty_closed`. The log file is written only on the pty thread once a
station has started (the start banner and the close are posted there too), so
ordering holds across restarts; `Drain.close` reads what is left on the pty
before closing it, so a station's last words before it exited reach the log.
Writes to the pty stay on the main loop (non-blocking, an outbox under
`add_writer`, as before), and the fd is closed only by the pty thread after it
has stopped reading it.

### Verification

`pytest`: 82 passed (three new: text, frames and the marker reach their owners
in order with the marker split across two writes; no console bytes cross
while nobody watches; 2000 lines written just before exit are all in the
log). A console window over `/ws/console/out07` answers `show s.net.hostname`.
Logs of every station: no `F5 53 47` bytes, the marker once. soak30:
288.1 s against 307.3 s for stage 1 run right after it — inside the spread
between two stage-1 runs, as the profile said it would be. smoke7 pass D, above,
passes.

## Patches

`scratchpad/patches/<repo>.patch`, `git diff baseline` in the copy (the
`baseline` tag is the copied state). Not applied to the real repos: the
coordinator asked for them to be left for review while another agent builds
and runs from the real trees. `git apply --check` of each against the real
repo, 00:29 UTC: all OK.

    cd /home/spangap/reticulous
    P=/tmp/claude-1000/-home-spangap-reticulous/ac0e3560-2696-47f2-b206-e23e312a62ae/scratchpad/patches
    git -C SIMesh      apply $P/SIMesh.patch
    git -C hw-linux    apply $P/hw-linux.patch
    git -C spangap-net apply $P/spangap-net.patch
    git -C spangap-web apply $P/spangap-web.patch
    git -C netgraph    apply $P/netgraph.patch      # optional: outside the brief, see above

spangap-core, iface-lora and reticulous: no change. (The build rewrites
`reticulous/web-interface/package.json` and its lock for the straddle set it
builds; that is in the copy's diff and not in any patch.)

After applying: rebuild the linux station and `cmake --build SIMesh/radio/build`.

## Open

- The pace at 99 stations with this set is bound by netgraph's sub-tick spin
  (the watchdog's 20 ms per occurrence); with the netgraph patch city99 runs
  at 51x in its first 300 s of wall, without it at 10x.
- soak30 is bound by its eight berlinmesh stations, ~41 wakes per second of T
  each and on their own phases (~330 of its ~380 barriers per second of T).
- In simd: the barrier's `sendto` (30–40 % of simd), JSON (12–16 %), and
  `next_instant`, a scan of every station per barrier (5–6 % at 40 stations,
  growing with the count; a heap would make it logarithmic).
- The firmware's own 1 Hz tasks (log, tcp, lxmf, storage) are what wakes an
  idle station now, each on its own phase within the second.


