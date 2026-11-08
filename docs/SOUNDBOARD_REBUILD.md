# Soundboard audio rebuild — desktop client

Status: design baseline for `rebuild/soundboard-audio`. This is not yet an implementation.

## Scope

Preserve the existing relay GUI, clip library, labels, sorting, shared-clip sync, volume controls, and clip editing. Replace the soundboard playback backend. Do not alter YouTube/music playback in this project.

Remove the ambiguity of the current `direct_soundboard` split: the rebuilt soundboard has one clearly identified destination, Discord through RunsForming. Local DirectAudio remains a separate optional feature only if explicitly retained later; it must not silently intercept the normal Play action.

## Client responsibilities

1. Resolve the selected clip ID to a local file.
2. Validate that the file still exists and can be decoded.
3. Decode with FFmpeg to signed 16-bit little-endian, 48 kHz, stereo PCM.
4. Stream bounded, fixed-duration audio frames to the VPS soundboard endpoint.
5. Obey per-session flow control and cancellation.
6. Report lifecycle states distinctly: requested, accepted, decoding, streaming, server-buffered, playing, completed, cancelled, failed.
7. Never report audible playback merely because FFmpeg started or the server accepted a session.

The GUI remains a control surface; audio transport and playback state belong in a dedicated client module, not in Qt/Tk event handlers.

## Protocol v2 client contract

Use a dedicated soundboard WebSocket endpoint, separate from the existing general relay protocol. Negotiate an explicit protocol version and authenticate using the existing relay credential mechanism without logging or exposing the credential.

Each clip is a session with a cryptographically random session ID. Control messages are structured JSON. Audio is binary with a documented fixed header containing a magic value, protocol version, session ID, monotonically increasing frame sequence, and payload length. Reject malformed headers, unknown sessions, wrong frame sizes, duplicates, and out-of-order frames according to the protocol rules.

Initial PCM format: 48,000 Hz, stereo, signed 16-bit little-endian, 20 ms frames (3,840 payload bytes). This is deliberately explicit and independently validated at both ends. Do not reuse the old 32-character ASCII request-ID prefix.

The server grants bounded credits/window capacity. The client must not send unbounded audio or burst an entire clip into a socket buffer. Sequence gaps and backpressure must be visible in session diagnostics.

## Playback behavior

- Support concurrent clip sessions up to a server-advertised limit.
- Cancellation is per session; stopping all clips is an explicit bulk operation.
- Clip completion is acknowledged only after the client has stopped its decoder and the server has drained or discarded the session buffer.
- Disconnects fail active sessions visibly; reconnect must not replay stale audio.
- Keep the current clip gain/normalization/speed behavior only after it is documented and tested. Do not silently change gain or tempo as part of transport work.

## Tests

- Deterministic PCM fixture generation and byte-for-byte frame validation.
- FFmpeg decode success, decode failure, and early EOF.
- Sequence, header, and payload validation.
- Backpressure and bounded memory under a slow receiver.
- Two or more simultaneous clips, per-session stop, stop-all, and relay disconnect.
- Verify the GUI displays each lifecycle state accurately.
