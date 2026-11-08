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
