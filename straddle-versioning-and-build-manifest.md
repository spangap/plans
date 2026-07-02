# Releases & version constraints — future ideas

Status: ideas / not scheduled. The HEAD-tracking half is **done** (see "Shipped"
below); everything here is deferred until there are actual releases to constrain
against. Nothing here is committed to a design yet.

## Shipped (for context)

- Every straddle is `version: 0.0.0` — the "nothing released yet" sentinel. Git is
  the source of version truth; the committed field is never a hash.
- `spangap build` HEAD-tracks: in clone mode it fast-forwards each already-cloned
  dependency to its upstream before building (guarded, non-fatal, `--no-pull` /
  `SPANGAP_NO_PULL` to disable). `--straddles` dev checkouts are never auto-moved.
- Each build emits `/BUILD.md` into the device webroot: buildable + every staged
  straddle, alphabetical, with short hash, commit date, branch, and a `(dirty)`
  marker. The on-device viewer links to it from the welcome page.

## The trigger for all of this: first release / tags

None of the below is meaningful until straddles carry git **tags**. The moment
tags exist, `git describe --tags --always --dirty` becomes the real version, and
`0.0.0` is just what an untagged straddle reports. So step one, whenever releases
begin, is: start tagging, and teach the build to read the tag as the version
(instead of trusting the static `0.0.0` field, which stays as the untagged
fallback).

## Ideas, roughly in dependency order

### 1. Declare minimum versions of dependencies (`@`-constraints)

Let a straddle require a minimum version of another, in the existing `@`-token
form already allowed by the schema on `requires:` / `additional_installs:`
entries. The token is currently **latent and unused** (no straddle carries one),
so there is no git-ref semantics to preserve — the constraint grammar is a free
choice:

- Minimal: `org/repo@>=1.2.0`.
- npm/cargo-ish: `^1.2`, `~1.2.3`, ranges.
- Whatever is chosen, widen the schema regex and pick a form a future solver can
  represent.

### 2. Enforce constraints at build time — including already-downloaded straddles

- Resolve each dependency's actual version via `git describe` on its checkout
  (not the yaml field).
- Compare against the constraint. Below the floor → error, or offer `--update`
  to fetch a satisfying tag.
- This is the "also of already downloaded straddles" requirement: a stale local
  clone that no longer satisfies a raised floor should be caught, not silently
  built against. (The HEAD-tracking pull already refreshes branch checkouts; a
  pinned/tagged dep is what this check governs.)

### 3. A version solver (only if/when needed)

Needed only when two straddles demand **different** versions of a shared third —
a genuine conflict a linear check can't resolve. Moot until tags exist and such
a conflict actually arises. Don't build speculatively; just keep the constraint
grammar (idea 1) expressive enough that a solver could consume it later.

### 4. Surface release identity on-device

Once versions are real, `BUILD.md` and the boot log (`app_build_version`) can
show tags instead of `0.0.0`, and the manifest can record requested constraint
vs resolved version per straddle. Cheap follow-on once tags land.

## Open questions (revisit at release time)

- Keep the `version:` yaml field as a documentation-only `0.0.0`, or drop it
  entirely and derive purely from `git describe`? (Leaning: drop it once tags are
  the source of truth — one less thing to keep honest.)
- Constraint grammar: minimal `@>=x.y.z` vs full npm-style ranges.
- On a raised floor with a stale local clone: hard error vs auto-`--update`.
