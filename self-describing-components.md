# Plan — Self-describing components

A design direction (liked, **not yet scheduled**) for making "create a new thing"
on the platform a one-liner, by hiding the registration ceremony inside base
classes instead of hand-writing it at every call site.

Builds on two settled principles:

- **Keep platform primitives honest.** `lcdInstall` stays a single-responsibility
  *on-the-lcd-task* primitive that returns the app id. We do NOT teach it to
  self-detect task context and hop internally — that would smuggle a
  context-dependent return contract (id on-task, −1 off-task) into a primitive
  every straddle consumes. The gymnastics move *up* into an abstraction layer
  (a base class), not *into* the primitive.
- **Static config is declarative; live behavior is code.** Settings/storage
  defaults already lower from a `straddle.yaml` `settings:` block
  (see `generated-settings-and-app-dock.md`). This plan is the behavior half:
  apps, workers, CLI verbs — things with real C++ — become "define a class, call
  `T::install()`".

---

## 1. The ceremony today

Maps is the representative case. Its `when:`-gated hook
(`maps/esp-idf/conditional/spangap-lcd/src/maps_lcd.cpp:722`) hand-writes five
unrelated registrations:

```cpp
void mapsLcdRegister(void) {
    if (storageGetInt("s.maps.version", 0) < MAPS_VERSION) {   // (a) defaults + a
        storageDefault("s.maps.zoom", 15);                     //     pointless version
        storageDefault("s.maps.tiledir", "/sdcard/maps");      //     gate (zero users)
        storageSet("s.maps.version", MAPS_VERSION);
    }
    s_mux = xSemaphoreCreateMutex();                           // (b) worker state
    cliRegisterCmd("maps", cliMaps);                           // (c) CLI verb
    s_worker = spawnTask(mapsWorker, TAG, 8192, ...);          // (d) render worker
    lcdRun([](void*) { lcdInstall(new MapsApp()); });          // (e) install — the
    lcdRegisterSettings("Maps", "Maps", mapsSettingsPane);     // (f) settings pane
}
```

- (a) and (f) are **static config** → they belong in a `settings:` block
  (the generator already supports `section`/`slider`/`value`/`text`,
  `spangap-inside:1029`). The version gate is dead weight — zero users, just
  `storageDefault` unconditionally.
- (e) is the **install ceremony**: a `new`, a task hop (`lcdRun`), and a
  throwaway lambda, written identically in every LCD straddle.
- (b)(c)(d) are **genuine app behavior** — they stay code, but can move *into*
  the app class so the straddle's hook gets out of the wiring business.

The target end-state: the hook disappears from C++ entirely and becomes one yaml
line, `install: MapsApp` — the generator synthesizes the `MapsApp::install();`
trampoline (see §2).

## 2. How the boot dispatcher works (and why it constrains us)

The build generates the buildable's entire entry point — `app_main()` plus two
phase dispatchers (`spangap-inside:1172` `write_init_dispatch`):

- **`start:`** — bare hardware, before fs/storage/log/cli exist (board rail
  power, display/touch HAL). Raw peripherals only.
- **`init:`** — after `spangapInit()`; the whole ecosystem (storage, net, web,
  lcd) is up. This is where straddle hooks like `mapsLcdRegister` fire.

Three properties of the generator we must not break:

1. **Ordering** is the one source of truth (`init_order()`): platform band
   (core/net/web/lcd) then dependency-topo straddle band, buildable last.
2. **`when:` gating** is resolved at generation time (`spangap-inside:1240`): a
   hook gated on a pruned straddle is simply never written — preprocessor-free.
3. **No straddle-header coupling.** The dispatcher forward-declares each hook as
   a bare `void mapsLcdRegister(void);` and calls it. A `--with` straddle isn't
   in the buildable's requires graph, so **its `include/` dir is NOT on the
   dispatcher's path** — only its component *lib* is on the link line. A bare
   forward-decl + call resolves; an `#include` would not (`spangap-inside:1206`).
   Referencing the symbol is also what keeps it off `--gc-sections`' list.

