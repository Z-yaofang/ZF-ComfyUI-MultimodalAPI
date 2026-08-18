# ZF-ComfyUI-MultimodalAPI

一个独立、轻量的 ComfyUI 多模态文本 API 插件，用于把现有的图片/视频反推工作流接到你自己的 API。

它不是 RunningHub API 的转发器，也不依赖 `ComfyUI_RH_OpenAPI` 或 `comfyui-FOK_API_tools`。节点接口特意接近 `RHLLMChatNode`，方便迁移已有工作流，但请求逻辑是独立实现。

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
  - `sample_frames`：本地用 ffmpeg 抽取时间顺序帧，兼容只支持图片的视觉模型，但不包含音频。
  - `ignore`：忽略连接的视频。

### ZF 多模态反推（RH 接口兼容）

- 最多 8 路 `IMAGE`。
- 1 路 `VIDEO`。
- 图片和视频可以同时发送，不会像当前 RH 节点那样在有图片时忽略视频。
- 保留 `role`、`prompt`、`model`、采样参数、`skip_error`。
- 输出 `response` 和 `raw_response` 两路 `STRING`。

## Key

把 `api_key.txt.example` 复制为 `api_key.txt`，只在里面放一行 Key：

```text
YOUR_API_KEY_HERE
```

也可以创建多个文件，例如 `api_key_xflow.txt`、`api_key_openai.txt`，然后在配置节点中选择对应文件名。`.gitignore` 已排除这些文件，Key 不会写进工作流 JSON。

本地 API 不需要 Key 时，将 `api_key_file` 留空。

## 安装

在 `ComfyUI/custom_nodes` 目录执行：

```text
git clone https://github.com/Z-yaofang/ZF-ComfyUI-MultimodalAPI.git
cd ZF-ComfyUI-MultimodalAPI
python -m pip install -r requirements.txt
```

安装依赖时应使用实际启动 ComfyUI 的 Python 解释器。

## 快速使用

1. 重启 ComfyUI。
2. 在 `ZF/API` 分类添加 `ZF 多模态 API 配置` 与 `ZF 多模态反推（RH 接口兼容）`。
3. 在配置节点选择协议并填写你自己的 `api_base_url`。
4. 将真实 Key 写入本插件目录的 `api_key.txt`，不要把 Key 填进工作流。
5. 在反推节点填写服务端实际使用的模型名，并连接提示词、图片或视频。

常见 OpenAI 兼容接口可先使用：

```text
api_protocol = openai_chat_completions
api_base_url = https://你的接口地址/v1
api_key_file = api_key.txt
parameter_mode = auto
video_mode = sample_frames
```

确认服务端明确支持 Base64 原生视频后，才把 `video_mode` 改成 `native`。`native` 可以保留视频内嵌音频；`sample_frames` 兼容性更高，但只发送按时间顺序抽取的画面。

## 端点规则

| 协议 | 自动追加 |
| --- | --- |
| OpenAI Chat Completions | `/chat/completions` |
| OpenAI Responses | `/responses` |
| Anthropic Messages | `/messages` |
| Gemini GenerateContent | `/models/{model}:generateContent` |

如果 `api_base_url` 已经是完整端点，插件不会重复追加。Gemini 地址也支持 `{model}` 占位符。

## 视频兼容性

`native` 使用以下常见多模态兼容格式：

- OpenAI Chat：`video_url`
- OpenAI Responses：`input_video`
- Gemini：Base64 `inline_data`

不同代理服务对视频字段的支持不统一。如果服务端报“不支持 video_url/input_video”，将 `video_mode` 改为 `sample_frames`。混合流里若必须识别视频声音，应使用服务商明确支持的原生视频模型和 `native`。

## 安全

- 不上传到固定第三方平台。
- 不拉取远程模型列表。
- 不记录 Key。
- 日志不打印请求正文或 Base64 媒体。
- `raw_response` 会清理响应中意外回显的 Base64 数据和 Bearer Key。
- `.gitignore` 会排除 `api_key*.txt`、`*-API.txt`、`.env*`、私钥和证书文件。若自行使用其他文件名保存凭据，提交前仍应检查 `git status`。

## 范围

本插件只实现多模态大模型反推所需的通用文本 API，不复制 RunningHub 的大量图像、视频、音频生成节点。

需要本地 llama.cpp 反推时，请安装 `ZF-ComfyUI-PromptDirector` 并使用其中的“ZF 导演台本地多模态写作（llama.cpp）”；API 反推继续使用本仓库节点。两条路径可以在同一工作流中共存，但节点实现分别由各自仓库维护。

本项目与 RunningHub、FOK API Tools 无隶属或官方合作关系。

## 迁移现有 RH LLM 工作流

`tools/migrate_rh_workflow.py` 会复制工作流，只替换 `RHLLMChatNode`、`RHSettingsNode` 以及两者之间的配置连接，不会覆盖源文件：

```text
python tools/migrate_rh_workflow.py "原工作流.json" "新工作流.json"
```

节点采用独立名称和独立配置类型，避免与已安装的 RH 插件发生类名冲突。

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
