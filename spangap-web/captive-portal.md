# Captive portal → real-browser handoff (plan)

Status: design / not yet implemented.

## Goal

When a phone joins a spangap device's SoftAP, get the user into their
**real browser** on the device's web UI over **HTTPS** — because WebRTC
(every browser↔device data path in `spangap-web`) requires a secure
context, and the captive mini-browser is too sandboxed to host the app.

The captive portal is used purely as a **launcher**, never as the app
host.

## Why HTTPS is non-negotiable here

WebRTC (`getUserMedia`, and increasingly `RTCPeerConnection` itself)
only runs in a secure context. On a phone over WiFi that means real
HTTPS — `localhost` is exempt but only helps code on the device, not
connecting phones. So plain HTTP is out for the app itself; HTTP is only
used for the captive launcher page (see below).

Note the WebRTC *media/data* path uses self-signed DTLS with fingerprint
verification (no CA) — fine. The cert problem is purely about loading
the SPA **document** in a secure context (`isSecureContext === true`)
and the same-origin `wss` signaling (`deviceWssBase()` is same-origin).

## The cert problem and the chosen tradeoff

You cannot have all three of: zero-install, works-perpetually-offline,
zero-warning. This is CA/Browser-Forum policy, not engineering — no
publicly-trusted cert is valid indefinitely offline (max ~398 days and
shrinking). reticulous is off-grid-first, so "never online" is a primary
case.

**Decision: accept a one-time self-signed click-through.** It needs no
internet ever and works on every platform; the cost is an ugly warning
the user taps past once. After proceeding, the origin is a secure
context and WebRTC works (verify per target iOS version, but holds in
practice). The green padlock is unattainable for a perpetually-offline
device without a one-time trust step — so we make that one tap painless
rather than try to engineer it away.

The existing `acme`/`duckdns` real-cert path still applies when the
device has been online: `tls` hot-swaps `/state/tls_cert.pem`, so the
captive layer is built once and the cert underneath it can be either the
real ACME cert (green padlock) or self-signed (click-through). Only
*which cert* `tls` serves changes; the captive plumbing is identical.

## Mechanism: how the portal pops and how it goes away

The OS decides a network is "open internet" iff its **connectivity
probe** gets the exact expected response. You never command the CNA to
close — you satisfy (or fail) the probe, and dismissal follows.

Probe endpoints and their success sentinels (all hit our HTTP server
because DNS is hijacked — dispatch on `Host` + path):

| OS      | Probe URL                                              | Success response                                                        |
| ------- | ------------------------------------------------------ | ----------------------------------------------------------------------- |
| Apple   | `captive.apple.com/hotspot-detect.html`, `/library/test/success.html` | `200`, body exactly `<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>` |
| Android | `connectivitycheck.gstatic.com/generate_204`, `/generate_204` | `204 No Content`, empty body                                            |
| Windows | `www.msftconnecttest.com/connecttest.txt`              | `200`, body `Microsoft Connect Test`                                    |

Apple's probe carries User-Agent `CaptiveNetworkSupport`, so OS probes
are distinguishable from real Safari traffic.

### Per-client state machine

First probe must FAIL (CNA pops with our page); a later probe must
SUCCEED (CNA dismisses). So track state per client (key on DHCP source
IP or MAC):

- **UNSOLVED** (new client): probe → serve HTTP instruction page (or
  302 to it). CNA pops.
- **RELEASED** (user engaged): probe → return the success sentinel.
  Next re-probe dismisses the captive view.

### The flip trigger — and the two gotchas

Instruction page ends with a button that flips the client, then hands
off:

```js
btn.onclick = () => {
  fetch('/captive/release', {keepalive: true});      // flip client → RELEASED
  location = <platform-specific escape URL>;          // jump to real browser
};
```

- **Don't flip too early.** If a client is RELEASED before its first
  probe, the CNA never appears and instructions never show.
- **Don't let them leave before the flip.** The CNA Done/Cancel button
  forces an immediate re-probe; if it still FAILS, iOS warns and may
  drop the WiFi association — killing the AP. Beacon-then-navigate
  guarantees RELEASED first, so Done succeeds cleanly and WiFi stays.

## Escaping the mini-browser to the real browser (per platform)

There is **no single scheme** that escapes on both platforms — branch on
User-Agent when serving the page:

| Platform    | Detect (UA contains)                       | Escape                                            |
| ----------- | ------------------------------------------ | ------------------------------------------------- |
| iOS / macOS | `CaptiveNetworkSupport`, `iPhone`/`iPad`/`Mac OS X` | `x-safari-https://<name>/` (plain `https` stays trapped in CNA) |
| Android     | `Android` (login view = `com.android.captiveportallogin`) | `intent://<name>/#Intent;scheme=https;end` — blocked on some OEMs, see fallback |
| Desktop/other | anything else                            | plain `https://<name>/`                           |

The scheme tricks are an *enhancement*. The universal backbone that
needs no scheme magic: **flip to RELEASED → OS auto-dismisses the
captive view → user opens their own browser.** On Android, returning
`204` marks the network validated and auto-closes the
`CaptivePortalLogin` activity — that's the native exit. So every
platform's page copy ends with the guaranteed fallback line:

> If the app didn't open, open your browser and go to **https://&lt;name&gt;**
> — tap through the security warning.

