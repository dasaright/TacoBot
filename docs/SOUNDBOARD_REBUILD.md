# Soundboard audio rebuild — desktop client

Status: client implementation in progress on `rebuild/soundboard-audio`. Production `main` and the VPS are unchanged.

## Scope

Preserve the existing relay GUI, clip library, labels, sorting, shared-clip sync, volume controls, and clip editing. Replace the soundboard playback backend. Do not alter YouTube/music playback in this project.

Remove the ambiguity of the current `direct_soundboard` split: a normal clip click now always targets Discord through RunsForming. DirectAudio remains an explicit optional local route available only through a clip's right-click menu; it no longer intercepts normal Play.

## Client responsibilities

1. Resolve the selected clip ID to a local file.
2. Validate that the file still exists and can be decoded.
3. Decode with FFmpeg to signed 16-bit little-endian, 48 kHz, stereo PCM.
4. Stream bounded, fixed-duration audio frames to the VPS soundboard endpoint.
5. Obey per-session flow control and cancellation.
6. Report lifecycle states distinctly: requested, accepted, decoding, streaming, server-buffered, playing, completed, cancelled, failed.
7. Never report audible playback merely because FFmpeg started or the server accepted a session.

The dedicated `relay_agent/soundboard_transport.py` module owns the WebSocket, FFmpeg PCM stream, flow control, pacing, and server lifecycle handling. Both the packaged Qt controller and retained Tk fallback call the RelayAgent method that uses this module; neither GUI event handler implements transport itself.

## Protocol v2 client contract

Use a dedicated soundboard WebSocket endpoint, separate from the existing general relay protocol. Negotiate an explicit protocol version and authenticate using the existing relay credential mechanism without logging or exposing the credential.

Each clip is a session with a cryptographically random 16-byte session ID. Control messages are structured JSON. Audio is binary with the 27-byte network-order header `!4sB16sIH`: magic `SBV2`, protocol version, session ID, uint32 frame sequence, and uint16 payload length. The payload is exactly 3,840 bytes. The client obeys the server's 12-frame initial credit window and subsequent credit updates.

Initial PCM format: 48,000 Hz, stereo, signed 16-bit little-endian, 20 ms frames (3,840 payload bytes). This is deliberately explicit and independently validated at both ends. Do not reuse the old 32-character ASCII request-ID prefix.

The server grants bounded credits/window capacity. The client must not send unbounded audio or burst an entire clip into a socket buffer. Sequence gaps and backpressure must be visible in session diagnostics.

## Playback behavior

- Support up to eight local concurrent clip tasks; the server independently enforces its per-identity and per-guild limits.
- Cancellation is per session through client-task cancellation and WebSocket disconnect; the server also supports an explicit cancel control. Stop clips cancels all local soundboard tasks and invokes the legacy stop path for TTS/rollback.
- Clip completion is reported only after the FFmpeg process has ended and the server reports that its session queue has drained.
- Disconnects fail active sessions visibly; reconnect does not resume stale audio.
- The existing normalization, FFmpeg volume filter, per-clip volume transform, and tempo filter are retained in the new send path. They still require Windows fixture and audible-output comparison tests.
- Normal click uses Discord regardless of DirectAudio preference. DirectAudio is exposed as a separate right-click action.

## Staging build

The Windows relay workflow successfully built and uploaded the branch artifact in [Build Windows Relay run #101](https://github.com/dasaright/TacoBot/actions/runs/42434864991). The downloaded bundle contains `TacoBot.exe`, `Files/ffmpeg.exe`, `Files/yt-dlp.exe`, and the README. The run passed Qt desktop initialization, the full relay/installer test suite, real FFmpeg soundboard streaming, the frozen executable self-test, and the frozen installer/restart test. This is a branch Actions artifact only; it did not publish a GitHub release, change the public auto-update feed, or send a Discord notification.

The coordinated VPS/Caddy test sequence and rollback boundary are documented in the RunsForming repository's [SOUNDBOARD_STAGING.md](https://github.com/dasaright/RunsForming/blob/rebuild/soundboard-audio/docs/SOUNDBOARD_STAGING.md). Do not run this client against the public relay until the server branch and Caddy `/soundboard` route have been staged.

## Tests

Current CI coverage (green on the rebuild branch) verifies endpoint derivation, the exact binary header layout, session ID/sequence/payload fields, incomplete PCM rejection, and a full local WebSocket transport exchange using a stub FFmpeg source. The Windows build also runs the real `SoundboardTransport` against a local WebSocket server using the downloaded FFmpeg executable and a generated one-second WAV; it validates PCM frame sequencing, payload size, server-buffered/playing events, and server-confirmed completion. The full Windows relay test suite and Qt desktop smoke test passed, and the frozen executable and updater/restart checks passed.

Still required before cutover:
- Real FFmpeg decode failure, short/empty clip, early EOF, and process-cancellation edge cases.
- Backpressure/credit behavior against a deliberately slow server over longer clips.
- Per-session and stop-all client cancellation under relay disconnect and repeated-start/stop races.
- End-to-end test using the Windows client and staged gateway; confirm audible Discord playback with a listener.
