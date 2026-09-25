# SIMesh: framed RPC on the pty, descriptor interrupts on hw-linux, virtual time

Running notes for the reviewer. Three stages, each proven before the next.
Ports used for every run here: `--bind 0.0.0.0:9013 --ether 127.0.0.1:7002
--net 127.0.8.0/22` (second run 9014 / 7003 / 127.0.12.0/22), with `--run`
pointed into the session scratchpad so the user's `testbed/run/` is untouched.

Baseline before any change: `/usr/bin/python3 -m pytest SIMesh/radio/tests
SIMesh/ether SIMesh/testbed` → 45 passed. (`python3` on PATH is IDF's venv,
which has no pytest and no aiohttp; `/usr/bin/python3` has both.)

## Stage A: framed RPC over the station's pty

### Built

- `SIMesh/testbed/rpc.py` (new): `FrameDemux`, a byte state machine
  (SCAN → MATCH → HEAD → BODY) that holds a partial magic across reads,
  replays a false start (first held byte is text, the rest re-scanned), rejects
  an id outside 0x20..0xBF, resyncs when a magic appears inside a payload, and
  gives back a frame with no progress for 1 s (`expire`). `RpcClient`: one
  frame in flight (asyncio.Lock), id = flashmon's hash of the command in
  0x20..0xBF, retries reuse the id, a reply for an id nobody waits on is kept
  (bounded) for the retry, marker detection across reads, `wait_ready`
  (marker, then `show s.net.hostname` until it answers non-empty; no marker in
  20 s → one blind probe, Ctrl-C if unanswered, not up).
- `SIMesh/testbed/stations.py`: the pty drain feeds the demux; text goes to
  the log, the console websockets and the client's marker detector; frames go
  to the client. A resync timer (`call_later`) gives back a stalled frame.
  Console writes are buffered with `add_writer` when the pty does not take
  them whole, so a frame is never cut.
- `SIMesh/testbed/kinds/reticulous.py`: wait_up / run / setup / flush /
  transport all over RPC. Setup sends each line as its own frame; a line whose
  reply came back at ≥ 4.5 s (the 5 s exec bound) is followed by probes until
  the CLI answers again; `lora up` is confirmed by `show s.lora.0.enable` = 1.
- `SIMesh/testbed/setup.py` deleted (the TCP CLI client); its one helper,
  `parse_setting`, moved to rpc.py. The file had an uncommitted docstring edit
  by the user; the sentence it added (setup lines only on an empty store,
  `lxmf create` is an action) is already stated in README/INTERNALS.
- `SIMesh/testbed/simd.py`, `scenario.py`: `--run DIR` so a second testbed
  has its own run directory. Without it a second simd appends to the first's
  record and wipes its stations' state on load — needed to keep off the
  user's `testbed/run/`.
- `SIMesh/testbed/test_rpc.py` (new): 17 tests.
- `spangap-net/esp-idf/src/host/net_host.cpp`: removed
  `storageDefault("s.net.cli_port", 8081)`.

### The TCP CLI exception

`net_host.cpp` (host-only file) seeded `s.net.cli_port = 8081`, with a comment
saying it existed for the launcher, the harness and a person with nc. On a chip
the default is 0 (closed, `net_relay.cpp` config tree). Once open, the TCP CLI
takes no login on either target (net sends its own connect descriptor, which
the CLI reads as LINE mode without the login gate), so login is not a host
exception. The default was the only host-only difference, and it existed only
for the testbed, so it is removed. `set s.net.cli_port 8081` opens it, as on a
board.

Not changed, noted: `reticulous/sim/` is the superseded predecessor of the
testbed (still tracked in the reticulous repo, nothing runs it) and its
`setup.py` speaks the TCP CLI; it would need that setup line now.

### Decisions

- Marker regex matches `serial] framed rpc v1` as well as
  `serial: framed rpc v1`: the logger turns `serial: ` into a `[serial]` tag,
  so the literal line in framed-rpc.md never appears in a station's log.
  (flashmon matches the literal `serial: framed rpc v1`; on a board it would
  therefore always take the probe path. Not changed here — outside scope.)
- Query timeout 8 s per try (5 s exec bound + margin), two tries: a retry only
  happens when the first frame got no answer at all in 8 s, so a slow
  non-idempotent line such as `lxmf create` is not run twice by a retry.

### Verification