**Consequence for "boot just calls `<name>::install()`":** it can't, literally.
`MapsApp::install()` is a *static member* — calling it needs the full class
definition visible, i.e. maps' header on the dispatcher's include path. That is
exactly the coupling property #3 forbids. You cannot forward-declare a class and
call a static on the incomplete type.

**Resolution — a generated trampoline, authored as `install:` in yaml.** The line
`MapsApp::install();` must be compiled where the full `MapsApp` definition is
visible. Two places it could go:

- **(a) The dispatcher includes every staged straddle's header** and calls
  `MapsApp::install();` directly. Breaks property #3 — all straddle headers in one
  TU (name/macro-collision risk) + every staged include dir wired onto the
  dispatcher component. Rejected.
- **(b) Generate a one-line TU into each straddle's OWN component** (its header is
  already on that component's path), exposing a free symbol the dispatcher
  forward-declares and calls exactly as today. ← chosen.

The author writes **no trampoline** — just the class plus an `install:` entry in
`straddle.yaml`. From `install: MapsApp` the generator mints both sides off one
deterministic symbol:

```cpp
// generated into the dispatcher (unchanged mechanism — forward-decl + gated call):
void spangapInstall_MapsApp(void);
...
spangapInstall_MapsApp();                 // ordered + when:-gated as today

// generated into maps' OWN component (header visible here, not in the dispatcher):
#include "maps.h"
void spangapInstall_MapsApp(void) { MapsApp::install(); }
```

