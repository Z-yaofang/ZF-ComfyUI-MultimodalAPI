# Third-Party and Compatibility Notices

## ComfyUI_RH_OpenAPI

`ZF-ComfyUI-MultimodalAPI` provides a separately named compatibility surface for migrating workflows that use `RHLLMChatNode` and `RHSettingsNode`.

The public node and workflow schemas of `HM-RunningHub/ComfyUI_RH_OpenAPI` informed this compatibility layer and the optional migration utility. Its published API conventions also informed the explicit `ZFRunningHubAPIConfig` integration:

- Project: https://github.com/HM-RunningHub/ComfyUI_RH_OpenAPI
- License: Apache License 2.0
- Copyright notice in the upstream license: Copyright 2025 RunningHub
- Cloud credential reference: https://github.com/HM-RunningHub/ComfyUI_RH_OpenAPI/blob/main/core/api_key.py (`PromptServer.instance.shared_api_key`)

The optional RH configuration calls the selected fixed official LLM endpoint at `https://llm.runninghub.cn/v1/chat/completions` or `https://llm.runninghub.ai/v1/chat/completions`. It explicitly selects either cloud shared credentials or the local `RH_API_KEY` environment variable; it does not copy upstream's automatic fallback sequence. Existing third-party configurations retain their own endpoints and key files and do not enable RH credentials.

This integration is independently implemented from public interface conventions. No complete upstream source files or RH image, video, or audio generation nodes are copied or bundled. The plugin does not depend on the RH plugin, does not stage media on an additional upload platform, and does not include credentials in distribution packages. PromptDirector and the H3 media desk do not need source changes.

Tests use mocked API requests. Cloud deployment, model-specific media limits, shared-credential availability, and account settlement must be verified with RunningHub; this compatibility statement is not a guarantee of cloud acceptance or billing behavior.

## comfyui-FOK_API_tools

`facok/comfyui-FOK_API_tools` was evaluated as an interoperability reference while designing support for user-configured API base URLs:

- Project: https://github.com/facok/comfyui-FOK_API_tools

At the time of this repository's publication, that project did not include a software license. No project-specific FOK source implementation is incorporated here. The OpenAI Chat Completions, OpenAI Responses, Anthropic Messages, and Gemini GenerateContent request formats in this plugin are independently implemented from their public protocol conventions.

This project is not affiliated with, endorsed by, or sponsored by RunningHub or FOK API Tools.