- `pytest SIMesh/testbed/test_rpc.py` → 17 passed (frames between log text,
  every split size 1..64, a magic split across reads, false starts
  `F5 x`/`F5 S`/`F5 SG`/`F5 SG 02`/`F5 F5 SG x`, a magic inside a false one,
  an out-of-range id, a corrupt length resynced on the next magic, a stalled
  frame given back after 1 s, a log line inside a frame, 200 random streams,
  id hash = flashmon's, marker split across reads, late reply answers the
  retry, serialised queries, no marker → one probe then Ctrl-C).
- Whole suite → 62 passed.
- End to end with the existing ELF, then again after rebuilding with
  `net_host.cpp` changed (build target: hw-linux, the command in
  SIMesh/README.md; incremental, 8 s):
  `simd.py --bind 0.0.0.0:9013 --ether 127.0.0.1:7002 --net 127.0.8.0/22
  --run <scratch>/runA --stagger 3`, `scenario_load mixed`.
  - all five stations `up` in ~6 s after start (three reticulous by RPC,
    two berlinmesh by rncfg); setup lines ran (hostname, lxmf identity, sync
    0x12 read back per station); `transport: true` on the three reticulous
    stations in the snapshot's node list;
  - Run command `show s.net.hostname` → `alpha`, `bravo`, `charlie`;
  - `seq.py --only ANNOUNCE` on the run's record: 46 announce rows;
  - `run/nodes/{alpha,bravo,charlie}/log`: 0 bytes 0xF5, 0 `CLI mode`, the
    marker once each;
  - Reset alpha: restarting → starting → up in 0.7 s, marker re-seen;
  - after the rebuild: `nc -z 127.0.8.5 8081` refused, port 80 answers;
    `set s.net.cli_port 8081` over RPC, then `nc` gets `alpha $`.

## Stage B: descriptor interrupt controller in hw-linux

### Built

- `hw-linux/esp-idf/src/fdwait.cpp` (new): an epoll pthread started from a
  constructor before `main()` with every signal blocked; per-task waiter slots
  (64); per-fd atomic waiter bitmask; one-shot epoll registration only for the
  length of a wait. Readiness → bits into an atomic pending set →
  `kill(getpid(), SIGUSR2)`. The SIGUSR2 handler (sa_mask full, SA_RESTART)
  runs on the running task's thread; it wraps its work in
  `vPortEnterCritical`/`vPortExitCritical` (the tick handler bumps the static
  nesting count directly; this is the public way to do the same), notifies
  FromISR, and calls `vPortYieldFromISR` when a higher-priority task was woken.
  - `select()` via `-Wl,--wrap=select -Wl,-u,__wrap_select` (the `-u` was
    needed: the first link failed with `undefined reference to __wrap_select`
    from libiface-lora.a because libhw-linux.a had already been scanned).
    Blocks on notification index 1; POSIX semantics.
  - `hwLinuxWait()`: select that also returns when the task gets an index-0
    notification, waiting with `xTaskNotifyWaitIndexed(0, 0, 0, …)` so the
    count stays for the task's own `ulTaskNotifyTake`; a ready fd posts an
    `eNoAction` notification to such a waiter.
  - fallback to the port's polling style (zero-timeout check + `vTaskDelay(1)`)
    before the scheduler runs or with all 64 slots taken.
- `hw-linux/straddle.yaml`: `CONFIG_FREERTOS_TASK_NOTIFICATION_ARRAY_ENTRIES=2`.
- `hw-linux/esp-idf/CMakeLists.txt`, `include/hwlinux.h`: the source, the link
  options, the declaration.
- `spangap-core/esp-idf/src/cli.cpp`: on a non-USB-Serial-JTAG console with
  `hwLinuxWait` present (weak), the serial task waits on stdin or a
  notification: forever in log mode, 50 ms while a CLI session is open (the
  CLI task sets the auto-resume latch without notifying) or a frame is
  part-assembled. Chip path unchanged.
- `spangap-net/esp-idf/src/net_priv.h`, `net_relay.cpp`, `net.cpp`,
  `host/net_host.cpp`: the relay's block point is `netRelayWait()`, defined
  per link backend. Chip (net.cpp): exactly the old select 10 ms /
  vTaskDelay 10 ms. Host: `hwLinuxWait(…, 1 s)` — sockets or ITS
  notification, and at most 1 s because `epOpenAll()` notices a changed
  `s.net.*_port` only by polling storage on each pass.