This keeps the dispatcher header-free (property #3 intact), keeps the GC story
(the dispatcher still *references* a real symbol), and keeps ordering + `when:`
gating in the generator. The generated trampoline TU is itself conditional on the
same `when:` as the class it wraps (emit it into the straddle's `conditional/`
slice when gated). Two new requirements this imposes:

1. **The app class moves to a public header — out of the anonymous namespace, but
   NOT into any named namespace either.** Today `MapsApp` sits in an anonymous
   namespace in `maps_lcd.cpp`; anon-namespace members have **internal linkage**, so
   a separate TU (the trampoline) can never name them. The *only* requirement is
   external linkage + a header declaration; a plain **global** `class MapsApp` in
   `maps.h` satisfies it, and the generator emits `MapsApp::install()` straight
   from the class name. We deliberately do NOT wrap it in a `spangap::` namespace:
   the codebase disambiguates by **name prefix, not namespace** (`LcdApp`,
   `lcdInstall`, `storageGetInt` are all global), so a namespace would be an
   inconsistent island — and the base classes `LcdApp`/`LcdAppT` are global anyway.
   Global-collision risk is low (IDF/LVGL are prefixed C; cross-straddle name
   clashes are already caught by the build aggregation), and if one ever bites,
   namespacing is a localized fix then. The generated *C symbol*
   `spangapInstall_<Class>` keeps its prefix — that's the normal symbol-naming
   convention, unrelated to the C++ namespace.
2. **A header-discovery rule.** Start explicit — an `install:` entry carries the
   header — to avoid magic; a `<prefix>.h` convention can come later. And the
   entry is **phase-less** — a top-level `install:` list, NOT under `start:`/
   `init:` (see §3a: the class declares its phase by which virtuals it overrides,
   so the generator never sees a phase):
   ```yaml
   install:
     - { class: MapsApp, header: maps.h, when: spangap/spangap-lcd }
   ```

So the "self-describing" win lands fully at the **author** layer (class + one
yaml line, no trampoline, no `new`, no `lcdRun`, no phase) while the dispatcher
mechanism is untouched.

## 3. The LCD base class — `LcdAppT<Derived>`

A CRTP base over the existing `LcdApp` (`lcd_app.h:37`). CRTP is required: a
static method defined on `LcdApp` can't see the derived type, so
`MapsApp::install()` would try to `new LcdApp` (abstract). `LcdAppT<Derived>`
makes `Derived::install()` construct a `Derived`.

```cpp
// Common root — ALL installable kinds derive from this. Holds the phase virtuals
// and the boot registry. NOT static-init self-registration (see §5): the ctor
// only runs when an explicit, generated install() calls `new` at boot.
class Installable {
public:
    Installable() { registry().push_back(this); }   // join the registry on construction
    virtual ~Installable() {}
    virtual void onStart() {}   // pre-spangapInit  (bare hardware)
    virtual void onInit()  {}   // post-spangapInit (ecosystem up)
    virtual void onBoot()  {}   // go live
    static std::vector<Installable*>& registry();    // Meyers singleton
};

template <class Derived>
class LcdAppT : public LcdApp /* : public Installable */ {
public:
    using LcdApp::LcdApp;                            // inherit the Config ctor (trivial!)

    static void install() {                         // generated trampoline calls this
        if (!s_inst) s_inst = new Derived();        // construct → Installable ctor registers it
    }                                               // that's ALL install() does now
    static Derived* instance() { return s_inst; }

    void onInit() override {                         // platform calls this post-spangapInit
        lcdRun([](void*){ lcdInstall(instance()); });   // the ONE lcd-task hop, hidden here
        instance()->appInit();                          // leaf wiring (CLI/worker), if any
    }
private:
    static inline Derived* s_inst = nullptr;
};

class MapsApp : public LcdAppT<MapsApp> { /* trivial ctor (Config); fills appInit()/onBoot() */ };
```

### 3a. The dispatch model — construct all, then drive phases

The generator emits ONE flat, ordered, `when:`-gated list — `spangapInstallAll()`
— that calls every `spangapInstall_<Class>()` trampoline. Each `install()` only
*constructs* its singleton; the `Installable` ctor pushes it onto the registry.
The platform then drives the phases by walking that registry — fixed code in
spangap-core, NOT generated, NOT per-thing:

```cpp
spangapInstallAll();                              // generated: construct + register all
for (auto* x : Installable::registry()) x->onStart();   // bare hardware
spangapInit();
for (auto* x : Installable::registry()) x->onInit();    // ecosystem up
for (auto* x : Installable::registry()) x->onBoot();    // go live
```

A thing participates in a phase purely by overriding that virtual; the rest are
no-ops. **The generator never learns the phase** — that's the whole win of this
section (carried into §7).

Design points:

- **Naming.** `register` is a **reserved C++ keyword** (deprecated C++11, meaning
  removed C++17, still reserved) — it will not compile as a method name. Use
  `install()` (symmetric with the `lcdInstall` primitive) or `create()`.
- **`install()` is now trivial: construct + register, nothing else.** No phase
  work, no task hop, no wiring. That is what makes it kind-agnostic enough for the
  generator to call blindly. Singleton ownership stays (`instance()`), `void`
  return stays honest.
- **Constructors must be ecosystem-free.** Every object is constructed at the top
  of `app_main`, *before* `spangapInit()` (so it exists for a possible `onStart`).
  Heap/PSRAM are up that early, so `new` is fine — but a ctor must NOT touch
  storage/fs/log/ITS. All real work moves into `onInit()`/`onBoot()`. (This is a
  genuinely new, stricter rule — see costs in §5.)
- **The primitive stays honest.** `lcdInstall` is unchanged — still on-task, still
  returns the id for the `shellInit` built-in path. The lcd-task hop lives in
  exactly one place: `LcdAppT::onInit()`.
- **`appInit()` / `onBoot()` (leaf overrides)** are where an app puts its wiring —
  `cliRegisterCmd("maps", cliMaps)`, the `s_mux` create, the `spawnTask` worker —
  discoverable *on the class*, run by the platform's phase walk, not scattered in
  a registration function.

## 4. Generalizing to non-LCD things — `ServiceT<>`

Same shape, second base class over `Installable` for things with no launcher tile
(net services, background workers). The author overrides only the phase virtuals
it needs:

- `onStart()` — bare-hardware work, pre-`spangapInit` (rare for a service).
- `onInit()` — wire up / register (ecosystem up).
- `onBoot()` — go live / start running.

It needs no `start:`/`init:` listing and no per-kind dispatch: it's just another
`install:` entry, constructed into the same registry, and the platform's phase
walk (§3a) calls whichever virtuals it overrides. `ServiceT` adds no install-time
machinery of its own — the base `install()` (construct + register) is inherited;
all it contributes is service-flavored defaults/helpers for the virtuals.

**Start/stop is not a phase.** A service object is installed once and lives
forever; its *task* is what starts and stops (on demand, or via an
`s.<x>.enable` subscription / CLI command), entirely inside the service. The
`Installable` phases are boot-time only — they bring the object into being; the
service governs its own worker thereafter. We are not evicting object instances
(no code loading/unloading on the ESP32).

So uniformly: **define a class over the right base, override the phases you care
about, and add one `install:` entry.** The generator synthesizes the trampoline
(§2); the platform constructs everything and walks the registry per phase.

## 5. Two registries — the one we use vs the one we reject

§3a introduces a runtime registry (`Installable::registry()`). It is essential to
see why this is NOT the static-init self-registration trap, and what it costs.

**The trap (still rejected): static-init self-registration.** Each component drops
a `static Registrar<MapsApp> _reg;` whose *global constructor* inserts into a
vector, and boot iterates it. Broken for this build:

- **Linker GC.** Each straddle firmware half is an ESP-IDF component = a static
  archive. With `-ffunction-sections`/`--gc-sections`, a TU whose *only* outward
  symbol is a self-registering global is garbage-collected — registration
  silently never runs unless force-linked (`spangap-inside:1201`).
- **Loses `when:` gating + band ordering** — a compiled-in TU always registers; no
  generation-time pruning, no `init_order()` bands.
- **Static-init-order fiasco** — global ctor order across TUs is unspecified.

**What we use instead: a registry populated by explicit, generated `install()`
calls.** The `Installable` ctor pushes onto the registry, but it only runs when
`spangapInstallAll()` — the generated, `when:`-gated, `init_order()`-ordered list
— explicitly calls `new`. So we keep every property the trap loses: GC-safe (the
trampoline symbol is *referenced*), `when:`-gated, ordered. The registry is just a
runtime handle on the already-ordered set, so the platform can walk it per phase
(§3a) instead of the generator emitting a separate ordered call-list per phase.
The distinction is *who triggers the ctor* — an explicit generated call (fine),
not a static initializer (broken).

**Costs this model adds** (state them honestly):

1. **A common `Installable` root base + a runtime registry** (one pointer per
   installable — trivial memory). `LcdApp`/`Service` now derive from it.
2. **Eager construction, ecosystem-free ctors.** Every object is constructed
   before `spangapInit()` (so it can take part in `onStart`), vs today's
   lazy-per-phase touch. Ctors must not assume storage/fs/log/ITS — a new global
   rule (§3a). Heavy/lazy work (UI build) is unaffected; it already defers to
   first open.
3. **One order for all phases.** The registry is walked in `install` order at
   every phase. This matches today exactly — the current dispatcher already orders
   both `start:` and `init:` through the same `init_order()` — so it's not a
   regression, but it does mean phases can't be independently reordered.

## 6. Dividing line (the rule this establishes)

- **Static config** (settings, storage defaults, panels) → declarative
  `settings:` yaml, lowered by the build. No hand-written `storageDefault` /
  `lcdRegisterSettings` / pane function.
- **Live behavior** (apps, workers, CLI verbs) → a class over `LcdAppT<>` /
  `ServiceT<>`, declared with a phase-less `install:` entry; the build synthesizes
  the `T::install()` trampoline, and the class expresses *when* it acts by which
  phase virtuals it overrides.

Everything you create is then one of two gestures: a yaml block, or a class +
`::install()`.

## 7. The payoff — the generator is kind-agnostic

This is the point of the whole exercise. The generator's *entire* contract with
any installable thing is one uniform interface: **`spangap::<Class>::install()` —
static, no args, void.** Per `install:` entry it needs only *three* kind-agnostic
facts — class name, header, `when:` — and never learns the base class, the
lifecycle, the phase, or what the thing wires. It `#include`s the header, emits
the construct-call, done. `LcdApp` and `Service` are not special-cased; they are
merely two things that satisfy `install()`.

So the generator is **closed for modification, open for extension**: invent
`Daemon`, `Codec`, `Sensor` base classes — each overriding whatever phase virtuals
it needs — and as long as they expose `install()` they drop into the same
`install:` list with no generator change. Nobody is special.

**The boundary is now even further out.** Because phase lives in the *object* (the
virtuals) and the platform drives it by walking the registry (§3a), even adding a
genuinely *new* lifecycle phase no longer touches the generator — it touches
`app_main`'s pass-list and a new virtual on `Installable`, nothing else. The
generator only ever does one thing: construct the registry. It is agnostic of
kind *and* of phase.

## 8. Core is not special either — core services as Installables

The abstraction eats its own creator: spangap-core's foundational pieces
(`LogService`, `CliService`, `CronService`, the net stack, …) become `install:`
entries in **spangap-core's own `straddle.yaml`**, declared exactly like a
straddle's app — no hand-wired special-casing.

No bootstrap paradox, because the ordering already exists: `init_order()`'s
**platform band** (core/net/web/lcd) precedes the straddle band, so core services
land first in the single ordered registry. Their `onInit` brings the ecosystem up
before any straddle installable's `onInit` runs — the "deps are up" guarantee
falls out of the existing bands, no new mechanism. `spangapInit()` then largely
**dissolves** into "the platform-band slice of the `onInit` walk," each service
owning its own bring-up.

**The irreducible nucleus stays hand-wired** — it's what the mechanism rests on:

- **The registry + the `app_main` walk driver.** It *runs* Installables; it can't
  be one.
- **The earliest bring-up an `onInit` could need before it can run.** Heap is up
  pre-`app_main` (so `new` works from the first line); realistically only the very
  first fs/storage bring-up must precede the walk if any service reads storage in
  its own `onInit`. Even storage can be an Installable if ordered early enough in
  the platform band — only the truly self-referential bottom stays explicit.

**Scope / risk — do this LAST.** `spangapInit()` is the most load-bearing code in
the tree. Convert it only *after* the pattern is proven on maps, and
**incrementally** — one core service into `install:` at a time, re-flashing
between each — never a big-bang rewrite. This section is a destination, not a
near-term step.

---

## Proving ground — maps (first slice, isolated, low risk)

Two phases. **Phase 1 proves the runtime shape — base classes, registry, phase
walk — with no generator changes** (maps is still triggered by its existing
hand-written `call:` hook). **Phase 2 adds the `install:` codegen** so the hook
disappears.

**Phase 1 — runtime shape (platform + base classes, no generator):**

1. **Settings → declarative.** Add a `settings:` block to `maps/straddle.yaml`
   (`section`/`slider` zoom min/max/`default: 15`/`value` `maps.state`/`text`
   tiledir `default: "/sdcard/maps"`). Delete the version gate, both
   `storageDefault`s, `mapsSettingsPane`, and the `lcdRegisterSettings` line.
2. **Add `Installable`** (root base: phase virtuals + `registry()`) and the
   `app_main` phase walk (`onStart` pre-`spangapInit`, `onInit`/`onBoot` after) to
   the platform. Add `LcdAppT<Derived>` to `spangap-lcd` (CRTP over `LcdApp`,
   `LcdApp : public Installable`); the lcd-task hop lives in `LcdAppT::onInit`.
   Leave `lcdInstall` untouched.
3. **`class MapsApp : public LcdAppT<MapsApp>`** (global, no namespace) —
   promoted out of the anonymous namespace into a public header (`maps.h`);
   **trivial ctor**; move `cliRegisterCmd`, the `s_mux` create, and the worker
   `spawnTask` into `MapsApp::appInit()` (run from `onInit`). Maps needs no
   `onStart` (no bare-hardware work).
4. **Hand-trigger construction**: `void mapsLcdRegister(void) { MapsApp::install(); }`
   (still `call: mapsLcdRegister` — generator untouched). `install()` constructs +
   registers; the platform's `onInit` walk then does the lcd hop + `appInit`.
5. **Build + flash** (`reticulous/reticulous --with spangap/hw-tdeck`),
   confirm the launcher tile, settings pane, CLI verb, and live map all still
   work. (`spangap log` + `spangap cli "maps"`.)

**Phase 2 — codegen sugar (touches the generator):**

6. Add a top-level, phase-less `install:` descriptor (`class` + `header` + `when`)
   to the schema; teach `write_init_dispatch` to mint each `spangapInstall_<Class>`
   symbol + forward-decl into a generated `spangapInstallAll()`, and emit the
   `#include`d trampoline TU into the straddle's own component (its `conditional/`
   slice when `when:`-gated). `app_main` calls `spangapInstallAll()` then walks the
   registry.
