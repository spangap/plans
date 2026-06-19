# Plan: SSH-based `spangap cli` with monitor-owned bridge

## Context

Today `spangap cli` reaches a device's **TCP CLI** (port 2323) over a host-side LAN
relay (`ensure_forwarder` in `spangap-outside`, Darwin-only) keyed off a single
`.spangap-host` file / `$SPANGAP_HOST`, started *lazily* on any container command. The
relay forwards `host.docker.internal:{2323,22}` → device; the in-container `cli` dials
`host.docker.internal` (falling back to the device host directly on native Linux).

We want `spangap cli` to prefer **SSH** to the device (the `sshd` straddle, openssh
`ssh-ed25519` auth), fall back to the existing **TCP CLI**, and bootstrap a key + device
authorization when neither is configured. Bridge ownership moves from the lazy
`ensure_forwarder` to **`spangap monitor`** — it opens the bridge on start (from the
config files) and closes it on exit. Two new workspace files replace `.spangap-host`:

- **`.spangap-ssh`** — `<host-or-ip>:<port>` (ssh; default port `22`)
- **`.spangap-tcp`** — `<host-or-ip>:<port>` (TCP CLI; default port `2323`)

Decisions confirmed with the user:
- **Replace entirely**: monitor is the sole bridge owner; remove the lazy
  `ensure_forwarder` + `.spangap-host`/`$SPANGAP_HOST` path. `spangap cli` reads only the
  two new files. (Consequence: in-container `spangap cli` works only while a host
  `spangap monitor` is running — the standard host-side process anyway.)
- **Change firmware default**: `sshd` comes up enabled by default (safe — no access
  without an authorized key/password).

Note on the original request wording: the codebase uses `host.docker.internal` (not
`host.docker.local`), and ssh takes `-p <port>` (not `host:port`) — the plan uses the
corrected forms.

## `spangap cli` resolution order (both inside and outside)

1. **`.spangap-ssh` exists** → parse `host:port` (default `22`) → exec
   `ssh -p <port> -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 <connect_host> [<cmd and args>]`.
   With args → one-shot remote command (sshd runs it through the device `cli` and exits);
   no args → interactive shell. The default identity `~/.ssh/id_ed25519` is used
   automatically; sshd ignores the username so none is specified.
2. **`.spangap-tcp` exists** → parse `host:port` (default `2323`) → use the **existing**
   socket code (drain-until-prompt one-shot / `selectors` interactive loop), unchanged
   except the host/port come from this file.
3. **Neither exists** → bootstrap (below).

`connect_host` differs by side:
- **inside** (`spangap-inside`): try `socket.getaddrinfo("host.docker.internal", port)`;
  use `host.docker.internal` if it resolves (Docker Desktop, bridge → monitor), else the
  device host directly (native Linux). This mirrors the existing TCP logic at
  `spangap-inside:1476-1481` — apply it to ssh mode too.