- `SIMesh/radio/backend/esp-idf/services.cpp`, `iface-lora/…/ether_task.cpp`:
  the ether reader's select has no timeout.
- Left alone: `spangap-web/…/webrtc_task.cpp` (its UDP socket is always open on
  the host, so its `vTaskDelay(1)` + `itsPoll(1)` loop runs at 100 Hz; the task
  said to leave it unless trivially blockable when no socket is open, which
  never happens here).

### Decision: signal injection, not the tick hook

`port.c`'s masking discipline can be met from outside it: every task thread
has all signals unblocked only while it is the running task and outside a
critical section, so a process-directed signal is delivered exactly where
SIGALRM is. The only port-private piece the tick handler touches is the
static `uxCriticalNesting`, and `vPortEnterCritical()` / `vPortExitCritical()`
change it the same way. So no tick-hook fallback and no edit to /opt/esp/idf.
`vPortExitCritical` at the end of the handler unblocks signals inside the
handler, so a pending tick can nest there; that is equivalent to the tick
arriving the instant the handler returned.

### Verification

Build: hw-linux target (incremental, 12 s after the link fix). `nm`:
`__wrap_select` and `hwLinuxWait` present, IDF's `select` gone (gc-sections).

Idle CPU, mixed.yaml, three reticulous stations, /proc/<pid>/stat deltas over
30 s, twice each (cpu.py in the scratchpad):

| | alpha | bravo | charlie |
|---|---|---|---|
| before (Stage A build) | 2.80 / 2.70 % | 2.80 / 2.73 % | 2.60 / 3.17 % |
| after | 1.50 / 1.37 % | 1.73 / 1.47 % | 1.67 / 1.37 % |

`strace -f -c -p <alpha>` for 10 s (strace installed into the container with
apt; attaching works as the same user, not under sudo):

| syscall | before | after |
|---|---|---|
| pselect6 | 4000 | 36 |
| rt_sigprocmask | 41312 | 10336 |
| futex | 13943 | 8189 |
| rt_sigreturn (the tick) | 1000 | 1001 |
| clock_nanosleep (idle task, EINTR each tick) | 975 | 956 |
| recvfrom (webrtc task, EAGAIN) | 500 | 500 |
| epoll_pwait | — | 2 |

What remains periodic: the 100 Hz tick, the IDF idle task's 15 ms
`clock_nanosleep` interrupted by every tick, and the webrtc task's UDP drain.

Functional, same run: all five stations up; Run command answers; `set
s.net.cli_port 8081` then `nc 127.0.8.{5,6,7} 8081` answers `show
s.net.hostname` on each; `curl -H 'Host: alpha.sim.localhost'
127.0.0.1:9013/` → 200, and `2.sim.localhost` → 200; frames on the air
(seq.py); `rnpath` 4 paths per station; `lxmf send` alpha → charlie: link
established, delivered, proof back, alpha `nothing unfinished`, charlie 1
conversation.

Found later, during Stage C (fixed in `fdwait.cpp`): the SIGUSR2 handler
returned without touching the pending set when `xTaskGetSchedulerState()` was
not RUNNING, which includes SUSPENDED (any task inside vTaskSuspendAll). The
bits stayed pending with no signal left to deliver them: a lost wakeup. In
real time the relay's 1 s bound and the next readiness hid it; in virtual time
it stopped the run (a station's ether reader never woke, Recv-Q 896 B, T
stood). The handler now only skips before the scheduler starts; the FromISR
calls queue the woken task and the switch happens at resume.

## Stage C: virtual time

### Built

- `SIMesh/ether/ether.py`: `RealClock` / `VirtualClock` (heap keyed by
  (instant, station id, order)); `--time real|max|<k>x`; the barrier
  (`kick` → `flush_pending` → `next_instant` → `paced` → `step_to`), a
  busy count instead of scanning; per-station `seq` on every message sent in
  virtual time, `idle {seq, until}` accepted only for the current seq;
  state/tx held while T stands and taken in station order; `expect`/`leave`
  for simd; `sleep()` on the run clock; late tx clamped to T and logged;
  record first column T; run/idle not recorded; `welcome {t, mode, rate,
  epoch, seed}`; a station asking for T itself 64 times running is given a
  10 ms tick instead.
