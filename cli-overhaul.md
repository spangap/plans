# CLI overhaul

Design notes for reworking the spangap-core CLI from ambient-global dispatch to
commands-as-functions. Not scheduled; this is the target to converge on.

## Why

Today a command is `void cmd(const char* args)` that prints to an ambient sink.
Three consequences:

- **Every command re-parses its own arguments.** ~90 sites across the tree use
  `strcmp(args, …)`, `strncmp(args, …)`, `sscanf`, `strtok`. Quoting is handled
  in exactly one place (`parseArgs` in `spangap-net/esp-idf/src/net.cpp`, used by
  `net add` / `net join`) and nowhere else, so whether you can pass a WiFi
  password with a space in it depends on which command you're talking to.
- **Output has one destination and it is implicit.** `cliPrintf`/`cliWrite` write
  to the file-static `cliOut`, which resolves its actual target through a second
  global, `cliActiveSlot`. Nothing can run a command and collect its output.
- **Commands are bound to the cli task** by those globals, so trivial work
  (a `storageGet`) has to be marshalled onto one task instead of just being
  called.

`cliOut` barely varies — every client path sets it to `itsCliWrite`; only cron
sets `cronCliWrite`. The real per-client state is `cliActiveSlot`.

### Known defect this fixes

`cliInitSlot` sets `cliActiveSlot` to write its connect-time prompt and then
clears it to `-1` rather than restoring the previous value. `cliReadLine` and
`cliReadRaw` deliberately pump `itsPoll(0)` while parked so new connections are
still accepted — so a client connecting while a command sits at a password
prompt clears the parked command's slot. The read still works (the handle is
cached at entry) but echo stops, and when the command returns, the rest of its
output goes nowhere and cwd falls back to `s.cli.start_dir`.

## Target model

```c
int cmd(cli_ctx* ctx, int argc, char** argv, const std::string& in, std::string& out);
```

- **ctx** — the session, replacing the `cliActiveSlot`/`cliOut` globals. Carries
  the cwd, the color/terminal properties, and for a stream-registered command its
  ITS handles. `nullptr` means no session (see below). A stream command works
  through the ctx and leaves `in`/`out` untouched; a simple command ignores the
  ctx entirely and uses `in`/`out`. One signature covers both.
- **argv** — one quote-aware tokenizer for everything. Promote `parseArgs` from
  net.cpp into core. This also retires the naive `strchr(line, ';')` split in
  `cliProcess`, which today mis-splits `set x="a;b"`, and is a prerequisite for
  splitting on `|` without the same bug.
- **status** — `0` ok, non-zero failed. Unlocks `&&`/`||` in `cliProcess`, real
  failure reporting from cron (which currently logs every outcome at `info`
  identically), stop-on-error for `/state/boot` and `net_up`, `$?`, and a status
  for the serial RPC that doesn't require parsing prose. All 86 commands return
  void today, so the convention has to be enforced as they convert — a
  half-populated status space is worse than none, because scripts will trust it.
- **out by reference** — caller owns the buffer, so it can reserve once and
  reuse across calls, impose a cap, and detect a command that blows past it.
  `cliPrintf` inside a converted command appends to that call's `out`; that is
  the design, not a compatibility shim, so the ~950 existing print sites do not
  need to change.
- **in** — the input side, which is what makes a command a pipeline stage.

Registration carries `stackBytes`:

- `0` — run inline on the caller's task. The common case. No task, no ping-pong.
- non-zero — spawn a task. Implied by taking input, since a stage must run
  concurrently with its producer.

### Constraints to preserve

- **`cliPrintf` keeps its `int (*)(const char*, ...)` shape.** It is passed as a
  function pointer (`itsStatus(cliPrintf)`), and `storageList` takes a
  `cli_write_fn`. Modules hand their diagnostics to whatever printer they're
  given; that pattern is the one place a sink legitimately travels as a value,
  and it must keep working.
- **The help convention survives argv.** Every command answers `help` with one
  line and `-h`/`--help` with more, and `cliWantsHelp` covers all three
  spellings. That is threaded through all 86 commands and the `help` command
  itself iterates the registry calling each callback with `"help"`. argv-ifying
  must keep that contract, or the help listing dies wholesale.
- **`-O` becomes an ordinary argv flag** rather than a string compare against
  `args` (see `flashmon-framed-rpc.md`).

### Context

A `cli_ctx*` replaces `cliActiveSlot`/`cliOut`. `nullptr` means "no session:
give me the output, no colors, no frills."

That state already exists and is already exercised — it is what cron,
`/state/boot` and `net_up` run in today, and every ambient lookup already fails
soft in the right direction: `cliWantsColor()` false, `cliTermSize` 80x24,
`cliReadLine`/`cliReadRaw` return -1, `cliClose` a no-op, `cliGetCwd` falling
back to resolved `s.cli.start_dir`.

Two things need a decision rather than an existing fallback:

- **cwd has nowhere to live.** `cliSetCwd` returns false with no slot, and
  `cliGetCwd` re-resolves `start_dir` from storage every call, so `cd /x; ls` in
  a script silently ignores the `cd`. Fix: put `cwd[]` on the ctx — a
  session-backed ctx points into slot storage, a `nullptr`/capture ctx gets a
  local. `cliSetCwd`'s slot lookup then disappears.
- **Stream commands must be refused, not attempted.** `ssh` under cron enters
  its callback, hits `cliReadLine` → -1, and stumbles down an error path it was
  never designed for. With registration declaring its needs, `nullptr` ctx plus
  a stream requirement is a static mismatch: return an error, never enter the
  callback.

