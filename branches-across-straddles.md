# Branches across straddles

## Motivation: retire `--straddles`

Once bootstrapped, `--straddles` earns nothing. The workspace *is* the override
mechanism: every straddle checkout is editable, forkable (org/name isn't
validated, so a fork just replaces the checkout), committable, and pushable in
place. For the in-tree developer, "point the build at a different source" and
"edit the thing in your workspace" are the same capability — the flag is
redundant surface area that forces every build path to branch on where straddles
come from.

The only jobs the workspace model structurally can't do are invocation-scoped,
non-dirtying override and multiple roots at once. But a second/third workspace is
just another directory that shares nothing — `cd ../other-workspace` already
covers the multi-root case. That leaves `--straddles` with no residual job.
Retire it.

## The real unit of work is a cross-straddle change

What you experiment with is almost never *a straddle* — it's a *change that
crosses straddles*, and there's no first-class handle for that today. A shared
branch name is that handle.

Resolution rule: **use branch X wherever it exists, else main.** A checkout
resolves each straddle to branch X if that straddle has it, otherwise main.

Key property — **emergent membership**: a straddle is "in" experiment X iff it
carries branch X. No manifest to maintain, no membership list to keep in sync;
you add a straddle to the experiment simply by pushing branch X into it.

This is strictly more capable than an override flag:
- **Collaborative** — others check out topic X and get the whole cross-cutting
  state, not just one overridden straddle.
- **Namespaced** — the experiment lives off main instead of dirtying it, and
  leaves a trace.

Prior art: Gerrit topics / Android `repo` manifest-branches — the same idea,
recovered from a polyrepo by convention.

## Primitives to build

The resolution rule is cheap. The workspace-wide fan-out is the real work, and
it's what the current workscripts already do informally:
- **push-all** — push everything dirty to a named branch X across all straddles.
- **pull-all** — pull/sync branch X (with main fallback) across the workspace.
- checkout resolves per-straddle branch with main fallback.

## Lifecycle — where the actual work is

1. **Drift.** main moves under the experiment, so straddles carrying X diverge
   and periodically need a workspace-wide rebase-onto-main. That fan-out is the
   workscript problem, formalized.
2. **Landing.** Merging X→main across N repos isn't atomic. With cross-straddle
   API changes there's a window where repo-A is merged and repo-B isn't and the
   build is broken. Want an ordering or a "land these together" operation.
3. **Reaping.** Because resolution silently falls back to main, an abandoned or
   half-deleted experiment degrades quietly — some repos still have X, some
   don't, nobody notices. A workspace-wide `status`/`topics` view (which
   straddles carry X, how far each has diverged) is therefore not optional; it's
   what makes emergent membership legible instead of spooky.

## Boundary to keep clean

Branch-resolution is a *dev-time* convenience that resolves to a moving tip. The
lock must still pin exact commits per build — otherwise "reproducible build" and
"follow branch X" fight each other. Keep the branch layer for humans
coordinating; let the lock do the pinning.

Do this and `--straddles` isn't just removed, it's replaced by something that
does a job it never did well.
