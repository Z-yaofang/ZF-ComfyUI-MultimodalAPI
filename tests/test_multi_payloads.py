"""Pure request-construction checks; no credentials or network calls."""

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "nodes.py"
SPEC = importlib.util.spec_from_file_location("zf_multi_payload_nodes", MODULE_PATH)
nodes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nodes)


def video(index):
    return {"label": f"<Video {index}>", "mime_type": "video/mp4", "data": f"video-{index}"}


def audio(index):
    return {"label": f"<Audio {index}>", "mime_type": "audio/wav", "format": "wav", "data": f"audio-{index}"}


class MultiPayloadTests(unittest.TestCase):
    def setUp(self):
        self.images = [{"label": "<Picture 3>", "mime_type": "image/jpeg", "data": "image-3"}]
        self.videos = [video(index) for index in (1, 2, 3)]
        self.audios = [audio(index) for index in range(1, 8)]

    def test_chat_keeps_every_video_audio_and_label_pair(self):
        content = nodes._openai_chat_payload("model", "role", "prompt", self.images, self.videos, self.audios)["messages"][1]["content"]
        self.assertEqual(content[:3], [
            {"type": "text", "text": "prompt"},
            {"type": "text", "text": "<Picture 3>"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,image-3"}},
        ])
        expected = []
        for item in self.videos:
            expected.extend([
                {"type": "text", "text": item["label"]},
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{item['data']}"}},
            ])
        for item in self.audios:
            expected.extend([
                {"type": "text", "text": item["label"]},
                {"type": "input_audio", "input_audio": {"data": item["data"], "format": "wav"}},
            ])
        self.assertEqual(content[3:], expected)

    def test_responses_keeps_every_video_audio_and_label_pair(self):
        payload = nodes._openai_responses_payload("model", "role", "prompt", self.images, self.videos, self.audios)
        content = payload["input"][0]["content"]
        self.assertEqual(payload["instructions"], "role")
        expected = []
        for item in self.videos:
            expected.extend([
                {"type": "input_text", "text": item["label"]},
                {"type": "input_video", "video_url": f"data:video/mp4;base64,{item['data']}"},
            ])
        for item in self.audios:
            expected.extend([
                {"type": "input_text", "text": item["label"]},
                {"type": "input_audio", "input_audio": {"data": item["data"], "format": "wav"}},
            ])
        self.assertEqual(content[3:], expected)

    def test_gemini_keeps_every_video_audio_and_label_pair(self):
        parts = nodes._gemini_payload("role", "prompt", self.images, self.videos, self.audios)["contents"][0]["parts"]
        expected = []
        for item in self.videos + self.audios:
            expected.extend([
                {"text": item["label"]},
                {"inline_data": {"mime_type": item["mime_type"], "data": item["data"]}},
            ])
        self.assertEqual(parts[3:], expected)

    def test_single_dictionary_and_one_item_list_are_identical(self):
        for builder in (nodes._openai_chat_payload, nodes._openai_responses_payload):
            with self.subTest(builder=builder.__name__):
                self.assertEqual(
                    builder("model", "role", "prompt", self.images, self.videos[0], self.audios[0]),
                    builder("model", "role", "prompt", self.images, self.videos[:1], self.audios[:1]),
                )
        self.assertEqual(
            nodes._gemini_payload("role", "prompt", self.images, self.videos[0], self.audios[0]),
            nodes._gemini_payload("role", "prompt", self.images, self.videos[:1], self.audios[:1]),
        )

    def test_empty_lists_match_none_and_chat_stays_plain_text(self):
        builders = (
            lambda v, a: nodes._openai_chat_payload("model", "", "prompt", [], v, a),
            lambda v, a: nodes._openai_responses_payload("model", "", "prompt", [], v, a),
            lambda v, a: nodes._gemini_payload("", "prompt", [], v, a),
            lambda v, a: nodes._anthropic_payload("model", "", "prompt", [], v, a),
        )
        for builder in builders:
            self.assertEqual(builder([], []), builder(None, None))
        self.assertEqual(builders[0]([], []), {"model": "model", "messages": [{"role": "user", "content": "prompt"}]})

    def test_supplied_non_contiguous_labels_are_not_renumbered(self):
        content = nodes._openai_chat_payload("model", "", "prompt", [], [video(3)], [audio(7)])["messages"][0]["content"]
        self.assertEqual([part["text"] for part in content if part["type"] == "text"], ["prompt", "<Video 3>", "<Audio 7>"])

    def test_payload_layer_does_not_truncate_to_node_port_limits(self):
        videos = self.videos + [video(4)]
        audios = self.audios + [audio(8)]
        content = nodes._openai_chat_payload("model", "", "prompt", [], videos, audios)["messages"][0]["content"]
        self.assertEqual(sum(item["type"] == "video_url" for item in content), 4)
        self.assertEqual(sum(item["type"] == "input_audio" for item in content), 8)

    def test_payload_builders_do_not_mutate_media(self):
        original = copy.deepcopy((self.images, self.videos, self.audios))
        nodes._openai_chat_payload("model", "role", "prompt", self.images, self.videos, self.audios)
        nodes._openai_responses_payload("model", "role", "prompt", self.images, self.videos, self.audios)
        nodes._gemini_payload("role", "prompt", self.images, self.videos, self.audios)
        self.assertEqual((self.images, self.videos, self.audios), original)

    def test_invalid_media_collections_fail_instead_of_dropping_items(self):
        for media in ("not-a-media", [None], [self.videos[0], "invalid"]):
            with self.subTest(media=media), self.assertRaises(ValueError):
                nodes._openai_chat_payload("model", "", "prompt", [], media, None)

    def test_anthropic_rejects_native_video_single_or_list(self):
        for media in (self.videos[0], self.videos):
            with self.subTest(media=media), self.assertRaisesRegex(ValueError, "sample_frames"):
                nodes._anthropic_payload("model", "", "prompt", [], media, None)

    def test_anthropic_rejects_audio_single_or_list(self):
        for media in (self.audios[0], self.audios):
            with self.subTest(media=media), self.assertRaisesRegex(ValueError, "音频"):
                nodes._anthropic_payload("model", "", "prompt", [], None, media)

    def test_anthropic_accepts_sampled_video_frames_as_images(self):
        frames = [dict(self.images[0], label=f"<Video 2, chronological frame {index}/2>") for index in (1, 2)]
        content = nodes._anthropic_payload("model", "role", "prompt", frames, [], [])["messages"][0]["content"]
        self.assertEqual([part["text"] for part in content if part["type"] == "text"], ["prompt"] + [frame["label"] for frame in frames])
        self.assertEqual(sum(part["type"] == "image" for part in content), 2)

    def test_request_dispatch_does_not_hide_anthropic_media(self):
        config = {
            "api_protocol": nodes.ANTHROPIC_MESSAGES,
            "api_key_file": "unused.txt",
            "api_base_url": "https://example.test/v1",
            "anthropic_version": "2023-06-01",
            "parameter_mode": "auto",
        }
        # Avoid reading credentials. Request construction must fail before any send.
        with patch.object(nodes, "_read_api_key", return_value="test-only"):
            for v, a in ((self.videos, None), (None, self.audios)):
                with self.subTest(video=v is not None), self.assertRaises(ValueError):
                    nodes._build_request(config, "model", "", "prompt", [], v, a, 0.6, 4096, 1.0, 0.0, 0.0, "none", -1)


if __name__ == "__main__":
    unittest.main()
