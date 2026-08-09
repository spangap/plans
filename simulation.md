# Simulation — a virtual radio and a virtual ether

> Status: **design notes, nothing built.** Captured from a design conversation so
> the thinking is not lost. Not scheduled, not scoped, no owner. Help may be
> coming for this; these notes are written to be handed over.

## 0. The shape

A LoRa radio that behaves like an SX1262 to the code above it, but sends each
frame as a UDP datagram to a server — "the ether" — that holds every station's
position in a virtual 3D space. The ether decides, from the transmitter's power
and the geometry, which other stations hear the frame cleanly, which hear it as
a CRC error, and which merely have their noise floor raised by it. Frames that
are heard are delivered to the receiving station, which hands them to its normal
receive callback.

To any code above it, it is just a radio. Any number of stations can join.

```
station A ──┐
station B ──┼──► ether ──► per-station verdict: clean / CRC error / noise only
station C ──┤              (position, tx power, path loss, capture, sync word,
station D ──┘               occupancy, noise accumulation)
```

## 1. Why this is worth building

Three specific things we currently cannot do, in descending order of pain:

**Retune turnaround is unmeasurable on hardware without a scope.** The two
constants that govern every deadline in SUPE are guesses. In a simulator the
virtual radio charges the datasheet times and is genuinely deaf while retuning,
so a wrong constant shows up as deterministic frame loss in a test rather than
as a mystery on air. This alone would retire several open items in the spec.

**Contention behaviour needs more than two nodes.** Everything interesting about
carrier sense, deferral, detour collisions and the deafness window happens with
four to ten stations. We have two boards.

**The departure policy is unanswerable by argument.** When a detour is worth
taking, how long to hold traffic, how to treat a peer's application latency —
these want to be measured against synthetic traffic patterns, not reasoned about.
See §7.

## 2. The seam: a RadioLib-compatible virtual radio

Our firmware talks to the radio exclusively through RadioLib. If the virtual
class matches the surface we actually use, **the same `lora.cpp` compiles and
runs on the host** — not a model of our code, our code.

The surface is small, roughly twenty methods:

```
begin, standby, sleep
setFrequency, setBandwidth, setSpreadingFactor, setCodingRate
setPreambleLength, setSyncWord, setOutputPower, setCRC
startTransmit, finishTransmit
startReceive, readData, getPacketLength
getRSSI, getSNR, getFrequencyError
setPacketReceivedAction, setPacketSentAction
scanChannel, getTimeOnAir
```

Return the same error codes RadioLib returns, including the ones we branch on.

This is the highest-leverage decision in the whole design: matching an existing
API means no porting layer in the firmware and no divergence between what is
simulated and what ships.

## 3. Time

Two modes, and the ether owns the clock in both.

**Virtual time (default).** Stations block on "advance me to T"; the ether
releases them in order. Fully deterministic and reproducible from a seed, and it
can run far faster than real time — which is what makes a thousand-seed overnight
sweep possible.

**Real time.** The ether tracks wall clock. Slower, non-deterministic, but lets
unmodified external processes join — notably the Reticulum reference
implementation in Python.

Determinism is the point of the exercise: a six-node race that cannot be
reproduced cannot be debugged. So virtual time is the default and real time is
the switch you flip when you want Python in the room.

**Consequence for the firmware:** `millis()` and the timer must both route
through a single monotonic clock module, which becomes the porting seam. That
module is worth having regardless — four subsystems each handling 32-bit wrap
their own way is a bug waiting for an uptime of 49.7 days.

## 4. What the ether must model

In priority order. Note that propagation is *not* first: it is the easy part and
the least decision-relevant.

**1. Capture effect.** Two overlapping frames: the stronger wins if it is roughly
6 dB above the other *and* arrived within the weaker's preamble; otherwise both
are lost. This is what actually determines LoRa behaviour under contention. Get
it wrong and carrier sense looks either much better or much worse than reality,
and every conclusion drawn downstream is worthless.

**2. Wrong sync word raises the noise floor but is not received.** This is the
precise mechanism SUPE's private channels depend on. Unmodelled, the central
premise of the protocol goes untested.

**3. Retuning costs time and you are deaf during it.** Charge the datasheet
numbers — TS_HOP 30 µs, TS_FS 40 µs, TS_OSC 150 µs, PA ramp 10 µs to 3.4 ms —
and drop anything that arrives mid-retune. Best value per line in the whole
thing.

**4. Occupancy and airtime.** Time on air from the real formula; a channel is
occupied for the frame's duration; a receiver must be tuned for the *whole*
frame to get it. That is what catches deafness-window bugs mechanically instead
of by inspection.

**5. Noise accumulation** from concurrent transmissions, which is what makes
clear-channel assessment meaningful.

**6. CRC errors.** Do not model bit errors. Inside the capture margin band, mark
the frame as a CRC error with some probability. Our code paths treat CRC errors
distinctly and need to see them.

**Explicitly not modelled at first:** multipath, fading, Doppler, antenna
patterns, temperature drift. Realistic, but not decision-relevant for anything
being decided.

**Path loss** itself: free space plus a configurable exponent, plus optional
per-pair fixed obstruction in dB. Positions in a config file. Static before
mobile.

## 5. The ether as referee

The ether sees everything, so it can assert rather than merely report. End-of-run
assertions worth having:

- no station exceeded 100 s of transmission in any 3600 s window on any 500 kHz
  channel
- no station returned to a frequency inside the 100 ms minimum off-time
- no station exceeded the channel's radiated power cap
- every transmission was preceded by a clear-channel assessment within the
  declared dead time

That turns regulatory compliance from a code review into a test that fails.

## 6. A virtual RNode serial interface

Worth more than it sounds. It lets the Reticulum reference implementation join as
an ordinary station, which makes it an **oracle** for two questions we currently
answer by reading Python:

- what the daemon does when an interface stops accepting outbound packets
  (the backpressure question — we can only say take-it-or-wait, never "take
  these and hold those")
- whether real Reticulum behaves the way our neighbour and identity inference
  assumes

It is mostly framing code sitting on a pty or a socket.

## 7. What this is for

The immediate consumer is the departure policy. SUPE deliberately puts that
decision in one pure function:

```
should_detour(peer_state, queue_state, channel_state) -> { no | now | wait_until(t) }
```

no side effects, no radio access, one call site. The traffic patterns to run it
against:

- **interactive ping-pong** — an rnsh session, which stalls for a delivery proof
  before sending more; the case where a naive hold timer is strictly harmful
- **bulk transfer** — a resource, receiver-driven, batches of parts
- **contended gateway** — many stations, one transport node, all traffic tagged
  with the same identity
- **absent peer** — how much is spent discovering nothing is there
- **mixed segment** — SUPE and non-SUPE nodes sharing the channel, where the
  question is whether we are a good neighbour

Committing to a policy now would be the mistake. Committing to where the policy
lives costs nothing, and it is already done.

## 8. Risks

**A simulator that is subtly wrong gives confidence rather than information.**
The mitigation is cheap: calibrate against the few things measurable on real
hardware — time on air (exactly), received signal strength at two known
distances, detour success rate — and keep those as regression checks on the ether
itself.

**Scope creep towards physical realism.** The list in §4 is ordered; the tail of
it is where the temptation lives and where the value stops.

## 9. Transport and mechanics

UDP to a central ether keeps the distributed and Python cases free and costs
nothing. Do not optimise to a Unix socket or shared memory until it is
demonstrably slow.

Configuration: a scenario file holding station positions, radio families,
interface settings, path-loss exponent, obstructions, and the seed. One file
reproduces one run exactly.
