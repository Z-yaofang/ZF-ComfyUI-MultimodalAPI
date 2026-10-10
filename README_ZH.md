# ZF-ComfyUI-MultimodalAPI

一个独立、轻量的 ComfyUI 多模态文本 API 插件，用于把现有的图片、视频和音频反推工作流接到你自己的 API。

支持原有的自定义 API 接入，以及新增的显式 RH 接入。只有连接 `ZF RH 云端 API 配置` 才使用 RH；原配置节点继续使用你自己的协议、地址和 Key 文件。插件不依赖 `ComfyUI_RH_OpenAPI` 或 `comfyui-FOK_API_tools`，请求逻辑为独立实现。

本仓库保持为纯 API 插件，不再重复维护本地 llama.cpp 多模态写作节点。提示词导演台工作流需要的本地节点统一由 [ZF-ComfyUI-PromptDirector](https://github.com/Z-yaofang/ZF-ComfyUI-PromptDirector) 提供，避免两个插件出现功能相同但版本不同的节点。

## 节点

### ZF 多模态 API 配置

- `api_protocol`
  - `openai_chat_completions`
  - `openai_responses`
  - `anthropic_messages`
  - `gemini_generatecontent`
- `api_base_url`：API 根地址，也可以填写对应协议的完整端点。
- `api_key_file`：只允许填写本插件目录内的文件名。留空表示不发送 Key，适合本地无鉴权 API。
- `parameter_mode`
  - `auto`：默认，只发送必要参数和非默认高级参数。
  - `full`：尽可能发送节点上的全部参数。
  - `minimal`：尽可能少发采样参数，适合参数限制严格的兼容接口。
- `video_mode`
  - `auto`：OpenAI/Gemini 使用原生视频；Anthropic 自动改用抽帧。
  - `native`：发送 Base64 原生视频，可保留视频内音频，但服务端必须支持对应视频字段。
  - `sample_frames`：发送按时间顺序抽取的画面。`IMAGE` 帧批直接抽帧，无需 `CreateVideo` 或 ffmpeg；`VIDEO` 输入沿用视频抽帧路径。抽帧本身不包含音频。
  - `ignore`：忽略连接的视频，并在媒体说明中告知模型。
- `audio_max_seconds`：最多发送参考音频开头多少秒，默认 180 秒。
- `audio_max_mb`：每条音频压缩后的大小上限，默认 18MB；Base64 编码后请求会进一步增大。
- `audio_mode`
  - `native`：默认，发送音频；需要服务端及模型支持音频理解。
  - `ignore`：不发送独立音频，在媒体说明中明确标记未提供音频内容；不会让模型假装听过声音。原生视频内的声音由 `video_mode` 决定。
- `request_max_mb`：发送前检查完整请求大小（含 Base64），默认 64MB。超限会报错，不会静默丢弃素材；应按服务商限制调整该值、素材时长和抽帧数量。

### ZF RH 云端 API 配置：`ZFRunningHubAPIConfig`

将其 `api_config` 输出接到原来的 `ZFMultimodalLLM` 即可。输出类型仍是 `ZF_MULTIMODAL_API_CONFIG`，素材和提示词沿用现有接线。无需改造 PromptDirector 或素材台；原有 `ZF 多模态 API 配置` 的节点参数顺序保持不变。

| 参数 | 用法 |
| --- | --- |
| `auth_source=rh_cloud` | 默认，仅在执行时读取 `PromptServer.instance.shared_api_key`。云端未提供有效凭据时明确报错，不会静默切换到其他账户或本地 Key。 |
| `auth_source=rh_env` | 显式选择本地环境变量 `RH_API_KEY`。修改环境变量后需重启 ComfyUI；该模式不改用云端共享凭据。 |
| `endpoint_region=cn` | 默认，固定使用 `https://llm.runninghub.cn/v1/chat/completions`。 |
| `endpoint_region=ai` | 固定使用 `https://llm.runninghub.ai/v1/chat/completions`。 |
| `timeout` | 默认 300 秒。 |
| `video_mode` | `sample_frames`（默认）、`native`、`ignore` 三选一。 |
| `video_sample_frames` | 默认每条视频抽 8 帧。 |
| `request_max_mb` | 默认 64MB，发送前检查含 Base64 的完整 JSON 请求；这是插件本地限制，不代表 RH 服务端保证接受的上限。 |

RH 模式固定使用官方域名及 Chat Completions 协议，不接受第三方 URL、协议或 TXT Key 覆盖。配置输出和工作流不保存真实密钥，构造请求时才读取运行环境凭据。RH 请求不发送 `seed`。反推节点的模型名应填写该 RH 区域和账户实际可用的名称，其他平台的模型别名不一定能直接沿用。

RH 素材处理规则：

- 默认 `sample_frames` 支持图片与多条视频的按时间顺序抽帧一起发送，保留素材编号；抽帧不包含声音。
- `native` 仅接受一条视频，且不能同时带图片。图像与视频混用，或有多条视频时，请改用 `sample_frames`。
- 视频处理时长限制为 15 秒，原生视频大小限制为 10MB；模型和服务端仍可能有更严格的要求。
- 独立音频固定为忽略，并在媒体说明中明确写出未听取。不能据此识别台词、声线或音乐；原生视频内嵌声音是否能被理解取决于所选模型，不作保证。
- 素材包容量不等于 RH 的媒体条数限制。例如 11 张图片加 3 条各抽 8 帧的视频，会形成 35 个图像内容块。插件不会悄悄删掉部分素材继续请求。

云端使用前，需要请 RH 客服在实际运行环境部署本插件新版，并核验共享凭据是否提供、对应账户及结算方式。本地更新插件不会自动更新 RH 云端。当前自动检查使用模拟请求，没有实际调用付费 API；云端鉴权、模型限制及账户扣费仍需核验。

RH 模式只向所选 RH 官方域名发送请求，不经其他素材上传平台中转。分发包不包含密钥，也不安装或修改 PromptDirector。继续使用现有第三方账户时，保留原来的 `ZF 多模态 API 配置` 即可。

### ZF 多模态反推（RH 接口兼容）

- 最多 8 路 `IMAGE`。
- 1 路 `VIDEO`。
- 1 路 `AUDIO`；标准 ComfyUI `LoadAudio` 可直接连接。
- 图片和视频可通过兼容的接口同时发送；RH 图像与视频混用时使用 `sample_frames`，不会静默忽略其中一种素材。
- 保留 `role`、`prompt`、`model`、采样参数、`skip_error`。
- 输出 `response` 和 `raw_response` 两路 `STRING`。
- 新增可选 `video_frames`（`IMAGE`）和 `video_fps`（默认 24），可直接接视频帧批；不需要在外面增加视频转换或空值判断节点。
- 新增可选 `media_bundle`，接下述素材包，可一次处理更多图片、视频和音频。

旧工作流的输入、输出和参数顺序保持兼容。`None` 与空素材会自动跳过。一次只能选择一种素材接法：`media_bundle` 不能与实际非空的传统图片、视频、音频输入混用；同一视频也不能同时接 `video` 和 `video_frames`。冲突会给出错误，避免重复或误编号。

### 通用素材包：`ZFMultimodalMediaPack`

将普通 ComfyUI 素材集中打包，再用一根 `media_bundle` 线连接反推节点：

- 最多 11 路图片、3 路视频、7 路音频。
- 每路视频支持 `VIDEO` 或 `IMAGE` 帧批，二者选一，并为帧批设置正确帧率。
- 未连接、`None` 或空素材自动跳过；输入本身有误时仍会报错。
- 该节点只整理素材，不调用 API。通用素材包及原有 API 节点不依赖 PromptDirector。

### H3 素材台桥：`ZFH3MediaBundle`

用于 [ZF-ComfyUI-PromptDirector](https://github.com/Z-yaofang/ZF-ComfyUI-PromptDirector) 的 H3 素材台。接线如下：

```text
H3 素材对齐出口.reference_plan_json
    → ZFH3MediaBundle.reference_plan_json
    → ZFMultimodalLLM.media_bundle
```

原素材台、对齐出口以及通向 H3 生成节点的接线照常使用。桥节点复用素材出口的缓存，读取已经对齐的素材，不重复解码，也不需要额外的空值判断、视频转换或“第一条有效音频”选择节点。

- 包含首尾帧、最多 9 路参考图、3 路参考视频及对应音频。
- 保持 H3 的 `<Picture n>`、`<Video n>`、`<Audio n>` 编号；音频按视频原声、导轨音频、参考音频的顺序整理。
- `final_audio` 是复用音轨的别名，不会作为一条新音频重复发送。
- 只有该桥节点依赖 PromptDirector 及其素材出口；运行工作流的同一台 ComfyUI 需要安装本插件的更新版本和提供该素材出口的 PromptDirector。

使用支持音频理解的第三方模型时，H3 工作流建议先用 `video_mode=sample_frames`、`audio_mode=native`。此时视频提供有时间顺序的画面，音频通过独立音频内容发送。若模型不支持音频，可显式设为 `audio_mode=ignore`。RH 配置固定忽略独立音频，反推结果不能据此还原台词或声线。

## 第三方 API 的 Key

把 `api_key.txt.example` 复制为 `api_key.txt`，只在里面放一行 Key：

```text
YOUR_API_KEY_HERE
```

也可以创建多个文件，例如 `api_key_xflow.txt`、`api_key_openai.txt`，然后在配置节点中选择对应文件名。`.gitignore` 已排除这些文件，Key 不会写进工作流 JSON。

本地 API 不需要 Key 时，将 `api_key_file` 留空。

以上 TXT 文件设置只适用于原配置节点。RH 配置按显式选择的 `rh_cloud` 或 `rh_env` 读取凭据，不读取这些文件，也不会自动更换凭据来源。

## 安装

在 `ComfyUI/custom_nodes` 目录执行：

```text
git clone https://github.com/Z-yaofang/ZF-ComfyUI-MultimodalAPI.git
cd ZF-ComfyUI-MultimodalAPI
python -m pip install -r requirements.txt
```

安装依赖时应使用实际启动 ComfyUI 的 Python 解释器。

## 第三方 API 快速使用

1. 重启 ComfyUI。
2. 在 `ZF/API` 分类添加 `ZF 多模态 API 配置` 与 `ZF 多模态反推（RH 接口兼容）`。
3. 在配置节点选择协议并填写你自己的 `api_base_url`。
4. 将真实 Key 写入本插件目录的 `api_key.txt`，不要把 Key 填进工作流。
5. 在反推节点填写服务端实际使用的模型名，并连接提示词。普通素材可直接接入，数量较多时使用通用素材包；H3 素材台使用上面的桥节点。

音频会在本地通过 ffmpeg 截断并压缩为 MP3 后以内联 Base64 发送。Gemini GenerateContent、OpenAI Chat Completions 和 OpenAI Responses 使用各自的原生音频字段；Anthropic Messages 当前不支持本节点的原生音频输入。模型本身也必须具备音频理解能力，仅使用普通文本/视觉模型可能被服务端拒绝。插件不会通过自动丢弃不支持的音频来掩盖这个错误。

常见 OpenAI 兼容接口可先使用：

```text
api_protocol = openai_chat_completions
api_base_url = https://你的接口地址/v1
api_key_file = api_key.txt
parameter_mode = auto
video_mode = sample_frames
audio_mode = native
```

确认服务端明确支持 Base64 原生视频后，才把 `video_mode` 改成 `native`。`native` 可以保留视频内嵌音频；`sample_frames` 兼容性更高，但只发送按时间顺序抽取的画面。

如果输入是 `IMAGE` 帧批，`native` 会使用 PyAV 在本地编码成视频，需要 ComfyUI 的 Python 环境提供 `av`。帧批的 `sample_frames` 路径不需要视频编码器；音频处理仍需要 ffmpeg。

## 第三方 API 端点规则

| 协议 | 自动追加 |
| --- | --- |
| OpenAI Chat Completions | `/chat/completions` |
| OpenAI Responses | `/responses` |
| Anthropic Messages | `/messages` |
| Gemini GenerateContent | `/models/{model}:generateContent` |

如果 `api_base_url` 已经是完整端点，插件不会重复追加。Gemini 地址也支持 `{model}` 占位符。

## 第三方 API 视频兼容性

`native` 使用以下常见多模态兼容格式：

- OpenAI Chat：`video_url`
- OpenAI Responses：`input_video`
- Gemini：Base64 `inline_data`

不同代理服务对视频字段的支持不统一。如果服务端报“不支持 video_url/input_video”，将 `video_mode` 改为 `sample_frames`。需要识别视频声音时，另接音频输入、使用含视频原声的 H3 素材包，或使用服务商明确支持的原生视频模型和 `native`。

素材接口的容量不等于服务商或模型的容量。即使协议支持图片、视频或音频，具体接口仍可能限制条数、总时长、请求大小，或不支持多视频／多音频。请使用模型实际支持的素材组合；插件会报告不支持或超限问题，不会自动删减素材后继续请求。普通 `VIDEO` 抽帧不会额外提取声音；需要声音时另接 `AUDIO`，或使用同时提供视频原声的 H3 素材包。

## 安全

- 只有显式连接 RH 配置才使用 RH 官方固定域名；第三方配置继续使用用户填写的地址，不经额外素材上传平台中转。
- RH 凭据来源必须显式选择，缺失时不会静默切换到另一个账户。密钥不进入配置输出、工作流或分发包。
- 不拉取远程模型列表。
- 不记录 Key。
- 日志不打印请求正文或 Base64 媒体。
- 输出和错误处理会对意外回显的凭据做脱敏；`raw_response` 也会清理可识别的内联 Base64 媒体。
- `.gitignore` 会排除 `api_key*.txt`、`*-API.txt`、`.env*`、私钥和证书文件。若自行使用其他文件名保存凭据，提交前仍应检查 `git status`。

## 范围

本插件只实现多模态大模型反推所需的文本 API，并提供可选的 RH 账户接入；不复制 RunningHub 的大量图像、视频、音频生成节点。公开接口约定的参考来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，没有复制其整套源码。

需要本地 llama.cpp 反推时，请安装 `ZF-ComfyUI-PromptDirector` 并使用其中的“ZF 导演台本地多模态写作（llama.cpp）”；API 反推继续使用本仓库节点。两条路径可以在同一工作流中共存，但节点实现分别由各自仓库维护。

本项目与 RunningHub、FOK API Tools 无隶属或官方合作关系。

## 迁移现有 RH LLM 工作流

`tools/migrate_rh_workflow.py` 会复制工作流，只替换 `RHLLMChatNode`、`RHSettingsNode` 以及两者之间的配置连接，不会覆盖源文件：

```text
python tools/migrate_rh_workflow.py "原工作流.json" "新工作流.json"
```

节点采用独立名称和独立配置类型，避免与已安装的 RH 插件发生类名冲突。

迁移脚本生成的是原有自定义 API 配置，不会自动启用 RH 共享凭据。需要使用 RH 账户时，再显式接上 `ZFRunningHubAPIConfig`。

## 许可证与兼容说明

本仓库的原创实现采用 MIT License。RH 接口兼容背景以及 FOK 互操作性评估说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 测试

在插件目录执行：

```text
pytest --import-mode=importlib tests
```

或使用 Python 标准库：

```text
python -m unittest discover -s tests -v
```