- `SIMesh/radio/src/conductor.{h,cpp}` (new): mode from SIMESH_TIME, T,
  model timers in T, host wakes in node time, f/f⁻¹ from
  SIMESH_CLOCK_PROFILE (piecewise linear; identity when absent), idle
  reporting once per grant (again only when until moves earlier), a busy
  watchdog (timerfd, 20 ms wall) that reports idle anyway, the shim attach
  (dlsym `simclock_attach`).
- `SIMesh/radio/src/ether_link.cpp`: welcome/run/timed rx handling; own
  datagrams retract idle; idle sender.
- `SIMesh/radio/include/simradio.h`, `simclock.h`: the clock ABI.
- `SIMesh/radio/shim/simclock.c` → `build/libsimclock.so`: clocks, sleeps,
  itimer, descriptor-wait timeouts, cond timed waits, thread census.
- `SIMesh/radio/backend/esp-idf/services.cpp`: own CLOCK_MONOTONIC via
  syscall (never esp_timer), the hw-linux clock hooks.
- `iface-lora`: links SIMesh/radio (sources resolved through the realpath of
  its CMakeLists), `virtual_sx126x.*` and `ether_task.*` deleted,
  `VirtualHal` over `simradio_*`; delayMicroseconds sleeps instead of spinning.
- `hw-linux`: esp_timer reads `hwLinuxClockUs` and reports its head to
  `hwLinuxClockWake`; `hwLinuxClockDue` kicks its task; `vApplicationIdleHook`
  → `hwLinuxClockIdle`; onStart → `hwLinuxClockStart`;
  `CONFIG_FREERTOS_USE_IDLE_HOOK=y`; ESP_LOG through write(2) until the
  platform's logger takes it (see below).
- `SIMesh/testbed`: `--time`; stations register with the ether; start
  stagger, restart delay, settle wait and command spread on the run clock;
  `command` takes `name` and `after`; "is up at t" and "T stands … waiting
  on" log lines; `clock` messages to the page; `compare.py` (new); berlinmesh
  kind retries rncfg on timeout in a virtual run; seq.py reads T stamps.
- UI: header shows the mode and pace; rings scaled by the pace.

### Decisions

- hw-linux names the clock hooks itself (`hwLinuxClock*`), declared weak and
  absent-means-real-time; the radio backend defines them. The task said to
  declare the library's functions weak in hw-linux; naming them after the
  board keeps SIMesh names out of hw-linux and keeps "hw-linux depends on
  nothing".
- `idle` carries `seq` (not only `until`): without it an idle sent before a
  station processed a grant is indistinguishable from one sent after.
- Every ether→station message in virtual time is a grant (carries t and seq),
  not only `run`: a station told of a frame must re-report idle, because the
  frame arms model timers that move its `until`.
- The mode is also in the environment (SIMESH_TIME): the FreeRTOS port calls
  setitimer before the station can reach the ether. welcome still states it;
  a mismatch is logged as an error.
- Busy watchdog: a station whose tasks never all block (a spin, a wait the
  idle hook cannot see) is given time at the pace it actually runs instead of
  stopping the run. Found necessary on the first run (log deadlock, below).
- stdio deadlock: the radio backend logs through ESP_LOG, which before the
  platform's logger installs its vprintf goes to stdio; a signal-driven task
  switch while a task held the stdout lock left the reader task blocked on it.
  hw-linux now installs a write(2) vprintf from a constructor (the SIMesh
  INTERNALS rule "the console writes with write(2)"), and the welcome is
  applied before anything is logged.