Repeat the "tap through the warning" line on the HTTPS landing page too,
since the warning interrupts before the user sees anything.

## Coverage: who misses, and the DNS-proof fallback

Because the AP DNS resolves *everything* to the device IP, and the AP is
the client's only connectivity, the failure surfaces are:

**HTTPS cannot be intercepted — ever.** A TLS handshake for any name
other than our cert's name fails on name-mismatch *before* any HTTP
request exists, so you can't redirect it. HSTS-preloaded domains
(google.com, …) hard-fail with no override. So **the only HTTPS URL we
ever send users to is `https://<name>`** (or the raw IP). Drive the
flow through HTTP first.

**Weird HTTP paths** need a **catch-all: any unrecognized HTTP request →
302 to the instruction page.** Without it random hits 404.

**Background-app TLS storm.** Every name resolving to us means the
phone's mail/push/sync all open TLS to :443 on join, each a full
mbedTLS handshake doomed to cert-mismatch — real wasted CPU/RAM on
T-Deck (already DRAM/DMA-constrained). Reject unknown-SNI handshakes
cheaply where `tls` accepts connections.

**DoH/DoT does NOT leak on a sole-connectivity AP.** Earlier assumption
was wrong: DoH can't reach its own resolver (the connection to the DoH
server goes to our gateway, which has no upstream), so the probe name
does not resolve to its real IP.
- **Opportunistic / automatic DoH (common default):** DoH fails →
  client falls back to the DHCP-advertised resolver (us) → hijack works,
  portal pops. **Not a miss.**
- **Strict / no-fallback DoH/DoT** (Android Private-DNS hostname,
  Firefox max-protection, strict iOS encrypted-DNS profile): DNS dies
  entirely — no portal, and `https://<name>` won't resolve either.

**The raw IP is the DNS-proof escape hatch.** The local subnet is always
reachable over WiFi regardless of internet verdict / encrypted-DNS /
detection state. `https://192.168.1.1` (default `s.net.wifi.ap.ip`)
works for every miss category, needs no DNS, and — since we're doing
click-through anyway — the cert name-mismatch at the IP is just part of
the same warning. After proceed, `https://<ip>` is a secure context and
WebRTC works (origin being an IP doesn't matter).

So **publish both**: friendly `https://<name>` (happy/auto-pop path) and
`https://192.168.1.1` (typed fallback that rescues every miss). Print
the IP on the device / in docs. Real "stuck" population ≈ 0 if the IP is
advertised.

### Revised miss tally

- Auto-pop: most consumer phones, including opportunistic-DoH ones.
- No popup but reachable at raw IP: strict-DoH, detection-disabled,
  IoT/non-browser clients, desktop Linux, some cached-state phones.

## Config dependency: dhcps must advertise DNS

For the opportunistic-DoH fallback and the hijack generally, `dhcps`
must advertise the device as the DNS server (DHCP option 6) via
`esp_netif_dhcps_option(... ESP_NETIF_DOMAIN_NAME_SERVER ...)` on the AP
netif — ESP-IDF dhcps does not necessarily offer one by default. Set it
in `startAP()` (`spangap-net/esp-idf/src/net.cpp:843`) right where dhcps
starts. Without it, even opportunistic-DoH clients drop to the IP-only
bucket.

## Modern layer (optional, do on top): RFC 8908 + DHCP option 114

On iOS 14+/Android 11+, advertise the portal URL via **DHCP option 114**
(`esp_netif_dhcps_option`, same place as option 6) and serve an
**RFC 8908** JSON endpoint from `spangap-web`. The OS then surfaces a
native "Open" affordance pointing at `https://<name>` instead of relying
on probe-hijacking. Keep probe-hijack + `x-safari-https` as the fallback
for older OSs. Composes with `acme`'s "passive code that only compiles
when `spangap-web` is staged" pattern.

## Where it lives

A new **`captive` straddle**, mirroring `acme`/`duckdns`:

- `esp-idf/` — AP-side DNS responder (UDP/53, answers all A queries with
  the AP IP), probe-URL dispatch + per-client state table, the
  `/captive/release` flip endpoint, catch-all 302, option-6/114 setup.
  Depends on `spangap-net` (AP netif, dhcps, the cert it serves).
- Passive RFC-8908 / launcher web routes that compile only when
  `spangap-web` is staged (register via
  `itsSendAux("web", WEB_PATH_REG_PORT, …)`).
- UA branch lives in the instruction-page handler; probe dispatch, state
  table, release endpoint, and catch-all are platform-independent.

## Build order (suggested)

1. AP DNS responder + dhcps option-6 (DNS) — makes `<name>` and
   everything resolve to the device on the AP.
2. Probe dispatch + per-client state table + `/captive/release` flip +
   catch-all 302.
3. UA-branched instruction page (HTTP) with platform escape + universal
   fallback copy; HTTPS landing repeats the click-through line.
4. Unknown-SNI fast-reject in `tls`.
5. (Optional) RFC 8908 endpoint + DHCP option 114.

## Open item

- **Clock for the real-cert path only:** offline TLS validity needs a
  sane clock; irrelevant to the self-signed click-through default
  (no validity trust), relevant only if a device serves the ACME cert
  offline. Rely on RTC / last-NTP; a client-clock-seed endpoint is a
  possible mitigation but chicken-and-egg with the TLS handshake.
