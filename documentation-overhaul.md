# Documentation in this project — the standard, and how to fix it

A reusable playbook for writing and overhauling a straddle's documentation. It
applies equally to **every** straddle. Follow it whenever you create, consolidate,
or clean up docs. (The `rns` straddle is the current reference example of the
end state.)

## The standard: README + INTERNALS, scaled to the straddle

The two roles are always the same — an **operator guide** and a **maintainer
reference** — but how they're laid out depends on how much the straddle does.

**Default (a mono-function straddle — most straddles, e.g. `rns`):** exactly
**two files at the straddle root**:

- **`<straddle>/README.md`** — the operator / user guide.
- **`<straddle>/INTERNALS.md`** — the maintainer reference.

**Large, multi-function straddles (e.g. `spangap-*`):** one pair for the whole
thing would be unwieldy, so the same roles spread out by function:

- **`<straddle>/README.md`** — a **general overview** of the whole straddle: what
  functions it contains, how they fit together, and a pointer to each function's
  doc. It is *not* the operator guide for every function.
- **`<straddle>/docs/<function>.md`** — **one per major function.** Each plays the
  role README plays in a mono-function straddle (the operator guide for *that*
  function), and **ends with an `## Internals` chapter** — as long as it needs to
  be — that plays the role `INTERNALS.md` plays there. So these straddles have **no
  separate `INTERNALS.md`**: internals live in the trailing chapter of each
  function's doc.

Either way, the **shapes below apply**: the operator part follows the operator-guide
shape; the maintainer part follows the maintainer shape — whether it's the
`INTERNALS.md` file or the `## Internals` chapter.

