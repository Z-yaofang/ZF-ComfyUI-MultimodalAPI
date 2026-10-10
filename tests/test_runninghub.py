"""RunningHub account isolation, media boundaries and credential safety.

All server credentials, environment variables and HTTP calls are synthetic.
"""

import importlib.util
import json
from pathlib import Path
import sys
import traceback
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zf_runninghub_test_nodes", ROOT / "nodes.py")
nodes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nodes)
try:
    import torch
except ImportError:
    torch = None

CLOUD_KEY = "synthetic-cloud-key-for-offline-tests"
ENV_KEY = "synthetic-environment-key-for-offline-tests"


def chat_args(config=None, **changes):
    args = {
        "model": "synthetic-model", "role": "Follow the user's request.", "prompt": "Describe the material.",
        "temperature": .1, "max_tokens": 512, "top_p": 1,
        "presence_penalty": 0, "frequency_penalty": 0, "reasoning_effort": "none", "seed": 12345,
        "api_config": config if config is not None else nodes.ZFRunningHubAPIConfig().build()[0],
    }
    args.update(changes)
    return args


def encoded_video(index):
    return {"label": f"<Video {index}>", "mime_type": "video/mp4", "data": "AA=="}


class RunningHubTests(unittest.TestCase):
    def setUp(self):
        self.server = types.ModuleType("server")
        self.server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(shared_api_key=CLOUD_KEY))
        self.start(patch.dict(sys.modules, {"server": self.server}))
        # Never observe the actual process's credentials, even when testing absence.
        self.start(patch.dict(nodes.os.environ, {"RH_API_KEY": ENV_KEY}, clear=True))
        self.start(patch.object(nodes, "_read_api_key", side_effect=AssertionError("real key files must not be read")))
        self.start(patch.object(nodes.time, "sleep"))
        self.response = Mock()
        self.response.status_code = 200
        self.response.ok = True
        self.response.text = '{"choices":[{"message":{"content":"Synthetic answer"}}]}'
        self.response.json.return_value = {"choices": [{"message": {"content": "Synthetic answer"}}]}
        # Patch the actual transport, rather than bypassing the security wrapper.
        self.post = self.start(patch.object(nodes.requests, "post", return_value=self.response))

    def start(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def chat(self, config=None, **changes):
        return nodes.ZFMultimodalLLM().chat(**chat_args(config, **changes))

    def request(self, config, images=None, videos=None, audios=None):
        return nodes._build_request(config, "synthetic-model", "role", "prompt", images or [],
                                    videos, audios, .1, 512, 1, 0, 0, "none", 12345)

    def assert_safe_exception(self, exc):
        self.assertNotIn(CLOUD_KEY, str(exc))
        self.assertNotIn(ENV_KEY, str(exc))
        self.assertIsNone(exc.__cause__)
        self.assertTrue(exc.__suppress_context__)
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.assertNotIn(CLOUD_KEY, rendered)
        self.assertNotIn(ENV_KEY, rendered)
        self.assertNotIn("During handling of the above exception", rendered)

    def test_configuration_exposes_no_key_widget_and_resolves_no_secret(self):
        with patch.object(nodes, "_runninghub_key", side_effect=AssertionError("configuration must not resolve credentials")):
            config = nodes.ZFRunningHubAPIConfig().build()[0]
        names = set(nodes.ZFRunningHubAPIConfig.INPUT_TYPES()["required"])
        self.assertFalse(names.intersection({"api_key", "api_key_file", "api_base_url", "api_protocol"}))
        self.assertEqual(nodes.ZFRunningHubAPIConfig.RETURN_TYPES, nodes.ZFMultimodalAPIConfig.RETURN_TYPES)
        saved = json.dumps(config)
        self.assertNotIn(CLOUD_KEY, saved)
        self.assertNotIn(ENV_KEY, saved)
        self.assertEqual(config["video_mode"], "sample_frames")
        self.assertEqual(config["audio_mode"], "ignore")

    def test_cloud_mode_uses_only_current_shared_account(self):
        self.chat()
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], f"Bearer {CLOUD_KEY}")
        self.assertNotIn(ENV_KEY, json.dumps(self.post.call_args.kwargs))

    def test_env_mode_uses_only_explicit_environment_account(self):
        config = nodes.ZFRunningHubAPIConfig().build(auth_source="rh_env")[0]
        self.chat(config)
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], f"Bearer {ENV_KEY}")
        self.assertNotIn(CLOUD_KEY, json.dumps(self.post.call_args.kwargs))

    def test_missing_cloud_key_does_not_fall_back_to_valid_environment(self):
        for value in (None, "", "  ", "unknown", " UNKNOWN ", 123):
            with self.subTest(value=value):
                self.server.PromptServer.instance.shared_api_key = value
                with self.assertRaisesRegex(RuntimeError, "云端系统凭据"):
                    self.chat()
        self.post.assert_not_called()

    def test_missing_environment_key_does_not_fall_back_to_valid_cloud(self):
        config = nodes.ZFRunningHubAPIConfig().build(auth_source="rh_env")[0]
        for value in ("", " ", "unknown", " UNKNOWN "):
            with self.subTest(value=value), patch.dict(nodes.os.environ, {"RH_API_KEY": value}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "RH_API_KEY"):
                    self.chat(config)
        with patch.dict(nodes.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "RH_API_KEY"):
                self.chat(config)
        self.post.assert_not_called()

    def test_absent_comfy_server_reports_cloud_setup_error(self):
        with patch.dict(sys.modules, {"server": None}):
            with self.assertRaisesRegex(RuntimeError, "云端系统凭据"):
                self.chat()
        self.post.assert_not_called()

    def test_uninitialized_comfy_server_reports_cloud_setup_error(self):
        self.server.PromptServer = types.SimpleNamespace()
        with self.assertRaisesRegex(RuntimeError, "云端系统凭据"):
            self.chat()
        self.post.assert_not_called()

    def test_shared_credentials_are_resolved_for_each_execution(self):
        config = nodes.ZFRunningHubAPIConfig().build()[0]
        self.chat(config)
        second = "synthetic-second-cloud-account"
        self.server.PromptServer.instance.shared_api_key = second
        self.chat(config)
        sent = [call.kwargs["headers"]["Authorization"] for call in self.post.call_args_list]
        self.assertEqual(sent, [f"Bearer {CLOUD_KEY}", f"Bearer {second}"])
        self.assertNotIn(second, json.dumps(config))

    def test_environment_credentials_are_resolved_for_each_execution(self):
        config = nodes.ZFRunningHubAPIConfig().build(auth_source="rh_env")[0]
        self.chat(config)
        with patch.dict(nodes.os.environ, {"RH_API_KEY": "synthetic-new-env-account"}, clear=True):
            self.chat(config)
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer synthetic-new-env-account")

    def test_region_endpoints_ignore_url_protocol_and_key_file_overrides(self):
        for region, hostname in (("cn", "llm.runninghub.cn"), ("ai", "llm.runninghub.ai")):
            with self.subTest(region=region):
                config = nodes.ZFRunningHubAPIConfig().build(endpoint_region=region)[0]
                config.update(api_base_url="https://attacker.invalid/capture", api_protocol=nodes.GEMINI_GENERATE_CONTENT,
                              api_key_file="do-not-read.txt", audio_mode="native", video_max_seconds=120, video_max_mb=200)
                self.chat(config)
                call = self.post.call_args
                self.assertEqual(call.args[0], f"https://{hostname}/v1/chat/completions")
                self.assertIn("messages", call.kwargs["json"])
                self.assertNotIn("contents", call.kwargs["json"])
        # The request builder also protects direct callers, not just the node path.
        url, headers, payload = self.request(config)
        self.assertEqual(url, "https://llm.runninghub.ai/v1/chat/completions")
        self.assertEqual(headers["Authorization"], f"Bearer {CLOUD_KEY}")

    def test_invalid_region_or_auth_selection_fails_without_sending(self):
        for change in ({"endpoint_region": "https://attacker.invalid"}, {"auth_source": "automatic"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.chat({"provider": "runninghub", **change})
        self.post.assert_not_called()

    def test_rh_omits_seed_while_third_party_behavior_is_preserved(self):
        self.chat()
        self.assertNotIn("seed", self.post.call_args.kwargs["json"])
        config = nodes._normalize_config({"api_base_url": "https://third-party.invalid/v1", "api_key_file": "mock.txt"})
        with patch.object(nodes, "_read_api_key", return_value="synthetic-third-party-key"), patch.object(
            nodes, "_runninghub_key", side_effect=AssertionError("third-party mode accessed an RH account")
        ):
            self.chat(config)
        self.assertEqual(self.post.call_args.args[0], "https://third-party.invalid/v1/chat/completions")
        self.assertEqual(self.post.call_args.kwargs["json"]["seed"], 12345)
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer synthetic-third-party-key")

    def test_native_video_with_pictures_is_rejected_before_transport(self):
        config = nodes.ZFRunningHubAPIConfig().build(video_mode="native")[0]
        with self.assertRaisesRegex(ValueError, "sample_frames"):
            self.request(config, [{"label": "<Picture 1>", "mime_type": "image/jpeg", "data": "AA=="}], [encoded_video(1)])
        self.post.assert_not_called()

    def test_multiple_native_videos_are_rejected_before_transport(self):
        config = nodes.ZFRunningHubAPIConfig().build(video_mode="native")[0]
        with self.assertRaisesRegex(ValueError, "sample_frames"):
            self.request(config, videos=[encoded_video(1), encoded_video(2)])
        self.post.assert_not_called()

    def test_single_native_video_keeps_original_label(self):
        config = nodes.ZFRunningHubAPIConfig().build(video_mode="native")[0]
        _url, _headers, payload = self.request(config, videos=[encoded_video(1)])
        content = payload["messages"][1]["content"]
        self.assertEqual(sum(part["type"] == "video_url" for part in content), 1)
        self.assertTrue(any(part.get("text") == "<Video 1>" for part in content))

    def test_direct_audio_cannot_bypass_rh_media_boundary(self):
        config = nodes.ZFRunningHubAPIConfig().build()[0]
        audio = {"label": "<Audio 1>", "mime_type": "audio/wav", "format": "wav", "data": "AA=="}
        with self.assertRaisesRegex(ValueError, "独立音频"):
            self.request(config, audios=[audio])
        self.post.assert_not_called()

    def test_legacy_audio_input_is_not_encoded_or_sent_and_is_disclosed(self):
        with patch.object(nodes, "_audio_media", side_effect=AssertionError("RH ignored audio must not be encoded")):
            self.chat(audio=object())
        payload = self.post.call_args.kwargs["json"]
        saved = json.dumps(payload)
        self.assertNotIn("input_audio", saved)
        self.assertIn("NOT heard", saved)
        self.assertIn("independent AUDIO inputs are not sent", saved)

    @unittest.skipIf(torch is None, "Synthetic tensor media checks need the ComfyUI Python runtime")
    def test_default_bundle_sends_pictures_and_multiple_video_samples_but_no_audio(self):
        bundle = {
            "schema_version": "zf-media-bundle-v1",
            "images": [{"label": "<Picture 1>", "value": torch.zeros(1, 8, 8, 3)}],
            "videos": [{"label": f"<Video {i}>", "value": torch.zeros(24, 8, 8, 3), "format": "frames", "fps": 24} for i in (1, 2)],
            "audios": [{"label": "<Audio 1>", "value": {"waveform": torch.zeros(1, 1, 32), "sample_rate": 16000}}],
            "manifest": {"source": "synthetic-rh-test"},
        }
        with patch.object(nodes, "_audio_media", side_effect=AssertionError("audio must not be encoded")), patch.object(
            nodes, "_native_video_media", side_effect=AssertionError("default video must be sampled")
        ):
            self.chat(media_bundle=bundle)
        self.assertEqual(self.post.call_count, 1)
        payload = self.post.call_args.kwargs["json"]
        content = payload["messages"][1]["content"]
        self.assertEqual(sum(part["type"] == "image_url" for part in content), 17)
        self.assertFalse(any(part["type"] in ("video_url", "input_audio") for part in content))
        labels = [part["text"] for part in content if part["type"] == "text"]
        self.assertIn("<Picture 1>", labels)
        self.assertTrue(any(label.startswith("<Video 2, chronological frame") for label in labels))
        self.assertIn("NOT heard", labels[0])

    def test_cloud_requests_do_not_follow_http_redirects(self):
        self.response.status_code = 302
        self.response.headers = {"Location": "https://attacker.invalid/capture"}
        with self.assertRaisesRegex(RuntimeError, "重定向"):
            self.chat()
        self.assertEqual(self.post.call_count, 1)
        self.assertIs(self.post.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(self.post.call_args.args[0], "https://llm.runninghub.cn/v1/chat/completions")

    def test_http_error_echo_is_redacted_in_message_and_visible_traceback(self):
        self.response.status_code, self.response.ok = 401, False
        self.response.text = json.dumps({"api_key": CLOUD_KEY, "reason": "synthetic rejection"})
        try:
            self.chat()
        except RuntimeError as exc:
            self.assertIn("401", str(exc))
            self.assert_safe_exception(exc)
        else:
            self.fail("A failed API response must raise")

    def test_network_exception_echo_is_redacted_without_original_exception_chain(self):
        self.post.side_effect = nodes.requests.RequestException(f"synthetic transport failure with {CLOUD_KEY}")
        try:
            self.chat()
        except RuntimeError as exc:
            self.assertIn("transport failure", str(exc))
            self.assert_safe_exception(exc)
        else:
            self.fail("A failed transport must raise")

    def test_invalid_json_echo_is_redacted_without_parser_exception_chain(self):
        self.response.text = f"not JSON; credential echo={CLOUD_KEY}"
        self.response.json.side_effect = ValueError(f"synthetic parser error {CLOUD_KEY}")
        try:
            self.chat()
        except RuntimeError as exc:
            self.assertIn("有效 JSON", str(exc))
            self.assert_safe_exception(exc)
        else:
            self.fail("Invalid response JSON must raise")

    def test_success_text_raw_response_and_ui_do_not_echo_credentials(self):
        for auth, key in (("rh_cloud", CLOUD_KEY), ("rh_env", ENV_KEY)):
            with self.subTest(auth=auth):
                self.response.json.return_value = {
                    "choices": [{"message": {"content": f"Synthetic answer with {key}"}}],
                    "debug": {"api_key": key, "Authorization": f"Bearer {key}"},
                }
                result = self.chat(nodes.ZFRunningHubAPIConfig().build(auth_source=auth)[0])
                rendered = json.dumps(result)
                self.assertNotIn(key, rendered)
                self.assertIn("Synthetic answer", result["result"][0])
                self.assertIn("***", rendered)

    def test_skip_error_result_ui_and_logs_do_not_echo_credentials(self):
        self.response.status_code, self.response.ok = 403, False
        self.response.text = f"synthetic rejected key={CLOUD_KEY}"
        with self.assertLogs(nodes.log, level="ERROR") as logs:
            result = self.chat(skip_error=True)
        self.assertIn("[ERROR]", result["result"][0])
        self.assertNotIn(CLOUD_KEY, json.dumps(result))
        self.assertNotIn(CLOUD_KEY, "\n".join(logs.output))

    def test_mock_transport_unexpected_error_is_also_redacted(self):
        self.post.side_effect = RuntimeError(f"synthetic adapter error with {CLOUD_KEY}")
        with self.assertLogs(nodes.log, level="ERROR") as logs:
            result = self.chat(skip_error=True)
        self.assertNotIn(CLOUD_KEY, json.dumps(result))
        self.assertNotIn(CLOUD_KEY, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
