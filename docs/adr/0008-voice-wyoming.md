# 0008 — Voice satellites speak the Wyoming protocol

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR wants voice in. The maintainer has a homelab with multiple
rooms, multiple potential mic/speaker endpoints (Atom Echo,
Raspberry Pi, repurposed Echo Dot, eventually beamforming arrays),
and a strong preference for local-first audio. Building a bespoke
audio protocol would also mean building the satellite firmware
ecosystem, which is a separate engineering project entirely.

The Home Assistant ecosystem solved this with the **Wyoming**
protocol — a local, open audio protocol that already has
implementations for wake-word detection, ASR, TTS, and satellite
endpoints. Riding it lets CAESAR meet voice users where they already
are.

## Decision

CAESAR's voice satellites speak the **Wyoming protocol**. The
Voice Satellite is logically:

- Wake-word detection on the satellite (openWakeWord, microWakeWord,
  Porcupine — operator's choice).
- ASR on the satellite or pushed to a CAESAR-side worker, depending on
  hardware.
- TTS rendered by CAESAR (so personality lives near the brain) and
  streamed back to the satellite for playback.
- Wyoming events flow into Praetor as a stream that gets normalized
  into intents before any policy decision.

Praetor exposes a Wyoming server endpoint; satellites are clients.

## Alternatives considered

- **Rolling our own protocol** — buys nothing, costs forever.
- **WebRTC end-to-end** — strong if we needed browser-based voice,
  overkill for dedicated satellites.
- **Snapcast / MQTT audio bridges** — works for audio out, not
  designed for the wake-word + ASR + intent loop.
- **Cloud assistants (Alexa, Google) as the front-end** — sends every
  utterance off-device. Hard veto.

## Consequences

### Positive

- Instant compatibility with HA's voice satellite ecosystem and
  off-the-shelf hardware.
- Local-first by default; no audio leaves the home without explicit
  configuration.
- Wake word + ASR engines are pluggable per-satellite.

### Negative

- We commit to Wyoming as the audio protocol. If the ecosystem
  stalls, so do we.
- Some satellite hardware (notably commercial smart speakers) cannot
  speak Wyoming without flashing custom firmware.

### Neutral

- Whether CAESAR's intent classifier consumes raw ASR text or
  pre-normalized intents from HA's Assist pipeline is a later
  decision; both options are open under this ADR.

## References

- [Wyoming protocol](https://github.com/rhasspy/wyoming)
- [HA Voice satellites](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/)
- [openWakeWord](https://github.com/dscripka/openWakeWord)
