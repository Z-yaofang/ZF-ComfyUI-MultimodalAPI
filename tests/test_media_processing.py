import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("zf_processing_test_nodes", ROOT / "nodes.py")
nodes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nodes)
processor = nodes._media_processor()
try:
    import torch
except ImportError:
    torch = None


def bundle(images=(), videos=(), audios=()):
    return {"schema_version": "zf-media-bundle-v1",
            "images": [{"label": f"<Picture {i}>", "value": value} for i, value in enumerate(images, 1)],
            "videos": [{"label": f"<Video {i}>", "value": value, "format": "frames", "fps": 24} for i, value in enumerate(videos, 1)],
            "audios": [{"label": f"<Audio {i}>", "value": value} for i, value in enumerate(audios, 1)],
            "manifest": {"source": "synthetic-test"}}


def chat_args(**kwargs):
    return {"model": "synthetic", "role": "rewrite", "prompt": "request", "temperature": .1,
            "max_tokens": 512, "top_p": 1, "presence_penalty": 0, "frequency_penalty": 0,
            "reasoning_effort": "none", "seed": 1,
            "api_config": {"api_key_file": "", "video_mode": "sample_frames"}, **kwargs}


class CompatibilityTests(unittest.TestCase):
    def test_config_append_only(self):
        cls = nodes.ZFMultimodalAPIConfig
        schema = cls.INPUT_TYPES()
        self.assertEqual(list(schema["required"]), ["api_protocol", "api_base_url", "api_key_file", "timeout", "parameter_mode", "video_mode", "video_max_seconds", "video_max_mb", "video_sample_frames", "anthropic_version", "audio_max_seconds", "audio_max_mb"])
        self.assertEqual(list(schema["optional"]), ["audio_mode", "request_max_mb"])
        config = cls().build(nodes.OPENAI_CHAT, "https://example.invalid/v1", "", 300, "auto", "auto", 15, 10, 8, "2023-06-01")[0]
        self.assertEqual(config["audio_mode"], "native")
        self.assertEqual(config["audio_max_seconds"], 180)

    def test_chat_append_only(self):
        optional = list(nodes.ZFMultimodalLLM.INPUT_TYPES()["optional"])
        self.assertEqual(optional[:12], ["skip_error", *[f"image{i}" for i in range(1, 9)], "video", "api_config", "audio"])
        self.assertEqual(optional[12:], ["media_bundle", "video_frames", "video_fps"])
        self.assertEqual(nodes.ZFMultimodalLLM.RETURN_TYPES, ("STRING", "STRING"))

    def test_bundle_rejects_mixed_inputs_before_encoding(self):
        with patch.object(nodes, "_post_json", side_effect=AssertionError("network forbidden")):
            with self.assertRaisesRegex(ValueError, "不能同时使用"):
                nodes.ZFMultimodalLLM().chat(**chat_args(media_bundle=bundle(), video="synthetic.mp4"))

    def test_request_size_guard_no_network(self):
        with patch.object(nodes, "_read_api_key", return_value="offline"), patch.object(nodes, "_post_json") as post:
            with self.assertRaisesRegex(ValueError, "request_max_mb"):
                nodes.ZFMultimodalLLM().chat(**chat_args(prompt="x" * (1024 * 1024), api_config={"api_key_file": "", "request_max_mb": 1}))
            post.assert_not_called()

    def test_empty_bundle_one_request(self):
        response = {"choices": [{"message": {"content": "one answer"}}]}
        with patch.object(nodes, "_read_api_key", return_value="offline"), patch.object(nodes, "_post_json", return_value=response) as post:
            result = nodes.ZFMultimodalLLM().chat(**chat_args(media_bundle=bundle()))
            self.assertEqual(result["result"][0], "one answer")
            self.assertEqual(post.call_count, 1)

    def test_anthropic_audio_rejected_explicitly(self):
        config = nodes._normalize_config({"api_protocol": nodes.ANTHROPIC_MESSAGES})
        with self.assertRaisesRegex(ValueError, "audio_mode=ignore"):
            processor.prepare_bundle(bundle(audios=[object()]), config, vars(nodes))

    def test_ignored_audio_not_encoded_and_not_claimed_heard(self):
        config = nodes._normalize_config({"api_protocol": nodes.ANTHROPIC_MESSAGES, "audio_mode": "ignore"})
        with patch.object(nodes, "_audio_media", side_effect=AssertionError("ignored audio encoded")):
            _images, _videos, audios, note = processor.prepare_bundle(bundle(audios=[object(), object()]), config, vars(nodes))
        self.assertEqual(audios, [])
        self.assertIn("<Audio 2>", note)
        self.assertIn("NOT heard", note)