- Clocks read 0 before the library attaches (not the host's clock): Rust's
  Instant saturates, so a clock that jumped backwards at attach froze
  berlinmesh's uptime at 0.000 for the whole run.

### After the session restart

- The scratchpad (helper scripts, earlier run directories) was gone; the
  scripts were rewritten (sim.sh, killmine.sh, simctl.py, drive.py,
  soakdrive.py, soakstat.py, cpu.py, clockwatch.py, pair.sh, starts.py).
  strace and gdb had to be reinstalled (apt).
- Rebuild attempt (hw-linux target) FAILED, not on my code: the user's
  uncommitted UART-console work in spangap-core/esp-idf/src/cli.cpp includes
  `driver/uart.h` under `CONFIG_ESP_CONSOLE_UART`, which the linux target
  sets and which has no such header there:
  `cli.cpp:19:10: fatal error: driver/uart.h: No such file or directory`.
  Left untouched (the user's work). `find -newer reticulous.elf` over every
  firmware source I own shows nothing changed since the last good build
  (17:46, hw-linux, which contains all my firmware edits: fdwait lost-wakeup
  fix, clock hooks, migration), so that ELF is the one verified below. Every
  later fix in this stage is in SIMesh (python, the shim), not in firmware.
- pytest SIMesh/radio/tests SIMesh/ether SIMesh/testbed: 78 passed.

### Verification

Unit: `pytest ether/test_ether.py radio/tests/test_conductor.py
radio/tests/test_shim.py` → 38 passed (9 virtual ether cases: welcome,
barrier holds, stale idle ignored, retraction, rx at exact instants in T,
late tx clamped, station-order tiebreak, restart, 10x pacing; 6 conductor
cases incl. the non-identity profile; the shim stand-in: 25 ms sleeps land at
25 000, 50 000 … exactly, ≥ 19 SIGALRMs, time() = epoch + T, no watchdog).

mixed.yaml (3 reticulous with transport + 2 berlinmesh), real on 9013 and max
on 9014 side by side:
- all 5 up in both; `lxmf send` alpha→charlie delivered with proof in both
  (real: `DIRECT delivered mid=o_19642_60e0`, max: `mid=o_276530_5990`);
  rnpath 3 paths (real) / 6 paths (max, run longer in T) per station;
- max ran at 37x real time (T 3558 s after ~95 s wall), 917 680 barriers
  (≈258 per second of T); station CPU real 0.40 % each, max ~35 % each,
  simd 28 %.

### Iterations (real vs max)

1. smoke7, before the restart: max run stopped T for good at 43.03 s waiting
   on n06; its ether socket had 896 B unread. Cause: fdwait's SIGUSR2 handler
   dropped wakeups while the scheduler was SUSPENDED. Fixed in fdwait.cpp
   (that build is the 17:46 ELF).
2. smoke7, same session: in max mode the lora task sat in a 10 s
   settle window: every reticulous first announce ~10 s later in T than in
   real. Traced with gdb on `cfgArmSettle` in a 0.2x paced run: the setup
   line edits landed at esp_timer 11.17 s instead of ~1 s. The `lxmf create`
   setup line took 10.5 s wall and 5 s of T, and a clock watch showed T
   crawling at 0.16x during the berlinmesh boot: 16 500 barriers per wall
   second advancing T 10 µs each. Cause: the Rust SX1262 driver's
   `delay_ns` → `thread::sleep` of microseconds, each one a wake and so a
   barrier. Fix: the shim rounds every wait's end up to the next whole
   millisecond of node time (RESOLUTION_US). After it: all 5 up by T 7.6 s,
   T reaches 40x within 3 s of wall.
   Also changed on the way: the testbed's polls between probes (wait_up,
   the settle confirm, berlinmesh detect) sleep on the run clock
   (`Kind.pause`), not the wall.
3. mixed after 2: first announce per station now matches within the
   difference of the run's zero (real's first hello is a berlinmesh station
   1.8 s after load; reticulous stations say hello at radio start in real,
   at board start in virtual): every station announced 12.4 s (real) vs
   14.2 s (max) from the first hello. compare.py gained `--starts` to put
   both runs on the scenario load.
4. A max run hung at boot with T at 0: a task switched out (SIGUSR2) inside
   the shim's lazy `dlsym` held the dynamic linker's lock; the ether reader,
   resolving `recv` through the shim, blocked on it for good (gdb: idle task
   in `do_lookup_x` under `fdIrq`, reader in `__lll_lock_wait` on
   `_rtld_global`). Fix: the shim resolves every C library function in its
   constructor, before main(). The conductor's own dlsym (the shim attach, at
   board start) now holds signals off around the call — that one is in
   radio/src/conductor.cpp and is NOT in the tested ELF, which could not be
   rebuilt (see above); the Rust station and the ctypes tests have it.