- **outside** (`spangap-outside`): always the device host directly (the host is on the
  device's LAN; no relay needed).

### Bootstrap (neither file exists)
1. `[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -q`
   (generate only if absent; no prompts).
2. **Prompt** the user on stdin for the device hostname or IP; write
   `.spangap-ssh` = `<entered-host>:22`.
3. Read `~/.ssh/id_ed25519.pub` and print serial-monitor instructions:
   - `sshd add <contents of id_ed25519.pub>`
   - "sshd is enabled by default; on an older build also run: `sshd enable`"
   - "If `sshd` isn't in your build, rebuild with: `spangap build --include spangap/sshd`"
4. Exit 0 (the next `spangap cli` invocation takes the ssh path).

A `-h <host[:port]>` convenience is retained but now (re)writes **`.spangap-ssh`**
(ssh is the primary mode), replacing the old `-h` → `.spangap-host` behavior.

## Files to modify

### `/straddles/spangap/build-system/spangap-outside`
- **`cli)` case (~784)**: rewrite per the resolution order above (ssh mode = exec ssh to
  device directly; tcp mode = existing inline python socket block, host/port from
  `.spangap-tcp`; bootstrap otherwise). Drop the `-h`→`HOST_FILE` sticky and the
  `SPANGAP_HOST` fallback; `-h` now writes `.spangap-ssh`.
- **Replace `ensure_forwarder` (964-997) with `start_bridge` / `stop_bridge`**
  (Darwin-only; no-op elsewhere). `start_bridge` reads `.spangap-ssh` and `.spangap-tcp`,
  builds one forward spec per present file as `dhost:lport` (where `lport == dport ==`
  the file's port — the port the container dials on `host.docker.internal`), and launches
  the forward script with those specs. Keep the existing pidfile idempotency (pid + spec
  set; restart on change). `stop_bridge` kills the pid and removes the pidfile.
- **`write_forward_script` (899-952)**: generalize the launcher arg format from
  `<host> <port>...` to a list of `<dhost>:<lport>` specs (the inner
  `serve(lport, dhost, dport)` already supports per-port hosts — only argv parsing at
  `945-948` changes).
- **`monitor)` case (705-749)**: call `start_bridge` after port/firmware resolution, and
  install `trap 'stop_bridge' EXIT` at this scope so the bridge tears down on Ctrl-],
  INT/TERM (monitor_loop's `exit 0` fires the EXIT trap), or normal exit.
- **`ensure_container` (1001)**: remove the `ensure_forwarder` call (bridge is now
  monitor-owned only).
- **Config/comments**: remove `HOST_FILE` (264) and `SPANGAP_HOST` usages; update header
  docs (21, 53, 256-264) and the `ensure_forwarder`→bridge comments (954-963) to describe
  the two new files and monitor ownership. Keep `FORWARD_SCRIPT`/`FORWARD_PID`/
  `FORWARD_LOG` (269-271).

### `/straddles/spangap/build-system/spangap-inside`
- **`cmd_cli` (1446-1532)**: rewrite per the resolution order. Keep the existing TCP
  socket code (1483-1532) as the `.spangap-tcp` branch; add the `.spangap-ssh` branch
  (`os.execvp("ssh", [...])` with the options above and the host.docker.internal-or-direct
  `connect_host`); add the bootstrap branch (`subprocess` ssh-keygen if missing,
  `input()` prompt, write `.spangap-ssh`, print authorize instructions).
- Remove `HOST_FILE` (58) and `SPANGAP_HOST` (1463); add small helpers `SSH_FILE`/
  `TCP_FILE` paths and a `parse_endpoint(text, default_port)` splitter (split on last
  `:`). Update the `-h` comment block (1440) to reflect `.spangap-ssh`.

### `/straddles/sshd/esp-idf/src/sshd.cpp` (firmware default → enabled)
- `storageDefault("s.sshd.enabled", 0)` → `1` (line 415, the authoritative persisted
  default).
- `storageGetInt("s.sshd.enabled", 0)` → `..., 1` at lines 114 and 233 (fallback reads,
  for consistency before the default registers).
- Update the gating comments (103, 177-179) accordingly. *Note:* the version-gated
  `storageDefault` block only re-defaults on fresh storage / a `SSHD_VERSION` bump;
  flipping the default affects new installs (the user's `--include sshd` reflashes).
  Bumping `SSHD_VERSION` to force it onto existing storage is optional — call out in the
  PR but default to **not** bumping (avoids re-defaulting unrelated keys).

### Docs
- `/straddles/sshd/README.md`: config table `s.sshd.enabled` default `false`→`true`; in
  "Setup", drop the mandatory `set s.sshd.enabled=1` and note it's on by default.
- `/straddles/spangap/build-system/README.md`: update the "Drive the device over the
  network" section — the bridge is now owned by `spangap monitor` and keyed off
  `.spangap-ssh` (ssh) / `.spangap-tcp` (TCP CLI), and `spangap cli` prefers ssh. Mention
  the first-run bootstrap (keygen + `sshd add`). (`spangap-inside`'s baked copy is the
  README under `build-system/`.)

## Trade-offs / notes
- Bridge stays **Darwin-only** (matches today): on native Linux the container bridge net
  reaches the LAN directly and `host.docker.internal` isn't injected, so both ssh and tcp
  modes fall back to dialing the device host directly.
- `BatchMode=yes` keeps ssh fully non-interactive (no password/passphrase prompt);
  `StrictHostKeyChecking=accept-new` accepts the device's stable host key once without a
  prompt while still catching later key changes.
- The `sshd` host seed persists (`secrets.sshd.host_seed`), so reflashing doesn't churn
  the device host key / `known_hosts`.

## Verification
1. `python3 /straddles/spangap/build-system/spangap-inside validate` (and from a project
   dir) — no manifest regressions.
2. **Bootstrap**: inside the container, `rm -f .spangap-ssh .spangap-tcp; spangap cli` →
   generates `~/.ssh/id_ed25519` if missing, prompts for IP, writes `.spangap-ssh`, prints
   the `sshd add …` + `--include sshd` instructions.
3. **Firmware**: `spangap build --include spangap/sshd`, flash; on a fresh device confirm
   `sshd status` shows enabled without `sshd enable`. Run the printed `sshd add <pubkey>`
   in the monitor.
4. **Bridge + ssh**: on the host, `spangap monitor <port>` → check
   `.spangap-forward-*.log` shows the relay up for the ssh (and tcp, if present) endpoint;
   from the container `spangap cli get s.net` returns output over ssh. Ctrl-] the monitor →
   confirm the forward pid is gone and the pidfile removed (bridge closed on exit).
5. **TCP fallback**: with only `.spangap-tcp` present (and `set s.net.cli_port=2323` on the
   device), `spangap cli get s.net` works via the existing socket path.
6. **Outside parity**: run `spangap cli …` on the host (ssh + tcp modes) — dials the device
   directly, no bridge needed.
