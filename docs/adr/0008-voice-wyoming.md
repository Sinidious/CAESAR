# 0008 — Voice input via client-side transcription

- Status: Accepted (rewritten 2026-05-16: dropped the Wyoming
  satellite plan in favour of client-side audio; CAESAR exposes HTTP
  and never touches a microphone)
- Date: 2026-05-15 (original); 2026-05-16 (rewrite)
- Deciders: @sinidious

> **Note on the rewrite.** The first version of this ADR committed
> CAESAR to the Wyoming protocol and a fleet of dedicated voice
> satellites (Atom Echo, Pi, beamforming arrays). On reflection, the
> maintainer wants voice to ride hardware they already own — phone
> with OS-level dictation, laptop browser, etc. — and does not want
> to maintain satellite firmware. The original decision is preserved
> below in *Alternatives considered* as "dedicated voice satellites
> via Wyoming"; this rewrite is the current direction.

## Context

CAESAR wants voice in. The maintainer has:

- A phone with first-class voice dictation (Gboard / iOS keyboard,
  Whisper-based system services).
- A laptop with browser-level Web Speech API and OS dictation.
- No appetite for owning and firmware-maintaining a separate fleet of
  audio satellites.

The Home Assistant ecosystem solved the **hardware** side of voice
with the Wyoming protocol. Adopting Wyoming would mean adopting (and
maintaining) the satellite story. The transcription side is a solved
problem on every modern client, locally.

## Decision

**CAESAR does not own the microphone.** Voice is captured and
transcribed by the user's existing device (phone keyboard dictation,
laptop browser, OS speech-to-text); the resulting text is sent to
Praetor over HTTP. Praetor exposes a single conversational endpoint
(`POST /v1/chat`) that:

- Accepts a message list (text only).
- Runs a tool-using LangGraph that lets the LLM emit `call_service`
  tool invocations — the brain's way of taking action.
- Dispatches every tool call through the Policy Engine
  ([ADR-0013](0013-policy-engine.md)) before reaching the HA Bridge
  ([ADR-0007](0007-home-assistant-bridge.md)).
- Audits the conversation, the tool call, and the resulting service
  call.

There is no dedicated CAESAR satellite hardware, no Wyoming server,
and no audio streaming in Praetor.

## Alternatives considered

- **Dedicated voice satellites via Wyoming** (the original decision in
  this slot). Strong fit for HA's ecosystem and pluggable wake-word /
  ASR engines; rejected on rewrite because it adds a hardware track
  and firmware burden the maintainer doesn't want. We may revisit if
  a real always-on-listening use case appears.
- **Audio upload to Praetor + server-side Whisper.** Workable
  fallback for clients without local STT; deferred until a real
  client needs it. Endpoint would be `POST /v1/voice`, gated by the
  same Policy as text input.
- **WebRTC end-to-end.** Overkill for a single-household service.
- **Cloud assistants (Alexa, Google) as the front-end.** Sends every
  utterance off-device. Hard veto.

## Consequences

### Positive

- No hardware on CAESAR's critical path. The phone in the user's
  pocket is already the best voice client.
- Audio never leaves the device unless the user picks a cloud
  dictation service. CAESAR sees text only.
- The conversational HTTP API is the same surface every future client
  uses — dashboard, PWA, mobile app, CLI.
- No Wyoming/firmware track to maintain.

### Negative

- "Hands-free always listening" requires a client app that handles
  wake-word + push the transcript. Not in v0.2's scope.
- The text-only path drops some semantic signal (prosody, emphasis,
  who-is-speaking). Tolerable for an assistant tier.

### Neutral

- If we ever need server-side transcription, `POST /v1/voice` is the
  natural place; it doesn't conflict with this ADR.

## References

- [HA voice control (informational)](https://www.home-assistant.io/voice_control/)
- [Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API)
- [ADR-0007 — Home Assistant Bridge](0007-home-assistant-bridge.md)
- [ADR-0013 — Policy engine](0013-policy-engine.md)