5. smoke7 pass 1 (real `smoke7-real5`, max `smoke7-max5`; sends
   gw02→n04 @200 s, n06→gw02 @300, n03→n06 @400, n05→gw02 @500; gather at
   1500 s run time):

   | | real | max |
   |---|---|---|
   | wall time for 1500 s of run | 1501 s | 56 s |
   | all 7 up (run s / wall s) | 56.0 / 56.8 | 55.2 / 3.2 |
   | first announce gw02 n03 n04 n05 n06 | 21.6 30.3 38.9 47.5 56.1 | 21.6 30.1 38.7 47.4 55.8 |
   | announces originated per LoRa station | 4 each | 4 each |
   | forwarded gw02 n03 n04 n05 n06 | 10 10 11 9 7 | 10 9 10 6 7 |
   | frames all / ANNOUNCE / DATA / LINKREQ / PROOF | 238/67/81/12/38 | 247/62/88/17/44 |
   | receptions clean / crc | 361 / 23 | 359 / 30 |
   | pairs with a path, by hops 1/2/3/4 | 8/6/4/2 | 8/6/4/2 |
   | first 1/2/3/4-hop path (run s) | 30.9/42.6/333.9/479.2 | 30.7/42.3/279.7/514.6 |
   | messages proven delivered | 4/4 (269, 419, 515, 1085) | 4/4 (290, 1151, 558, 1030) |
   | rnpath at 1500 s | same hop profile per station, max gw02 one extra 4-hop | |

   Passes: every milestone reached in both, counts of the same order, no
   station different in kind. (compare.py, with `--starts` from the drivers'
   load instants.)
6. Found while driving soak30 (both modes, not virtual time): framed RPC runs
   no command line longer than about 128 bytes. A 165-byte `echo` and every
   `lxmf send` with 120+ characters of text answer '' after the 5 s exec bound
   (real mode: 5.00 s wall), and the command never runs (no mid in the
   sender's log). The same `lxmf send` over the TCP CLI answers `queued`.
   The bound is in spangap-core cli.cpp (rpcRun), the user's file under active
   edit; not changed. The soak driver sends over the TCP CLI (`set
   s.net.cli_port 8081` over RPC first), as run D's driver did, and still
   schedules each send at its run instant (a `show` over simd with `after`,
   then the TCP send).
   Reproduced exactly on the 19:14 ELF, real time, smoke7 station gw02,
   `scratchpad/rpclen.py 9013 gw02` (`echo x…` through simd's `command`,
   i.e. one framed-RPC frame): lines of 100, 120, 125 and 126 bytes answer in
   0.02 s; 127, 128, 129, 130 and 165 bytes answer '' after 5.00 s. rpcRun
   feeds the CLI `<cmd>;\n`, so the boundary is where that reaches 129 bytes;
   the CLI's LINE-mode receive reads 128 bytes at a time (`char buf[128]`
   in the CLI task loop). Cause not pinned further; not fixed (the user's
   file). flashmon's framed RPC goes through the same rpcRun.
7. soak30 `soak30-max2` (17:46 ELF, TCP sends, run D's recipe: warm 2040 s,
   361 messages every 4–6 s of T, drain 600 s): all 40 up at run 60.4 s,
   wall 13.8 s; 4448 s of T in 686 s of wall (6.5x). Delivery 46/361 (13%),
   by route hops 1: 21/37 (57%), 2: 16/36 (44%), 3: 8/63 (13%), 4: 0/51,
   5+: 1/174. Run D (real, older firmware): 59/360 (16.4%), by hops
   25/26 (96%), 19/37 (51%), 11/45 (24%), 4/49 (8%), 0 beyond. Everything but
   1 hop is of run D's order; 1 hop at 57% against 96% is not.
   Traced (record + sender logs, 1-hop pairs): links to a neighbour failed
   with SUPE READY frames never received. iface-lora's virtual_hal
   `delayMicroseconds` had become `vTaskDelay(pdMS_TO_TICKS(us/1000)+1)` in
   the migration onto radio/: every sub-millisecond wait RadioLib makes
   around a mode change was a whole tick (10 ms), in virtual time and real,
   with the radio out of receive for it. Fix: under one tick it yields
   (`taskYIELD`), since the model completes every command at once and BUSY is
   never busy; a longer wait sleeps.
   Build: the workspace build (hw-linux) still fails on the user's cli.cpp
   (`driver/uart.h`). So the station was built in a scratch workspace
   (`scratchpad/ws`): a symlink to every straddle except spangap-core (its
   `git archive HEAD` plus only my cli.cpp hunks: the weak hwLinuxWait and
   its idle-branch wait) and reticulous (a copy without build directories).
   `spangap build reticulous/reticulous --with spangap/hw-linux …` there,
   target **hw-linux**, 19:14, OK → `ws/reticulous/esp-idf/build.linux/
   reticulous.elf`. That ELF also has the conductor's signal-masked dlsym.
   simd runs from `ws/SIMesh/testbed` by that absolute path, so the
   scenarios' relative ELF paths resolve to the scratch ELF (checked in
   /proc for every station). Every comparison from here is on it; the
   "twice in a row" count starts again.
