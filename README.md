# ZF-ComfyUI-MultimodalAPI

An independent, lightweight ComfyUI node set for sending text, images, videos, and audio to a user-configured multimodal API. Existing workflows keep their eight image inputs, one video input, and one audio input; optional media bundles support up to 11 images, three videos, and seven audio tracks.

Choose either the existing user-configured API connection or the new, explicit RunningHub connection. Only connecting `ZFRunningHubAPIConfig` enables the RH provider; existing configurations continue to use their own protocol, endpoint, and key file. The plugin does not depend on `ComfyUI_RH_OpenAPI` or `comfyui-FOK_API_tools`. Its request implementation is independent.

This repository intentionally stays API-only. The optional local llama.cpp multimodal writer used by the prompt-director workflow is maintained in [ZF-ComfyUI-PromptDirector](https://github.com/Z-yaofang/ZF-ComfyUI-PromptDirector), avoiding two competing copies of the same local adapter.

Supported protocols for the existing third-party configuration:

- OpenAI Chat Completions
- OpenAI Responses
- Anthropic Messages
- Gemini GenerateContent

For the existing third-party configuration, API keys are read from a file in this plugin directory and are never stored in workflow JSON. Leave `api_key_file` empty for a local API without authentication. The separate RH configuration uses runtime credentials described below and does not read that file.

Video modes for the existing third-party configuration:

- `auto`: native video for OpenAI/Gemini, sampled frames for Anthropic.
- `native`: Base64 video; preserves embedded audio when the API accepts it.
- `sample_frames`: chronological images. An `IMAGE` frame batch is sampled directly, without `CreateVideo` or ffmpeg; `VIDEO` inputs use the existing video sampling path. Sampling itself provides no audio.
- `ignore`: omit connected videos and tell the model in the media description.

With the third-party configuration, the optional standard ComfyUI `AUDIO` input is clipped and compressed locally with ffmpeg before inline Base64 upload. Gemini GenerateContent, OpenAI Chat Completions, and OpenAI Responses use their native audio content fields. Anthropic Messages does not accept native audio here. The selected remote model must support audio understanding. `audio_mode=native` is the default; explicit `audio_mode=ignore` omits separate audio inputs and informs the model that the audio was not provided. Embedded audio in native video is governed by the video mode. The RH configuration always ignores separate audio, as described below.

The per-audio defaults are `audio_max_seconds=180` and `audio_max_mb=18`. `request_max_mb=64` checks the complete request size, including Base64, before sending. Set limits to match your provider: encoding increases size and each provider may impose stricter limits. Oversized requests fail instead of silently dropping media.

Native video fields vary across compatible gateways. If a service rejects `video_url` or `input_video`, use `sample_frames`.

See [README_ZH.md](README_ZH.md) for full documentation.

This project is not affiliated with RunningHub or FOK API Tools.

## Installation

From `ComfyUI/custom_nodes`:

```text
git clone https://github.com/Z-yaofang/ZF-ComfyUI-MultimodalAPI.git
cd ZF-ComfyUI-MultimodalAPI
python -m pip install -r requirements.txt
```

Use the Python interpreter that starts ComfyUI when installing dependencies.

## Third-party API quick start

1. Restart ComfyUI and add `ZFMultimodalAPIConfig` and `ZFMultimodalLLM` from `ZF/API`.
2. Put the real key in this plugin's `api_key.txt`.
3. Select the provider-compatible protocol, enter your own API base URL, and use the exact model name accepted by the server.
4. Start with `video_mode=sample_frames`; use `native` only when the gateway explicitly supports Base64 video input.

## RunningHub connection

Add `ZFRunningHubAPIConfig` (display name: **ZF RH 云端 API 配置**) and connect its `api_config` output to the existing `ZFMultimodalLLM`. It returns the same `ZF_MULTIMODAL_API_CONFIG` type, so media connections and prompts stay the same. No PromptDirector source changes or media-desk changes are needed. The old configuration node's schema remains unchanged.

| Setting | Behavior |
| --- | --- |
| `auth_source=rh_cloud` | Default. Read only `PromptServer.instance.shared_api_key` at execution time. Missing cloud credentials cause a clear error; the plugin does not silently switch accounts or fall back to a local key. |
| `auth_source=rh_env` | Explicit local choice. Read only the `RH_API_KEY` environment variable. Restart ComfyUI after changing the environment. |
| `endpoint_region=cn` | Default. Use the fixed `https://llm.runninghub.cn/v1/chat/completions` endpoint. |
| `endpoint_region=ai` | Use the fixed `https://llm.runninghub.ai/v1/chat/completions` endpoint. |
| `timeout` | Default 300 seconds. |
| `video_mode` | `sample_frames` (default), `native`, or `ignore`. |
| `video_sample_frames` | Default eight frames per video. |
| `request_max_mb` | Default 64 MB for the complete JSON request, including Base64. This is a local guard, not a claim about RH's server limit. |

RH mode uses its fixed official endpoint and Chat Completions protocol; third-party URL, protocol, and TXT key overrides do not apply. Keys are resolved when a request is built, not stored in the configuration output or workflow. RH requests omit `seed`. Enter a model name that is actually available through the selected RH endpoint and account; a model alias from another provider is not necessarily valid there.

RH media behavior:

- `sample_frames` sends pictures and chronological frames from multiple videos together. The existing labels and order are preserved. Sampling does not provide sound.
- `native` accepts one video with no pictures. To combine pictures and video, or use several videos, select `sample_frames`.
- Video preparation uses a 15-second limit and a 10 MB native-video limit. Server and model restrictions may be stricter.
- Separate audio inputs are always explicitly ignored in RH mode. The media note says they were not heard; do not expect dialogue, voice, or music recognition from those tracks. Sound embedded in native video depends on the chosen model and is not guaranteed.
- Bundle capacity does not establish the provider's permitted image count or request size. For example, 11 pictures plus three videos sampled at eight frames each produce 35 image parts. The plugin does not silently discard excess media.

For RH cloud, ask RunningHub support to deploy this plugin version in the workflow's execution environment and verify that the shared credential is supplied for the intended account and billing arrangement. Local installation alone does not update RH's cloud environment. Authentication, media limits, and account settlement require cloud verification; the automated tests use mocks and do not perform paid API calls.

Only the chosen RH official domain receives RH requests. This integration does not stage media on another upload platform, does not include credentials in distribution packages, and does not install or modify PromptDirector. To keep using your existing third-party account, keep `ZFMultimodalAPIConfig` connected instead.

## Simpler media connections

`ZFMultimodalLLM` keeps its existing inputs, outputs, and widget order. New optional inputs are appended:

- `video_frames`: connect an `IMAGE` video frame batch directly; set `video_fps` to its frame rate (default 24).
- `media_bundle`: connect either of the bundle nodes below.

Disconnected, `None`, and empty media are skipped automatically. Do not connect both `video` and `video_frames` for the same clip. A bundle cannot be combined with actual nonempty legacy media inputs; conflicting inputs produce an error instead of duplicates or ambiguous numbering.

### Generic pack: `ZFMultimodalMediaPack`

Connect up to 11 images, three videos, and seven audio tracks, then connect its bundle output to `ZFMultimodalLLM.media_bundle`. Each video slot accepts either a `VIDEO` or an `IMAGE` frame batch, with its corresponding frame rate. Empty slots are skipped. Packing is local and does not call an API.

The original API nodes and this generic pack do not require PromptDirector. Native encoding of frame batches uses PyAV (`av`) in the ComfyUI Python environment. Direct frame sampling does not need an encoder; audio preparation still uses ffmpeg.

### H3 bridge: `ZFH3MediaBundle`

For the H3 media desk in [ZF-ComfyUI-PromptDirector](https://github.com/Z-yaofang/ZF-ComfyUI-PromptDirector):

```text
ZVH3ReferenceOutlet.reference_plan_json
    → ZFH3MediaBundle.reference_plan_json
    → ZFMultimodalLLM.media_bundle
```

Keep the original media desk and connections to H3 generation. The bridge reuses the media outlet's cached, aligned material without decoding it again. It replaces external empty checks, video conversion, and first-valid-audio switches.

The bundle includes the first/last frames, up to nine reference images, three reference videos, and associated audio. It retains H3's `<Picture n>`, `<Video n>`, and `<Audio n>` labels. Audio order is video soundtracks, drive audio, then reference audio; the `final_audio` alias is not added twice. Only this bridge depends on PromptDirector's H3 outlet.

With a third-party model that supports audio, use `video_mode=sample_frames` and `audio_mode=native` as a starting point for H3. Videos become chronological images; separate audio tracks carry their sound. If the selected model cannot accept audio, explicitly choose `audio_mode=ignore`. The RH configuration always ignores separate audio. In that mode, do not expect the reverse prompt to recover dialogue or voices from those tracks.

Bundle capacity is not a guarantee of provider capacity. Gateways and models may restrict image count, multiple videos or audio tracks, duration, and total request size. Unsupported requests fail visibly rather than silently losing material. Sampling a plain `VIDEO` does not extract its soundtrack: connect a separate `AUDIO`, use an H3 bundle with video audio, or use supported native video.

## RH workflow migration

To migrate an existing RH LLM workflow into a new file:

```text
python tools/migrate_rh_workflow.py "source.json" "destination.json"
```

The script replaces only the RH chat/config nodes and their config link. It never overwrites the source workflow. It targets the existing user-configured API mode; migration alone does not enable cloud credentials. To use RH account credentials, explicitly connect `ZFRunningHubAPIConfig` afterward.

## Security before publishing changes

Credential files are excluded by `.gitignore`, including `api_key*.txt`, `*-API.txt`, `.env*`, private keys, and certificate bundles. If you use a custom credential filename, verify it with `git status` before committing.

## License and compatibility

The original implementation in this repository is released under the MIT License. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the RH compatibility background and the FOK interoperability note.

## Tests

Run either command from the plugin directory:

```text
pytest --import-mode=importlib tests
```

```text
python -m unittest discover -s tests -v
```