@unittest.skipIf(torch is None, "Tensor integration checks require the ComfyUI Python runtime")
class TensorIntegrationTests(unittest.TestCase):
    def test_empty_vs_black_and_silent(self):
        self.assertTrue(processor.is_absent(torch.zeros(0, 8, 8, 3)))
        self.assertFalse(processor.is_absent(torch.zeros(1, 8, 8, 3)))
        self.assertTrue(processor.is_absent({"waveform": torch.zeros(1, 1, 0)}))
        self.assertFalse(processor.is_absent({"waveform": torch.zeros(1, 1, 16)}))

    def test_full_capacity_labels_and_single_request(self):
        media = bundle(images=[torch.zeros(1, 8, 8, 3) for _ in range(11)],
                       videos=[torch.zeros(48, 8, 8, 3) for _ in range(3)],
                       audios=[{"waveform": torch.zeros(1, 1, 32), "sample_rate": 16000} for _ in range(7)])
        fake_audio = {"kind": "audio", "label": "<Audio 1>", "data": "AA==", "mime_type": "audio/mp3", "format": "mp3"}
        response = {"choices": [{"message": {"content": "one answer"}}]}
        with patch.object(nodes, "_read_api_key", return_value="offline"), patch.object(nodes, "_audio_media", side_effect=lambda *a: dict(fake_audio)), patch.object(nodes, "_post_json", return_value=response) as post:
            result = nodes.ZFMultimodalLLM().chat(**chat_args(media_bundle=media))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result["result"][0], "one answer")
        payload = post.call_args.args[2]
        parts = payload["messages"][1]["content"]
        labels = [p.get("text", "") for p in parts if p["type"] == "text"]
        self.assertIn("<Picture 11>", labels)
        self.assertIn("<Audio 7>", labels)
        self.assertTrue(any("<Video 3, chronological frame" in x for x in labels))
        self.assertEqual(sum(p["type"] == "input_audio" for p in parts), 7)
        self.assertEqual(sum(p["type"] == "image_url" for p in parts), 11 + 3 * 8)

    def test_sample_indices_cover_clip_start_end_and_respect_time(self):
        frames = torch.zeros(100, 8, 8, 3)
        samples = processor.sample_frames(frames, 25, 2, 4, "<Video 2>", vars(nodes))
        self.assertEqual(len(samples), 4)
        self.assertIn("at 0.000 seconds", samples[0]["label"])
        self.assertIn("at 1.960 seconds", samples[-1]["label"])
        self.assertTrue(all("<Video 2," in x["label"] for x in samples))

    def test_frames_direct_and_empty_paths(self):
        response = {"choices": [{"message": {"content": "answer"}}]}
        for frames in (None, torch.zeros(0, 8, 8, 3), torch.zeros(24, 8, 8, 3)):
            with patch.object(nodes, "_read_api_key", return_value="offline"), patch.object(nodes, "_post_json", return_value=response) as post:
                nodes.ZFMultimodalLLM().chat(**chat_args(video_frames=frames, video_fps=24))
                self.assertEqual(post.call_count, 1)

    def test_frames_and_video_conflict(self):
        with self.assertRaisesRegex(ValueError, "请选择 VIDEO 或帧批"):
            nodes.ZFMultimodalLLM().chat(**chat_args(video_frames=torch.zeros(1, 8, 8, 3), video="x.mp4"))

    def test_native_frame_encoding_and_cleanup(self):
        try:
            import av
        except ImportError:
            self.skipTest("native tensor video needs PyAV")
        with processor.encoded_frames(torch.zeros(24, 17, 19, 3), 24, 15, vars(nodes)) as path:
            self.assertTrue(path.exists())
            with av.open(str(path)) as video:
                self.assertEqual(video.streams.video[0].average_rate, 24)
                self.assertEqual(len(list(video.decode(video=0))), 24)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