8. smoke7 pass A on the 19:14 ELF (`smoke7-real7` on 9013, `smoke7-max7` on
   9014, same four sends, 1500 s): **passes.**

   | | real | max |
   |---|---|---|
   | wall for 1500 s of run | 1501 s | 56 s |
   | all 7 up (run s / wall s) | 56.0 / 56.8 | 56.8 / 3.0 |
   | first announce gw02 n03 n04 n05 n06 (from first hello) | 20.4 29.0 37.5 46.1 54.6 | 20.3 29.0 37.5 46.1 54.6 |
   | forwarded gw02 n03 n04 n05 n06 | 10 11 11 9 7 | 9 8 12 11 7 |
   | frames all / ANNOUNCE / DATA / LINKREQ / PROOF | 236/68/78/13/37 | 240/67/85/12/38 |
   | receptions clean / crc | 347 / 39 | 372 / 24 |
   | first 1/2/3/4-hop path | 29.6/40.9/394.5/536.8 | 29.6/40.6/332.0/557.3 |
   | pairs with a path by hops 1/2/3/4 | 8/6/4/2 | 8/6/4/1 |
   | messages proven delivered | 4/4 (269, 438, 572, 1019) | 4/4 (269, 478, 573, 1072) |
   | rnpath at the end | gw02 has one 4-hop path more in real; n03 one 3-hop more in real; n05 one 2-hop more in max | |

   (`compare.py … --starts 69286.859,0 --logs … --cli …`, output kept as
   scratchpad `smoke7-cmp7.txt`.)
9. soak30 `soak30-max3` on the 19:14 ELF, run D's recipe as in 7: all 40 up
   at run 62.4 s, wall 13.5 s; 4447.5 s of T in 696.7 s of wall (6.4x).
   Delivery **71/361 (20%)**, by route hops **1: 37/37 (100%), 2: 16/36
   (44%), 3: 14/63 (22%), 4: 4/51 (8%), 5+: 0/174**; by class short 33/201,
   two-frame 22/94, over 500 B 16/66. Run D: 59/360 (16.4%); 96/51/24/8/0 %
   by hops. **Matches run D hop for hop**, within a message or two at each
   count, with the firmware of today rather than run D's. The 1-hop fix of 7
   is confirmed. Barrier rate 2865 per wall second, 475 per second of T;
   32 reticulous stations 291 % of one core together (9.1 % each), 8
   berlinmesh 8.8 % together, simd 58 %.
10. smoke7 pass B on the 19:14 ELF (`smoke7-real8`, `smoke7-max8`): **passes;
    with pass A (8) that is two in a row.** All 7 up 56.1 / 57.1 run s (wall
    56.6 / 3.0); first announces within 0.2 s station for station; frames
    239 / 252 (ANNOUNCE 68/71, DATA 85/80, LINKREQ 12/21, PROOF 35/42);
    pairs with a path by hops 8/6/4/2 in both; first 3-hop path 362.6 /
    290.9, 4-hop 507.4 / 497.5; 4/4 messages proven delivered in both (real
    269 472 575 1078, max 208 561 517 1079); rnpath at the end differs by one
    path at n05 and n06. 1500 s of run in 76.7 s of wall.
