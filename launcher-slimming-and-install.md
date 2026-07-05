# Launcher slimming, self-update, and a robust installer

## Goal

Make the on-PATH `spangap` launcher small and near-static so it rarely needs
re-installing, let a workspace pull a stale launcher forward automatically, and
give the install itself prereq checks + PATH detection so it's foolproof.

Three decisions already agreed:

1. **Slim the launcher** — move everything but bootstrap + dispatch into
   `spangap-outside`.
2. **Auto-upgrade** — a workspace notices its on-PATH launcher is older than
   its skeleton's; if the file is writeable it upgrades it in place (one-line
   notice), else it prints that a newer version is available. No `self-update`
   command, no nag stamp, no sudo, no symlink special-casing.
3. **`cli` and `probe` now require a workspace** — kills the no-workspace
   command surface entirely, so the launcher needs no `self_outside`
   self-resolution and no `~/.spangap/skeleton` canonical location.

---

## Part 1 — Slim the launcher (`spangap`)

Current: 327 lines. Target: ~110, all bootstrap + dispatch.

### 1a. `cli`/`probe` require a workspace

- Delete the trailing `case ${1:-} in cli|probe) ...` block (launcher lines
  ~315–324) and the final `die "not in a spangap workspace..."` stays as the
  sole no-workspace outcome for every non-`init`, non-`help` command.
- Delete the `self` symlink-resolution block (lines ~282–291) and the
  `self_outside` variable — nothing outside a workspace needs it now.
- `spangap-outside`'s own `probe)` / `cli)` cases are unchanged (they already
  run inside the workspace dispatch). Only the launcher's outside-workspace
  path goes away.
- **Behavior change to document:** `spangap probe` and `spangap cli` no longer
  work before `spangap init`. Note in README + a CHANGELOG line.

### 1b. Move `init`'s post-bootstrap steps into `spangap-outside`

The only part of `init` that *cannot* move is "obtain a `spangap-outside` to
talk to." Everything after that versions with the skeleton instead of the
launcher.

Launcher `init` keeps only:
- arg parse (`--straddles`, `-v`, `<dir>`),
- resolve/create/enter the target dir + the created-dir cleanup trap,
- the *cheap* pre-clone guards that avoid a wasted clone: nest guard
  (`find_workspace`), `spangap.workspace.yaml` exists, inside-repo guard,
  inside-straddle guard, `./spangap` leftover,
- obtain the skeleton:
  - `--straddles` given and `<straddles>/spangap` exists → skeleton is there,
    no clone,
  - else `git clone "$INIT_URL" spangap`,
- then `exec "<skel>/build-system/spangap-outside" init-continue` with the
  resolved args in the env (`SPANGAP_WORKSPACE=$PWD`, straddles path,
  verbose/quiet, and a flag telling outside to disarm/complete the trap by
  writing the marker last).

Moves **into** `spangap-outside` (new `init-continue)` case):
- README copy from skeleton,
- `ensure_venv` (already a function there),
- `ensure_container` (already there),
- writing `spangap.workspace.yaml` **last** (the success barrier),
- final "workspace initialized" log.

Trap subtlety: the cleanup trap that `rm -rf`s a created dir on failure must
stay in the launcher (it owns the dir it made). `init-continue` failing →
non-zero exit → launcher's trap fires. On success `init-continue` writes the
marker, returns 0, launcher disarms the trap. So the marker-write moves to
outside but the *trap* stays in the launcher. Keep the "marker written last"
invariant intact across the handoff.

### 1c. `usage()` — keep a trimmed copy in the launcher

`--help` must print something with no workspace present, so it can't route
through the skeleton. Keep a short synopsis in the launcher (usage line +
one-line-per-command list). The full prose already lives in
`build-system/README.md`; the launcher `usage()` shrinks to a synopsis and a
pointer. When *in* a workspace, `spangap help` may `exec spangap-outside help`
for the fuller text; outside, the launcher's synopsis is the fallback.

### 1d. Launcher version stamp

Add near the top:

```sh
# Bumped only when the launcher's dispatch/bootstrap contract changes.
# spangap-outside compares this against the skeleton's copy to offer self-update.
SPANGAP_LAUNCHER_VERSION=1
```

Export it before `exec`-ing outside so the running version travels with the
call:

```sh
export SPANGAP_LAUNCHER_VERSION
```

---

## Part 2 — Auto-upgrade

### 2a. Check + act (in `spangap-outside`)

