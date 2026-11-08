import asyncio
import struct
import unittest
import uuid

from relay_agent.soundboard_transport import (
    FRAME_HEADER,
    FRAME_MAGIC,
    PCM_FRAME_BYTES,
    PROTOCOL_VERSION,
    SoundboardTransport,
    soundboard_endpoint,
)


class SoundboardTransportTests(unittest.TestCase):
    def test_endpoint_replaces_only_relay_suffix(self):
        self.assertEqual(
            soundboard_endpoint("wss://example.test/relay"),
            "wss://example.test/soundboard",
        )
        self.assertEqual(
            soundboard_endpoint("wss://example.test/base/relay/"),
            "wss://example.test/base/soundboard",
        )
        self.assertEqual(
            soundboard_endpoint("wss://example.test/base"),
            "wss://example.test/base/soundboard",
        )

    def test_protocol_v2_frame_header_matches_server_contract(self):
        session_id = uuid.uuid4().hex
        payload = bytes(PCM_FRAME_BYTES)
        packet = SoundboardTransport._frame(session_id, 17, payload)
        self.assertEqual(len(packet), FRAME_HEADER.size + PCM_FRAME_BYTES)
        magic, version, session_bytes, sequence, payload_length = FRAME_HEADER.unpack_from(packet)
        self.assertEqual(magic, FRAME_MAGIC)
        self.assertEqual(version, PROTOCOL_VERSION)
        self.assertEqual(session_bytes.hex(), session_id)
        self.assertEqual(sequence, 17)
        self.assertEqual(payload_length, PCM_FRAME_BYTES)
        self.assertEqual(packet[FRAME_HEADER.size:], payload)

    def test_rejects_incomplete_pcm_frame(self):
        with self.assertRaises(ValueError):
            SoundboardTransport._frame(uuid.uuid4().hex, 0, b"short")

    def test_frame_header_layout_is_fixed(self):
        self.assertEqual(FRAME_HEADER.format, "!4sB16sIH")
        self.assertEqual(FRAME_HEADER.size, 27)


if __name__ == "__main__":
    unittest.main()


class _FakePCMReader:
    def __init__(self, frames=()):
        self.frames = list(frames)

    async def readexactly(self, size):
        if self.frames:
            frame = self.frames.pop(0)
            if len(frame) != size:
                raise AssertionError("Unexpected fake PCM frame size.")
            return frame
        raise asyncio.IncompleteReadError(partial=b"", expected=size)


class _FakeStderr:
    async def read(self, _size):
        return b""


class _FakeFFmpeg:
    def __init__(self, frames):
        self.stdout = _FakePCMReader(frames)
        self.stderr = _FakeStderr()
        self.returncode = None

    async def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


class SoundboardTransportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_streams_pcm_and_waits_for_server_completion(self):
        import asyncio
        import json
        import uuid
        from array import array
        from pathlib import Path
        from unittest.mock import patch

        from websockets.asyncio.server import serve

        session_id = uuid.uuid4().hex
        pcm_frame = array("h", [1250]) * (PCM_FRAME_BYTES // 2)
        frames_to_send = [pcm_frame.tobytes(), pcm_frame.tobytes()]
        received_frames = []
        controls = []

        async def server_handler(websocket):
            hello = json.loads(await websocket.recv())
            self.assertEqual(hello["type"], "hello")
            self.assertEqual(hello["protocol"], PROTOCOL_VERSION)
            self.assertEqual(hello["token"], "test-token")
            await websocket.send(json.dumps({
                "type": "hello_ok",
                "protocol": PROTOCOL_VERSION,
                "sample_rate": 48000,
                "channels": 2,
                "frame_ms": 20,
                "frame_bytes": PCM_FRAME_BYTES,
            }))

            start = json.loads(await websocket.recv())
            self.assertEqual(start["type"], "start")
            self.assertEqual(start["session_id"], session_id)
            await websocket.send(json.dumps({
                "type": "accepted",
                "protocol": PROTOCOL_VERSION,
                "session_id": session_id,
                "guild_id": "123",
                "window": 12,
                "frame_bytes": PCM_FRAME_BYTES,
            }))

            async for raw in websocket:
                if isinstance(raw, bytes):
                    received_frames.append(raw)
                    continue
                control = json.loads(raw)
                controls.append(control)
                if control.get("type") == "end":
                    await websocket.send(json.dumps({
                        "type": "server_buffered",
                        "session_id": session_id,
                        "frames": len(received_frames),
                    }))
                    await websocket.send(json.dumps({
                        "type": "playing",
                        "session_id": session_id,
                        "first_non_silent_frame": 1,
                    }))
                    await websocket.send(json.dumps({
                        "type": "completed",
                        "session_id": session_id,
                        "frames_received": len(received_frames),
                        "frames_consumed": len(received_frames),
                        "underruns": 0,
                        "played": True,
                    }))
                    break

        async with serve(server_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            transport = SoundboardTransport(
                f"ws://127.0.0.1:{port}/relay",
                "test-token",
                "Test relay",
                None,
            )
            fake_process = _FakeFFmpeg(frames_to_send)
            states = []

            async def fake_create_subprocess_exec(*_args, **_kwargs):
                return fake_process

            with patch(
                "relay_agent.soundboard_transport.asyncio.create_subprocess_exec",
                side_effect=fake_create_subprocess_exec,
            ):
                result = await transport.play_file(
                    session_id=session_id,
                    guild_id="123",
                    file_id="fixture",
                    title="Fixture",
                    source_path=Path("fixture.wav"),
                    ffmpeg_path=Path("ffmpeg.exe"),
                    audio_filter="volume=0dB",
                    transform_frame=lambda frame: frame,
                    on_state=states.append,
                )

        self.assertEqual(result["type"], "completed")
        self.assertTrue(result["played"])
        self.assertEqual(len(received_frames), 2)
        self.assertEqual([item["type"] for item in controls], ["end"])
        for sequence, packet in enumerate(received_frames):
            magic, version, session_bytes, actual_sequence, payload_length = FRAME_HEADER.unpack_from(packet)
            self.assertEqual(magic, FRAME_MAGIC)
            self.assertEqual(version, PROTOCOL_VERSION)
            self.assertEqual(session_bytes.hex(), session_id)
            self.assertEqual(actual_sequence, sequence)
            self.assertEqual(payload_length, PCM_FRAME_BYTES)
            self.assertEqual(len(packet), FRAME_HEADER.size + PCM_FRAME_BYTES)

        state_names = [event["soundboard_state"] for event in states]
        self.assertEqual(state_names[0], "accepted")
        self.assertIn("decoding", state_names)
        self.assertIn("streaming", state_names)
        self.assertIn("server-buffered", state_names)
        self.assertIn("playing", state_names)
        self.assertEqual(state_names[-1], "completed")