11. cityF at max, `cityF-max1`. Measured first: the host has 16 cores and
    soak30's 40 stations took ~3.6 of them, so 70 fit. The scenario names the
    internet station by the user's address (`tcp peer add 127.0.4.5:4965`,
    nine lines), which from my network would have reached the user's live
    testbed, so the run used a scratch copy (`scratchpad/wsF/.../cityf.yaml`)
    with that one address changed to 127.0.12.5; `wsF` is `ws` with its own
    scenarios directory. The ten Python hosts of run F were not run (they are
    wall-clock processes outside the run; their stations are there, with the
    RNode TCP door open and nobody on it), and neither were run F's manual
    `announce now` / `lxmf announce` warm-up steps. Traffic: soakdrive, warm
    968 s (run F's 16 minutes from load to first message), 623 messages
    every 4–6 s of T for 3120 s between random Reticulous stations (60
    senders), run D's size mix, drain 600 s.
    - All 70 up at run 64.3 s, wall 25.1 s. 4690.9 s of T in 1300 s of wall
      (3.6x). Barrier rate 2004 per wall second, 556 per second of T. 60
      reticulous 298 % of one core together (5.0 % each), 10 berlinmesh
      7.4 % together, simd 57 %. No station exited.
    - Delivery **195/623 (31%)**; by route hops (radio graph through
      transports, plus each `tcp peer add` as one hop to the internet
      station) **1: 34/34 (100%), 2: 30/44 (68%), 3: 39/76 (51%), 4: 42/106
      (40%), 5+: 50/363 (14%)**. Run F: 155/512 (30.3%); 0–1 100%, 2 70%,
      3 71%, 4 38%, 5–6 13%, 7+ 3%. Of run F's order at every hop count;
      run F's own hop counting (analyzeF.py) is gone with its scratchpad, so
      3 hops differing by 20 points may be the counting as much as the run.
    - Against `run/record_F.tsv` (read only), both from the scenario load,
      first 4088 s (run F's first suspend): frames 28 528 / 31 640 (ANNOUNCE
      4333/3620, DATA 7646/9237, LINKREQ 3959/5055, PROOF 3223/4035, SUPE
      7860/8299), receptions clean 90 121 / 100 663, crc 14 128 / 16 631;
      pairs with a path by hops 1–11: 296/289, 272/261, 346/318, 393/409,
      266/298, 313/289, 211/219, 81/106, 34/45, 3/11, 0/3. More data frames
      in max, from 623 messages in 3120 s against run F's 5 s cadence; fewer
      announces, from the warm-up announces run F sent by hand.

### Speedup, real against max, on the 19:14 ELF

Wall seconds after the scenario load at which each milestone was reached.
Real is the run's own time by construction (smoke7-real7/8, and run D / run F
for soak30 / cityF, which ran real on older firmware). Milestones are from
compare.py, first delivery from the senders' logs.

| scenario (stations) | milestone | run s | real wall | max wall | speedup |
|---|---|---|---|---|---|
| smoke7 (7) | last station up | 57.1 | 56.6 | 3.0 | 19x |
| | first announce from every LoRa station | 54.6 | 54.6 | 2.8 | 19x |
| | first 1-hop / 2-hop path | 29.6 / 41.0 | ≈30 / 41 | 1.2 / 1.9 | 25x / 22x |
| | first message delivered | 207.6 (max), 268.7 (real) | 268.7 | 9.0 | 30x |
| | whole run | 1500 | 1500 | 56–77 | 20–27x |
| soak30 (40) | last station up | 62.4 | ≈62 | 13.0 | 4.8x |
| | first announce from every station | 70.5 | ≈70 | 14.8 | 4.8x |
| | first 1/2/3-hop path | 12.2 / 27.1 / 57.5 | ≈same | 1.2 / 4.1 / 11.9 | 10x / 6.6x / 4.8x |
| | first message delivered | 2048 | ≈2048 | 301.5 | 6.8x |
| | whole run | 4447 | 4447 | 696 | 6.4x |
| cityF (70) | last station up | 64.3 | 100 (run F, 90 s stagger) | 25.0 | 2.6x |
| | first announce from every station | 69.1 | 100.7 (run F) | 27.3 | 2.5x |
| | first 1/2/3-hop path | 13.3 / 24.0 / 19.6 | 14.9 / 29.4 / 23.9 (run F) | 2.0 / 6.0 / 4.4 | 7.5x / 4.9x / 5.4x |
| | first message delivered | 988.5 | ≈990 | 258.3 | 3.8x |
| | whole run | 4690 | 4690 | 1299 | 3.6x |

| scenario | barriers / wall s | barriers / T s | host CPU in max |
|---|---|---|---|
| smoke7 | 2704 | 101 | (7 stations; not sampled) |
| mixed (5) | — | 258 | stations ~35 % each, simd 28 % |
| soak30 | 2865 | 475 | 32 reticulous 291 %, 8 berlinmesh 8.8 %, simd 58 % |
| cityF | 2004 | 556 | 60 reticulous 298 %, 10 berlinmesh 7.4 %, simd 57 % |

Real-mode CPU for comparison: 0.40 % per reticulous station (mixed, real).
The speedup falls with the station count because every barrier waits for
the slowest station and simd, one Python thread, runs every barrier: simd
sits at ~57 % of a core in both larger runs, and the stations together use
3 of 16 cores. The ceiling is the barrier rate per wall second (~2000–2900),
not the host's cores.