`spangap-outside` lives in the skeleton, so it resolves the skeleton's launcher
copy from its own path: `<skel>/spangap` (one dir up from `build-system/`).

Add an early check (after `$workspace` is known, before the dispatch `case` at
line 1129) that runs on every command:

- Running version = `SPANGAP_LAUNCHER_VERSION` from the env; skeleton version =
  the `SPANGAP_LAUNCHER_VERSION=` line grepped from `<skel>/spangap`.
- Only act when **skeleton > running** (forward only — never clobber a newer
  on-PATH launcher with an older skeleton's).
- Locate the on-PATH file with `command -v spangap`. No symlink handling — treat
  the path as a file:
  - writeable (`[ -w ]`) → `cp <skel>/spangap` over it, `chmod +x`, then print
    `spangap: auto-upgraded <full path>`.
  - not writeable → print
    `spangap: newer version available, but <full path> not writeable`.
- One line, every relevant invocation. No stamp, no prompt, no sudo. (A symlink
  into a clone naturally never triggers: its skeleton *is* the source, so
  versions match and the check is a no-op.)

### 2b. Version bookkeeping

- Bump `SPANGAP_LAUNCHER_VERSION` **only** when the launcher's contract changes
  (dispatch path, init handoff, env it exports). Pure `spangap-outside` changes
  don't touch it — that's the whole point: the launcher stays static.

---

## Part 3 — Robust installer (`install.sh`)

New file at repo root, served from the `spangap` branch beside the launcher.
`curl -fsSL https://raw.githubusercontent.com/spangap/spangap/spangap/install.sh | sh`

### 3a. Prereq checks up front (with per-OS remediation)

- `docker`, `git`, `python3` + `venv` module (`python3 -c 'import venv'`).
- On miss, print the fix per platform (the notes already in README:
  `apt install python3-venv`, `apk add py3-virtualenv`, Docker Desktop link).
- Fail fast with a clear list rather than dying deep inside `init`.

### 3b. Locate-or-place the launcher

- `command -v spangap` present → update in place at its existing path
  (respect the user's chosen location; this is also the upgrade path).
- Else choose a bin dir: first writable of `~/.local/bin`, `~/bin`,
  `/usr/local/bin`. If none writable, offer `sudo` for `/usr/local/bin`.
- If the chosen dir isn't on `$PATH`, print the exact `export PATH=...` line
  (and which shell rc to add it to).

### 3c. Interactive vs piped

- Prompt via `/dev/tty` (works even when piped from curl).
- Non-interactive/CI: honor `--bin-dir DIR` and `-y` (accept defaults, no
  prompt). If no tty and no flags, use the first writable default and print
  what it did.

### 3d. Integrity

- Download launcher, `chmod +x`, verify against a published checksum
  (`spangap.sha256` beside it on the branch).
- Print next steps: `spangap init <dir>` etc.

### 3e. Keep the single-file path documented

The "just `curl -o ~/bin/spangap ...` and `chmod +x`" story stays in the README
as the no-installer fallback. The installer is a convenience, not a dependency —
self-update (Part 2) carries version currency, so the installer stays dumb and
rarely changes.

---

## Part 4 — Docs / cleanup

- **Fix the README curl typo** (line 20: stray `)` in the raw URL) — do this
  now regardless of the rest.
- README: document that `cli`/`probe` need a workspace; document auto-upgrade;
  document `install.sh` + the single-file fallback.
- INTERNALS.md: the launcher↔outside contract (what the launcher exports, the
  `init` → `init-continue` handoff, the version stamp).

---

## Order of work

1. README curl typo fix (trivial, independent).
2. Launcher slim: 1a (drop cli/probe + self_outside), 1c (trim usage),
   1d (version stamp). Test `init`, `build`, `flash`, `help` still work.
3. `init` handoff: 1b + the `init-continue` case in outside. Test a fresh
   `init` (both clone and `--straddles` modes) end-to-end incl. failure/trap.
4. Auto-upgrade: 2a/2b. Test with a deliberately-bumped skeleton vs an old
   on-PATH copy — writeable (upgrades) and not-writeable (prints notice).
5. `install.sh` + checksum: 3a–3d. Test piped and saved, fresh and upgrade,
   dir-not-on-PATH.
6. Docs: README + INTERNALS.

## Decisions locked

- Auto-upgrade when writeable (one-line notice); print-only when not. No
  `self-update` command, no nag stamp, no sudo, no symlink special-casing.
- `install.sh` served from the `spangap` branch raw URL for now.
