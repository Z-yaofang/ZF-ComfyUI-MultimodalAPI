# ZF-ComfyUI-MultimodalAPI

An independent, lightweight ComfyUI node set for sending text, up to eight image inputs, and one video input to a user-configured multimodal API.

It does not call RunningHub and does not depend on `ComfyUI_RH_OpenAPI` or `comfyui-FOK_API_tools`. Its chat node intentionally resembles the `RHLLMChatNode` workflow interface to make migration straightforward, while the request implementation is original and provider-neutral.

Supported protocols:

- OpenAI Chat Completions
- OpenAI Responses
- Anthropic Messages
- Gemini GenerateContent

API keys are read from a file in this plugin directory and are never stored in workflow JSON. Leave `api_key_file` empty for a local API without authentication.

Video modes:

- `auto`: native video for OpenAI/Gemini, sampled frames for Anthropic.
- `native`: Base64 video; preserves embedded audio when the API accepts it.
- `sample_frames`: ffmpeg-extracted chronological frames; works with image-only vision APIs but contains no audio.
- `ignore`: do not send the connected video.

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

## Quick start

1. Restart ComfyUI and add both nodes from `ZF/API`.
2. Put the real key in this plugin's `api_key.txt`.
3. Select the provider-compatible protocol, enter your own API base URL, and use the exact model name accepted by the server.
4. Start with `video_mode=sample_frames`; use `native` only when the gateway explicitly supports Base64 video input.

To migrate an existing RH LLM workflow into a new file:

```text
python tools/migrate_rh_workflow.py "source.json" "destination.json"
```

The script replaces only the RH chat/config nodes and their config link. It never overwrites the source workflow.

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
