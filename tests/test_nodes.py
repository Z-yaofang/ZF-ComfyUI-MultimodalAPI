import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "nodes.py"
SPEC = importlib.util.spec_from_file_location("zf_multimodal_nodes", MODULE_PATH)
nodes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nodes)


class EndpointTests(unittest.TestCase):
    def test_openai_complete_endpoint_is_not_duplicated(self):
        self.assertEqual(
            nodes._endpoint(
                nodes.OPENAI_CHAT,
                "https://example.test/v1/chat/completions",
                "model-a",
            ),
            "https://example.test/v1/chat/completions",
        )

    def test_gemini_model_placeholder(self):
        self.assertEqual(
            nodes._endpoint(
                nodes.GEMINI_GENERATE_CONTENT,
                "https://example.test/models/{model}:generateContent",
                "gemini-test",
            ),
            "https://example.test/models/gemini-test:generateContent",
        )


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.image = {
            "kind": "image",
            "label": "<Picture 1>",
            "mime_type": "image/jpeg",
            "data": "aW1hZ2U=",
        }
        self.video = {
            "kind": "video",
            "label": "<Video 1>",
            "mime_type": "video/mp4",
            "data": "dmlkZW8=",
        }
        self.audio = {
            "kind": "audio",
            "label": "<Audio 1>",
            "mime_type": "audio/mp3",
            "format": "mp3",
            "data": "YXVkaW8=",
        }

    def test_openai_chat_keeps_images_and_video(self):
        payload = nodes._openai_chat_payload(
            "model-a",
            "system",
            "prompt",
            [self.image],
            self.video,
        )
        content = payload["messages"][1]["content"]
        self.assertTrue(any(item.get("type") == "image_url" for item in content))
        self.assertTrue(any(item.get("type") == "video_url" for item in content))

    def test_anthropic_image_payload(self):
        payload = nodes._anthropic_payload("claude-test", "system", "prompt", [self.image])
        content = payload["messages"][0]["content"]
        self.assertTrue(any(item.get("type") == "image" for item in content))
        self.assertEqual(payload["system"], "system")

    def test_openai_chat_keeps_audio_input(self):
        payload = nodes._openai_chat_payload("gpt-audio", "system", "prompt", [], None, self.audio)
        content = payload["messages"][1]["content"]
        audio_part = next(item for item in content if item.get("type") == "input_audio")
        self.assertEqual(audio_part["input_audio"]["format"], "mp3")

    def test_responses_keeps_audio_input(self):
        payload = nodes._openai_responses_payload("gpt-audio", "system", "prompt", [], None, self.audio)
        content = payload["input"][0]["content"]
        self.assertTrue(any(item.get("type") == "input_audio" for item in content))

    def test_gemini_keeps_inline_audio(self):
        payload = nodes._gemini_payload("system", "prompt", [], None, self.audio)
        parts = payload["contents"][0]["parts"]
        audio_part = next(item for item in parts if item.get("inline_data", {}).get("mime_type") == "audio/mp3")
        self.assertEqual(audio_part["inline_data"]["data"], "YXVkaW8=")

    def test_auto_parameters_omit_default_penalties(self):
        payload = {"model": "model-a", "messages": []}
        nodes._apply_generation_parameters(
            payload,
            nodes.OPENAI_CHAT,
            "auto",
            0.6,
            4096,
            1.0,
            0.0,
            0.0,
            "none",
            -1,
        )
        self.assertNotIn("presence_penalty", payload)
        self.assertNotIn("frequency_penalty", payload)
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("seed", payload)


class ResponseTests(unittest.TestCase):
    def test_openai_list_content(self):
        data = {"choices": [{"message": {"content": [{"type": "text", "text": "ok"}]}}]}
        self.assertEqual(nodes._response_text(nodes.OPENAI_CHAT, data), "ok")

    def test_raw_response_redacts_media(self):
        raw = nodes._safe_raw_response({"echo": "data:image/png;base64,aW1hZ2U="})
        self.assertNotIn("aW1hZ2U=", raw)

    def test_safe_key_path_rejects_parent_path(self):
        with self.assertRaises(ValueError):
            nodes._safe_key_path("../secret.txt")


class ConfigTests(unittest.TestCase):
    def test_config_node_returns_normalized_config(self):
        config = nodes.ZFMultimodalAPIConfig().build(
            nodes.OPENAI_CHAT,
            "https://example.test/v1",
            "",
            300,
            "auto",
            "sample_frames",
            15,
            10,
            8,
            "2023-06-01",
        )[0]
        self.assertEqual(config["api_base_url"], "https://example.test/v1")
        self.assertEqual(config["video_mode"], "sample_frames")
        self.assertEqual(config["audio_max_seconds"], 180)
        self.assertEqual(config["audio_max_mb"], 18)


if __name__ == "__main__":
    unittest.main()