Hard rules (both layouts):
- **A straddle's docs live in that straddle.** Never document straddle A inside
  straddle B (e.g. an app's docs under a board straddle). Fold a stray doc into the
  owning straddle and delete it (fixing refs).
- **No plan file is documentation.** Plans under `plans/` are scratch history, not
  the source of truth. The docs stand on their own; they don't defer to a plan.
- **`docs/` is only for the multi-function pattern** — one self-contained file per
  function (operator guide + `## Internals` chapter), never arbitrary per-subsystem
  fragmentation. A mono-function straddle keeps everything in its two root files;
  if a section there grows big, it's a section, not a new file.
- A genuinely cross-cutting, multi-straddle architecture doc is separate from any
  straddle's docs (see Scope).

### Operator-guide shape (a mono-function `README.md`, or a `docs/<function>.md`)

Operator-facing, example-driven, **not a header dump**. (A multi-function
straddle's top `README.md` is lighter — a what's-here overview that indexes the
per-function docs — not this full guide.)

1. One-paragraph what-it-is.
2. **Origins** — brief. If the straddle wraps/forks/ports something, say so in a
   line and point detail to INTERNALS.
3. What it does, and **how it interacts with the other straddles** — short
   narrative plus one minimal, real usage example ("it opens an ITS connection to
   PORT_X sending struct Y; then the handle is the data pipe…").
4. The public surface: ports / API / opcodes (table, with a pointer to the header
   for exact layouts).
5. **The full storage-variable list** — settings (with defaults), runtime/
   telemetry, command sentinels, secrets. Exhaustive, and verified against code.
6. CLI / user manual.

Don't tell users to call an `init()` the build's generated init already calls —
state that it starts automatically when the straddle is in the build.

### Maintainer shape (an `INTERNALS.md`, or a `## Internals` chapter)

Maintainer-facing. **§1 first: an exhaustive inventory** of everything this
straddle changed or added relative to its upstream/baseline, and everything it
adds on top. Then: the task/threading model and ownership rules, the wire/IPC
framing, lifecycle details, and a dedicated **pitfalls** section. It is
self-authoritative.

## Terminology discipline

- **Use the real vocabulary** — the upstream library's or the protocol's terms,
  and the platform's existing names. Don't invent an in-house word for a concept
  that already has one.
- **Don't coin names that collide** with a platform primitive's term (the same
  word meaning two different things across straddles is a trap).
- **Pick the precise word**, and keep a borrowed-but-misused term out (e.g. a
  word that means one thing in the core but got reused loosely here).
- When you rename, do it **across code, comments, and docs in the same pass**, and
  verify zero stragglers of the old token.

## What belongs, and what doesn't

The governing principle, for both docs and code comments: **describe the present,
not the path to it.** Write as if the code has always looked this way. Anything
that narrates the *journey* — how it used to be, the order it was built in, what's
planned next — is out. The only admissible history is a trap a maintainer could
re-introduce, and even then you state the rule, not the story.

**Cut — keep none of this:**

- **Plan phases / rollout order** — "Phase A–G", milestones, "next we'll…",
  effort/risk/acceptance tables, sequencing diagrams, "deferred to a later phase".
- **Pointers into plan docs** — `§N.N` anchors, links to `plans/*` or other
  design docs, "see the plan".
- **Old states of software** — "we used to", "previously", "originally", "no
  longer", "before the refactor", "was X, now Y", "renamed from", "this replaces…",
  migration history, mentions of removed features or deleted code paths.
- **Dated / status prose** — "current status", "what works / what's left",
  "files touched (uncommitted)", verification or soak logs, TODO-by-date,
  who-did-what.
- **Superseded or in-house terminology** — and any borrowed term used loosely
  where a precise one exists.
- **Plan-only facts** — keys, defaults, or features that live in a plan but not in
  the code. Verify against source, then omit.
- **Compiled-out / stubbed code described as if live** — if it's `#if 0`'d, a
  stub, or unimplemented, say so plainly or leave it out; never present it as
  working.
- **Restatement of the header/signature verbatim** — docs add narrative and
  examples, not a copy of the declarations.
- **Scratch** — throwaway scripts, runbooks, build/CI noise, and any memory
  references, slugs, or `[[links]]`.

**Keep — this is the substance:**

- **What it is and does**, and how it interacts with the other straddles.
- **The current, true behavior and contracts**, in the present tense.
- **The complete, verified surface** — every config/storage key, port, opcode, and
  CLI command, with real defaults.
- **Architecture and the ownership / threading rules.**
- **Pitfalls and gotchas** — including a historical one *only* when it's a concrete
  trap to avoid, framed as the rule ("X must be Y, because Z") not the anecdote
  ("we changed X to Y").
- **Rationale for non-obvious decisions** — *why* it's this way, stated as a
  present fact.
- **The exhaustive inventory** of what this straddle changed or added relative to
  its baseline (INTERNALS §1).

This applies to **code comments too** — they are documentation. De-phase CLI help
and log strings the same way; when a comment leaned on a plan reference, replace it
with a plain inline description and move the durable part to INTERNALS.

## How to run an overhaul (process)

1. **Gather** all source for the straddle: any scattered `.md`, the code comments,
   the headers, and the commit history.
2. **Write** the two files from that material.
3. **Coverage audit before deleting anything.** Walk every fact, value, storage
   key, opcode, CLI command, and pitfall in the material you intend to delete, and
   confirm each is either (a) reflected in README/INTERNALS or (b) genuinely stale.
   Anything that is neither gets **added** to the right file.
4. **Verify every fact against live source — not the plan, not an older doc.**
   Plans and stale docs carry wrong values. Grep the code before you migrate a
   number, a key, or a default. (Watch for facts hidden behind indirection — e.g.
   a key read through a macro variable rather than a literal string — and for code
   that is compiled out, `#if 0`'d, or stubbed: don't document it as live.)
5. **Build / test after any code or comment change** (a malformed comment can
   break a build).
6. **Classify the exceptions** — the things you did *not* carry over — so the
   decision is on record: *stale/removed*, *plan-only (never in code)*, or
   *domain-foreign (belongs in another straddle's docs)*.
7. **Fix dangling references** in the files that stay, then delete the obsolete
   ones.
8. **Commit** in themed, per-repo commits: sign off (DCO `Signed-off-by`, **no AI
   co-author trailer**), no memory refs in the message, and **stage only the files
   this work touched** — never sweep unrelated working-tree changes into a doc
   commit.

## Scope boundaries

- A broad, cross-cutting architecture document that spans several straddles is
  **not** one straddle's doc — leave it where it is and don't fold it into a
  straddle's two files. If it ever needs retiring, that's its own audit.
- Settings/config defaults belong to the straddle that owns the key; other
  straddles read it, they don't redefine or default it. Reflect that in the docs
  (point at the owner rather than duplicating).
