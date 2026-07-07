# Code cleanup: drop `s.<x>.version` config-version gates

Leftover from the documentation overhaul (decision A7): config-version gates
conflict with the platform's no-config-migrations policy (documented in
`spangap-core/docs/storage.md` and `storage-internals.md`). The policy is
settled; the gates still exist in code. The one-time `storageDefault` blocks
they guard can simply run unconditionally.

Known sites:

- `nomad/esp-idf/src/nomad.cpp` (~line 961)
- `upnp/esp-idf/src/upnp.cpp` (~line 450)
- `wg/esp-idf/src/wg.cpp` (~line 169)
- `sshd/esp-idf/src/sshd.cpp` (`s.sshd.version` — also noted in
  `sshd/INTERNALS.md` §9 "Code-cleanup notes")
- check `audio` and `iface-tcp` for the same pattern

For each: delete the version read/compare/write, make the defaults block
unconditional, and remove the `s.<x>.version` key from any docs/tables that
mention it. Grep for `\.version` storage reads to catch stragglers.