7. Replace maps' `call: mapsLcdRegister` + hand trampoline with
   `install: { class: MapsApp, header: maps.h, when: ... }`; delete the hand hook.
   Rebuild and re-confirm.

If Phase 1 lands clean, the pattern is proven and we decide whether to roll
`LcdAppT<>` across the other LCD straddles and add `ServiceT<>`.

## Open questions

- **Naming:** `install()` vs `create()` vs `mount()`. Leaning `install()` for
  symmetry with `lcdInstall` — confirm it isn't confusing *against* the primitive.
- **How much moves into the phase virtuals** vs staying in a `call:` hook. Maps
  argues for "all of it" (`appInit`/`onBoot`); a straddle with cross-app wiring
  might keep some in a plain `call:` hook alongside its `install:`. Keep `call:`
  available — `install:` is additive sugar, not a replacement.
- **Ordering between `call:` hooks and the registry walk.** `call:` `start:`/`init:`
  hooks and the `Installable` phase walk are two mechanisms in the same phases.
  Default: run the existing `call:` dispatcher first (lowest-level board bring-up),
  then the registry walk — but confirm nothing needs the reverse.
- **Teardown phase? No (decided).** Two lifetimes, only one is `Installable`'s:
  the **object** is construct-once and immortal (no eviction/dtor phase — instance
  destruction would only matter if we loaded/unloaded code, which the ESP32
  doesn't). A service's **start/stop/on-demand** is *task* lifetime — a runtime
  concern it owns internally (its FreeRTOS task), driven as today by an
  `s.<x>.enable` subscription or a CLI command. A stopped service is still
  installed; it just parked its worker. So `Installable` phases stay strictly
  boot-time and monotonic; the dtor stays virtual but unused.
- **Header discovery for `install:`** (§2 req. 2): explicit `header:` field
  (chosen, no magic) vs a `<prefix>.h` convention. Start explicit; a convention
  can layer on later once every straddle reliably has an umbrella header.
- **Namespace (decided):** none — global class in a header. The only hard rule is
  "not anonymous" (anon = internal linkage, unreferenceable by the trampoline). No
  `spangap::` wrapper: the codebase disambiguates by name prefix, not namespace, and
  the base classes are already global. Generated C symbol `spangapInstall_<Class>`
  keeps its prefix (symbol convention, not a C++ namespace).
