# Third-Party and Compatibility Notices

## ComfyUI_RH_OpenAPI

`ZF-ComfyUI-MultimodalAPI` provides a separately named compatibility surface for migrating workflows that use `RHLLMChatNode` and `RHSettingsNode`.

The public node and workflow schemas of `HM-RunningHub/ComfyUI_RH_OpenAPI` informed this compatibility layer and the optional migration utility:

- Project: https://github.com/HM-RunningHub/ComfyUI_RH_OpenAPI
- License: Apache License 2.0
- Copyright notice in the upstream license: Copyright 2025 RunningHub

This plugin does not call or proxy the RunningHub service, does not depend on the RH plugin, and does not bundle RunningHub's image, video, or audio generation nodes. Its provider-neutral HTTP request implementation is separately structured.

## comfyui-FOK_API_tools

`facok/comfyui-FOK_API_tools` was evaluated as an interoperability reference while designing support for user-configured API base URLs:

- Project: https://github.com/facok/comfyui-FOK_API_tools

At the time of this repository's publication, that project did not include a software license. No project-specific FOK source implementation is incorporated here. The OpenAI Chat Completions, OpenAI Responses, Anthropic Messages, and Gemini GenerateContent request formats in this plugin are independently implemented from their public protocol conventions.

This project is not affiliated with, endorsed by, or sponsored by RunningHub or FOK API Tools.

## ComfyUI-llama-cpp_vlm

The optional `ZFLocalMultimodalLLM` node delegates local inference to the
separately installed `llama_cpp_instruct_adv` ComfyUI node:

- Project: https://github.com/lihaoyun6/ComfyUI-llama-cpp_vlm

No source code, model weights, or Python packages from that project are
bundled in this repository. Users install and update the backend separately.