Side effect to be deliberate about: `cliPrintf` currently returns 0 when
`cliOut` is null, so boot-script output is dropped entirely. Give `nullptr`
contexts a buffer and that output suddenly has somewhere to go — decide whether
it lands in the log the way cron's does, or is discarded, or the caller picks.
Otherwise boot gets noisier by accident.

### Registry

`cliRegisterCmd` does a sorted `insert` into a plain `std::vector`, called 86
times from 23 repos on whatever task each init runs on, while the dispatcher
walks the same vector unsynchronized. An `insert` can reallocate. Latent today
(dispatch usually needs a connected user, though `net_up` runs `cliRunFile` on
net-up and can overlap a late registration); wider once any task can dispatch.

Mutex is enough. "Command not found if you're early" is acceptable semantics; a
caller that cares waits on `sys.boot_complete`, which already exists and already
has subscribers.

**Do not hold the lock across the callback.** Resolve the longest-prefix match
and copy the entry out under the lock, release, then invoke. The ssh interactive
relay is an unbounded loop alive for the user's whole session; holding the
registry lock there blocks registration for hours, and a command that registers
a command self-deadlocks unless the mutex is recursive.

### Re-entrancy

Any task may call a command, so two tasks may call the same command
concurrently. Pure argv-in/out-by-ref is safe by construction; a command holding
file-scope statics is not. That is a contract line the signature cannot enforce.

`storageGet` is already safe from any task — storage is guarded by a recursive
mutex.

### Stack

Every task stack in the tree is internal DRAM; PSRAM is used for buffers but no
task is created with a PSRAM stack. So each concurrent stage costs real DRAM at
3–4 KB. `top` already reports per-task high-water and a `D`/`P` classification,
so pick a default and tune outliers from measurement. This is a better knob than
today's situation, where `cliPrintf` had to move its format buffer to the heap
because everything shared cli's 6144-byte stack.

## Streams and pipes

Add an ITS primitive: a null-modem pair. Two handles, crossed for bidirectional,
or one stream with a read end and a write end for a pipe. ITS already has
everything else — point-to-point connections with per-direction stream buffers
from the PSRAM pool, plus the flow control a pipe needs (`itsSetTriggerLevel`,
`itsSetFreeNotify`, `itsWaitForSpace`, `itsSpacesAvailable`, `itsSendDrain`).
What the pair bypasses is only the name lookup and the accept handshake:
allocate two handle-table entries pointing at each other's buffers. Everything
downstream works unchanged because it only ever sees a handle.

Consequences:

- **There is one command form, not two.** Streaming is the general case: a
  command writes to its handle and cannot tell a pipe end from a session. The
  out-by-ref form is the convenience wrapper for short commands. `show` is the
  motivating case — its output is potentially too long to materialize, but it
  already goes through `storageList(cli_write_fn)`, so it converts to a handle
  with no restructuring.
- **No buffer cap.** `cat huge | grep x` runs in constant memory with real
  backpressure instead of failing at a limit.

**Execution.** A small wrapper runs each stage in its own task and exits when the
command returns. Pipeline depth is then bounded by heap, not by a worker pool.
The wrapper — not the command — closes its ends before `vTaskDelete(NULL)`, so a
stage that errors or returns early still produces EOF for its consumer.

**Close discipline.** Closing the write end early truncates. The CLI already
learned this once: session teardown defers until `itsSendIsEmpty(h)` rather than
disconnecting inline. The pair needs the same rule — a writer's close means "EOF
after the buffer drains", or readers lose the tail of every piped command,
intermittently and by length.

**Deadlock.** A stage that fills its pipe blocks until its consumer drains, so
stages must run concurrently. Task-per-stage satisfies this; a fixed depth-1
worker would cap pipelines at two stages.

**Filters.** None of `grep`, `head`, `tail`, `wc` exist among the 86 commands.
Once `in` exists they are a few dozen lines each. `show`, `log`, `pm`, `top` and
`ls` are the sources worth piping.

**Colors.** Non-final stages run with no session and no color, so `ls`'s inline
SGR escapes don't pollute matches. That is `isatty` semantics arriving for free.

## Cancellation

Ctrl-C cannot interrupt a running command today: the handler tears down the CLI
session and resumes the log, but the cli task is inside the command and won't
read input again until it returns, so the command runs to completion and the
session is lost as a side effect.

With pipes, closing the reader end *is* the cancel — the producer's next write
fails, the command returns, the wrapper cleans up. `cat hugefile` stops at its
next chunk knowing nothing about cancellation. SIGPIPE semantics for free.

**Never `vTaskDelete` a stage mid-command.** Storage holds a recursive mutex; a
task killed inside a `storageGet` wedges it permanently and takes the device
with it. Same for open files and heap. Cooperative only.

Commands that block without producing output — `sleep`, `date wait`, `ping` —
need an explicit `cliCancelled()` poll. Handful of one-line additions, opt-in.

## Ordering

1. argv + ctx + status + out-by-ref. One pass; it is the signature change, so all
   86 commands convert together. `cliPrintf` sites do not change. This is also
   where the `cliInitSlot` defect above disappears — with the session passed as
   an argument there is no global left to clobber.
2. Registry mutex.
3. ITS null-modem pair.
4. Task-per-stage wrapper; `stackBytes` at registration.
5. `|` in the dispatcher; `grep`, `head`.
6. Cancel-by-close, then `cliCancelled()` for the blocking commands.

Serial framing is independent and lands first — see `flashmon-framed-rpc.md`.
Nothing there is invalidated by this: when commands become functions, the RPC
handler calls one directly instead of dialling `cli`, and the wire format is
unchanged.

## Risk

The edit volume is mechanical. The real constraint is verification: 23 repos have
to build, and behaviour across 86 commands can't be confirmed without device
time. Expect "builds clean everywhere, the common commands verified on device,
long tail found in use."
