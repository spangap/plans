# RNS ratchet support — implemented, pending desk verification

Reticulum ratchets give **forward secrecy**: a destination rotates an ephemeral
X25519 keypair, advertises the current public half in its announces, and senders
encrypt to that ratchet instead of the destination's long-term identity key. If
the identity key is later compromised, traffic encrypted to since-rotated
ratchets stays unreadable.

The design and the code map now live where they belong — **rns/INTERNALS.md
§4.2** — and the app_data / announce-field split is in **lxmf/INTERNALS.md §8b
and §9**. This file keeps only what a plan is for: what shipped, and what has
not yet been proven on real hardware.

## What shipped

| | |
|---|---|
| Validate ratcheted announces | `Identity::validate_announce`, via `announce_ratchet()` / `announce_app_data_offset()` |
| Ratchet off the consumer path | `Transport::inbound` fan-out and `Identity::recall_app_data` slice by context flag; the fan-out frame carries `ratchet(32)` as its own field |
| Recall a peer's ratchet | `Identity::get_ratchet()` — read back out of the retained announce, no separate store |
| Encrypt to it | `Identity::encrypt(plaintext, ratchet)`, `Destination::encrypt`, `rnsdEncryptFor(pubkey, dest_hash, …)` |
| Advertise ours | `Destination::enable_ratchets/rotate_ratchets`, announce insertion + context flag, `secrets.rnsd.ratchets.<dest_hex>`, `s.rnsd.ratchets` (default 1) |
| Open what arrives | `Identity::decrypt(token, ratchets)` trial decrypt, `Destination::decrypt`, `rnsdDecryptSelf(identity_key, dest_hash, …)` |

Consumers updated for the frame change: lxmf (which also lost its
offset-32 app_data guesses), nomad, rlpg.

Deliberately **not** implemented: `enforce_ratchets`. Upstream defaults it off,
and turning it on drops mail from every sender that has not heard a current
announce.

## Desk verification

Untested on hardware so far — this is where a wrong byte would show. In rough
order of what breaks worst if wrong:

1. **Our announces still validate everywhere.** Flash one node, watch a stock
   client (Sideband / MeshChat / NomadNet) accept the announce and show the
   name and stamp cost. A mis-ordered `signed_data` or a context flag without a
   ratchet makes us invisible to the whole mesh, so this is the first check.
2. **Ratcheted peers still parse.** Their names and costs must still land in the
   announce catalogue, and `lxmf.announces.<dest>.ratchet` must now be filled
   from the frame field rather than guessed.
3. **Round trip, reticulous ↔ reticulous.** Opportunistic message both ways;
   confirm at `verb` level that the sender logs `encrypting to ratchet …` and
   the receiver opens with a ratchet, not the identity key.
4. **Round trip with a stock client**, both directions — the interop case that
   the trial decrypt and the identity-key fallback exist for.
5. **Across a rotation and a reboot.** `RATCHET_INTERVAL` is 12 h, so force it
   by clearing `secrets.rnsd.ratchets.<dest>` (fresh set on next announce) or by
   shortening the constant on a test build. A message deposited on an RLPG
   mailbox or a propagation node before a reboot must still open after it —
   that is what persisting the privates is for.
6. **Cost sanity.** A non-ratcheted sender's packet pays an ECDH per retained
   ratchet before the identity key is reached (32 of them). Watch that inbound
   opportunistic traffic from a stock client doesn't visibly stall the rnsd
   task; if it does, the retained count is the dial.
