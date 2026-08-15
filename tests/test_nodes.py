import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock


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


class LocalNodeTests(unittest.TestCase):
    def test_local_node_uses_expected_comfy_types(self):
        inputs = nodes.ZFLocalMultimodalLLM.INPUT_TYPES()
        self.assertEqual(inputs["required"]["llama_model"][0], "LLAMACPPMODEL")
        self.assertEqual(inputs["optional"]["parameters"][0], "LLAMACPPARAMS")
        self.assertEqual(inputs["optional"]["image8"][0], "IMAGE")
        self.assertEqual(inputs["optional"]["video_frames"][0], "IMAGE")
        self.assertEqual(inputs["optional"]["video_max_frames"][0], "INT")
        self.assertFalse(hasattr(nodes.ZFLocalMultimodalLLM, "API_NODE"))

    def test_local_parameters_keep_advanced_values_but_visible_fields_win(self):
        params = nodes._local_generation_parameters(
            {"top_k": 7, "temperature": 1.5, "max_tokens": 20},
            temperature=0.15,
            max_tokens=4096,
            top_p=0.9,
            frequency_penalty=0.1,
            presence_penalty=0.2,
        )
        self.assertEqual(params["top_k"], 7)
        self.assertEqual(params["temperature"], 0.15)
        self.assertEqual(params["max_tokens"], 4096)
        self.assertEqual(params["present_penalty"], 0.2)

    def test_local_node_delegates_without_network(self):
        captured = {}

        class FakeBackend:
            def process(self, **kwargs):
                captured.update(kwargs)
                return ("<think>hidden</think>result", ["result"], 17)

        local_node = nodes.ZFLocalMultimodalLLM()
        with mock.patch.object(nodes, "_resolve_llama_cpp_instruct", return_value=FakeBackend()):
            result = local_node.chat(
                llama_model={"model": "test.gguf"},
                role="system",
                prompt="prompt",
                temperature=0.15,
                max_tokens=4096,
                top_p=0.9,
                presence_penalty=0.0,
                frequency_penalty=0.0,
                max_image_size=512,
                seed=42,
                force_offload=True,
                unique_id="123",
            )

        self.assertEqual(result["result"][0], "result")
        self.assertEqual(captured["inference_mode"], "images")
        self.assertEqual(captured["system_prompt"], "system")
        self.assertEqual(captured["custom_prompt"], "prompt")
        self.assertTrue(captured["force_offload"])
        self.assertIsNone(captured["images"])


if __name__ == "__main__":
    unittest.main()
