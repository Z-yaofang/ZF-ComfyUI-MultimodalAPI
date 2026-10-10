from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from PIL import Image

try:
    import folder_paths
except ImportError:
    folder_paths = None


log = logging.getLogger(__name__)
NODE_DIR = Path(__file__).resolve().parent

OPENAI_CHAT = "openai_chat_completions"
OPENAI_RESPONSES = "openai_responses"
ANTHROPIC_MESSAGES = "anthropic_messages"
GEMINI_GENERATE_CONTENT = "gemini_generatecontent"
API_PROTOCOLS = [OPENAI_CHAT, OPENAI_RESPONSES, ANTHROPIC_MESSAGES, GEMINI_GENERATE_CONTENT]
RH_BASE_URLS = {"cn": "https://llm.runninghub.cn/v1", "ai": "https://llm.runninghub.ai/v1"}

VIDEO_MODES = ["auto", "native", "sample_frames", "ignore"]
PARAMETER_MODES = ["auto", "full", "minimal"]
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3

DEFAULT_CONFIG = {
    "api_protocol": OPENAI_CHAT,
    "api_base_url": "https://api.openai.com/v1",
    "api_key_file": "api_key.txt",
    "timeout": 300,
    "parameter_mode": "auto",
    "video_mode": "auto",
    "video_max_seconds": 15,
    "video_max_mb": 10,
    "video_sample_frames": 8,
    "anthropic_version": "2023-06-01",
    "audio_max_seconds": 180,
    "audio_max_mb": 18,
    "audio_mode": "native",
    "request_max_mb": 64,
}


@lru_cache(maxsize=1)
def _media_processor():
    # Also supports tools/tests loading this module directly by filesystem path.
    spec = importlib.util.spec_from_file_location("zf_api_media_processing", NODE_DIR / "media_processing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_key_path(api_key_file: str) -> Optional[Path]:
    filename = (api_key_file or "").strip()
    if not filename:
        return None
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("api_key_file 只能填写本插件目录内的文件名，不能填写路径。")
    return NODE_DIR / filename


def _read_api_key(api_key_file: str) -> str:
    key_path = _safe_key_path(api_key_file)
    if key_path is None:
        return ""
    if not key_path.is_file():
        raise FileNotFoundError(
            f"未找到 API Key 文件：{key_path.name}。请在插件目录内创建该文件；本地无鉴权 API 可将 api_key_file 留空。"
        )
    key = key_path.read_text(encoding="utf-8-sig").strip()
    if key in {"", "YOUR_API_KEY_HERE"}:
        return ""
    return key


def _normalize_config(api_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if api_config:
        config.update(api_config)
    if config.get("provider") == "runninghub":
        region = config.get("endpoint_region", "cn")
        if region not in RH_BASE_URLS:
            raise ValueError("RH endpoint_region 必须是 cn 或 ai。")
        if config.get("auth_source", "rh_cloud") not in ("rh_cloud", "rh_env"):
            raise ValueError("RH auth_source 必须是 rh_cloud 或 rh_env。")
        # Shared platform credentials must never follow an arbitrary URL or protocol.
        config.update(api_protocol=OPENAI_CHAT, api_base_url=RH_BASE_URLS[region],
                      api_key_file="", audio_mode="ignore", parameter_mode="auto",
                      video_max_seconds=15, video_max_mb=10)
        if config["video_mode"] == "auto":
            config["video_mode"] = "sample_frames"
    if config["api_protocol"] not in API_PROTOCOLS:
        raise ValueError(f"不支持的 api_protocol：{config['api_protocol']}")
    if config["video_mode"] not in VIDEO_MODES:
        raise ValueError(f"不支持的 video_mode：{config['video_mode']}")
    if config["parameter_mode"] not in PARAMETER_MODES:
        raise ValueError(f"不支持的 parameter_mode：{config['parameter_mode']}")
    if config["audio_mode"] not in ("native", "ignore"):
        raise ValueError(f"不支持的 audio_mode：{config['audio_mode']}")
    config["timeout"] = max(10, min(int(config["timeout"]), 900))
    config["video_max_seconds"] = max(1, min(int(config["video_max_seconds"]), 120))
    config["video_max_mb"] = max(1, min(int(config["video_max_mb"]), 200))
    config["video_sample_frames"] = max(1, min(int(config["video_sample_frames"]), 32))
    config["audio_max_seconds"] = max(1, min(int(config["audio_max_seconds"]), 600))
    config["audio_max_mb"] = max(1, min(int(config["audio_max_mb"]), 100))
    config["request_max_mb"] = max(1, min(int(config["request_max_mb"]), 512))
    return config


def _runninghub_key(auth_source: str) -> str:
    if auth_source == "rh_env":
        key = os.environ.get("RH_API_KEY", "")
        missing = "未配置 RH_API_KEY 环境变量；本地 RH 模式需使用有 LLM API 权限的 Key。"
    elif auth_source == "rh_cloud":
        try:
            from server import PromptServer
        except ImportError:
            key = ""
        else:
            key = getattr(getattr(PromptServer, "instance", None), "shared_api_key", "")
        missing = "未获得 RH 云端系统凭据；请在 RH 云端运行并让客服确认插件可读取 shared_api_key。不会改用本地 TXT 或其他账户。"
    else:
        raise ValueError("RH auth_source 必须是 rh_cloud 或 rh_env。")
    if not isinstance(key, str) or not key.strip() or key.strip().lower() == "unknown":
        raise RuntimeError(missing)
    return key.strip()


def _endpoint(api_protocol: str, api_base_url: str, model: str) -> str:
    defaults = {
        OPENAI_CHAT: "https://api.openai.com/v1",
        OPENAI_RESPONSES: "https://api.openai.com/v1",
        ANTHROPIC_MESSAGES: "https://api.anthropic.com/v1",
        GEMINI_GENERATE_CONTENT: "https://generativelanguage.googleapis.com/v1beta",
    }
    base = (api_base_url or "").strip() or defaults[api_protocol]
    base = base.rstrip("/")

    if api_protocol == OPENAI_CHAT:
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    if api_protocol == OPENAI_RESPONSES:
        return base if base.endswith("/responses") else f"{base}/responses"
    if api_protocol == ANTHROPIC_MESSAGES:
        return base if base.endswith("/messages") else f"{base}/messages"
    if "{model}" in base:
        return base.replace("{model}", model)
    if base.endswith(":generateContent"):
        return base
    return f"{base}/models/{model}:generateContent"


def _headers(api_protocol: str, api_key: str, anthropic_version: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_protocol in {OPENAI_CHAT, OPENAI_RESPONSES}:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    elif api_protocol == ANTHROPIC_MESSAGES:
        headers["anthropic-version"] = anthropic_version or "2023-06-01"
        if api_key:
            headers["x-api-key"] = api_key
    elif api_key:
        headers["x-goog-api-key"] = api_key
    return headers


def _iter_pil_images(image: Any) -> Iterable[Image.Image]:
    if image is None:
        return
    if getattr(image, "ndim", None) == 3:
        batch = image.unsqueeze(0)
    elif getattr(image, "ndim", None) == 4:
        batch = image
    else:
        raise ValueError("IMAGE 输入必须是 HWC 或 NHWC 张量。")

    for item in batch:
        array = item.detach().cpu().float().clamp(0, 1).mul(255).byte().numpy()
        channels = array.shape[-1]
        if channels == 1:
            yield Image.fromarray(array[:, :, 0], mode="L")
        elif channels == 3:
            yield Image.fromarray(array, mode="RGB")
        elif channels == 4:
            yield Image.fromarray(array, mode="RGBA")
        else:
            raise ValueError(f"不支持的 IMAGE 通道数：{channels}")


def _jpeg_media(pil_image: Image.Image, label: str) -> Dict[str, str]:
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    buffer = BytesIO()
    pil_image.save(buffer, format="JPEG", quality=90, optimize=True)
    return {
        "kind": "image",
        "label": label,
        "mime_type": "image/jpeg",
        "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }


def _collect_image_media(images: Iterable[Any]) -> List[Dict[str, str]]:
    media: List[Dict[str, str]] = []
    for picture_index, image in enumerate(images, start=1):
        batch = list(_iter_pil_images(image))
        for batch_index, pil_image in enumerate(batch, start=1):
            label = f"<Picture {picture_index}>"
            if len(batch) > 1:
                label = f"<Picture {picture_index}, image {batch_index}/{len(batch)}>"
            media.append(_jpeg_media(pil_image, label))
    return media


def _existing_path(value: Any) -> Optional[Path]:
    if not isinstance(value, (str, os.PathLike)):
        return None
    candidate = Path(value)
    if candidate.is_file():
        return candidate.resolve()
    if folder_paths is not None and not candidate.is_absolute():
        for directory_getter in (
            folder_paths.get_input_directory,
            folder_paths.get_output_directory,
            folder_paths.get_temp_directory,
        ):
            resolved = Path(directory_getter()) / candidate
            if resolved.is_file():
                return resolved.resolve()
    return None


def _copy_file_object(file_object: Any, suffix: str = ".mp4") -> Optional[Path]:
    if not hasattr(file_object, "read"):
        return None
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        if hasattr(file_object, "seek"):
            file_object.seek(0)
        with handle:
            shutil.copyfileobj(file_object, handle)
        return Path(handle.name)
    except Exception:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        return None


def _materialize_video(video: Any) -> Tuple[Path, bool]:
    direct = _existing_path(video)
    if direct:
        return direct, False

    if isinstance(video, dict):
        for key in ("file_path", "path", "filename"):
            direct = _existing_path(video.get(key))
            if direct:
                return direct, False

    private_file = getattr(video, "_VideoFromFile__file", None)
    direct = _existing_path(private_file)
    if direct:
        return direct, False
    copied = _copy_file_object(private_file)
    if copied:
        return copied, True

    for attribute in ("path", "file"):
        value = getattr(video, attribute, None)
        direct = _existing_path(value)
        if direct:
            return direct, False
        copied = _copy_file_object(value)
        if copied:
            return copied, True

    if hasattr(video, "get_stream_source"):
        try:
            source = video.get_stream_source()
        except Exception:
            source = None
        direct = _existing_path(source)
        if direct:
            return direct, False
        copied = _copy_file_object(source)
        if copied:
            return copied, True

    if hasattr(video, "save_to"):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        handle.close()
        try:
            video.save_to(handle.name)
            path = Path(handle.name)
            if path.is_file() and path.stat().st_size:
                return path, True
        except Exception:
            pass
        Path(handle.name).unlink(missing_ok=True)

    raise RuntimeError("无法从 VIDEO 输入解析出可读取的视频文件。")


def _waveform_audio_path(audio: Dict[str, Any]) -> Path:
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate") or 44100)
    if waveform is None or not hasattr(waveform, "detach"):
        raise RuntimeError("AUDIO 输入缺少 waveform 张量。")
    samples = waveform.detach().cpu().float()
    if samples.ndim == 3:
        samples = samples[0]
    elif samples.ndim == 1:
        samples = samples.unsqueeze(0)
    if samples.ndim != 2:
        raise RuntimeError("AUDIO waveform 必须是 [batch, channels, samples] 或 [channels, samples]。")
    pcm = samples.clamp(-1, 1).mul(32767).round().short().transpose(0, 1).contiguous().numpy()
    output = _new_temp_path(".wav")
    with wave.open(str(output), "wb") as writer:
        writer.setnchannels(int(samples.shape[0]))
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm.tobytes())
    return output


def _materialize_audio(audio: Any) -> Tuple[Path, bool]:
    direct = _existing_path(audio)
    if direct:
        return direct, False
    if isinstance(audio, dict):
        for key in ("file_path", "path", "filename"):
            direct = _existing_path(audio.get(key))
            if direct:
                return direct, False
        if audio.get("waveform") is not None:
            return _waveform_audio_path(audio), True
    for attribute in ("path", "file"):
        value = getattr(audio, attribute, None)
        direct = _existing_path(value)
        if direct:
            return direct, False
        copied = _copy_file_object(value, ".wav")
        if copied:
            return copied, True
    raise RuntimeError("无法从 AUDIO 输入解析出可读取的音频。")


def _video_duration(path: Path) -> Optional[float]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def _audio_media(audio: Any, max_seconds: int, max_mb: int) -> Dict[str, str]:
    source, source_owned = _materialize_audio(audio)
    encoded: Optional[Path] = None
    try:
        ffmpeg = shutil.which("ffmpeg")
        duration = _video_duration(source)
        max_bytes = max_mb * 1024 * 1024
        suffix = source.suffix.lower()
        if ffmpeg:
            encoded = _new_temp_path(".mp3")
            _run_ffmpeg(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-t",
                    str(max_seconds),
                    "-vn",
                    "-ar",
                    "44100",
                    "-b:a",
                    "128k",
                    str(encoded),
                ],
                "音频压缩",
            )
            if encoded.stat().st_size > max_bytes:
                raise RuntimeError(f"压缩后音频仍超过 {max_mb}MB。请缩短参考片段。")
            payload_path = encoded
            mime_type = "audio/mp3"
            audio_format = "mp3"
        elif suffix in {".wav", ".mp3"} and source.stat().st_size <= max_bytes and (duration is None or duration <= max_seconds):
            payload_path = source
            mime_type = "audio/wav" if suffix == ".wav" else "audio/mp3"
            audio_format = suffix[1:]
        else:
            raise RuntimeError("音频需要压缩或转换，但系统找不到 ffmpeg。")
        return {
            "kind": "audio",
            "label": "<Audio 1>",
            "mime_type": mime_type,
            "format": audio_format,
            "data": base64.b64encode(payload_path.read_bytes()).decode("ascii"),
        }
    finally:
        if encoded is not None:
            encoded.unlink(missing_ok=True)
        if source_owned:
            source.unlink(missing_ok=True)


def _run_ffmpeg(command: List[str], operation: str) -> None:
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg {operation}失败：{detail}")


def _new_temp_path(suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.close()
    path = Path(handle.name)
    path.unlink(missing_ok=True)
    return path


def _fit_video(path: Path, max_seconds: int, max_bytes: int) -> Tuple[Path, bool]:
    duration = _video_duration(path)
    if path.stat().st_size <= max_bytes and (duration is None or duration <= max_seconds):
        return path, False

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("视频超过设定限制，但系统找不到 ffmpeg。")

    output = _new_temp_path(".mp4")
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-t",
        str(max_seconds),
        "-vf",
        "scale='min(1280,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(output),
    ]
    try:
        _run_ffmpeg(command, "视频压缩")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    if output.stat().st_size <= max_bytes:
        return output, True

    smaller = _new_temp_path(".mp4")
    retry_command = [
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-t",
        str(max_seconds),
        "-vf",
        "scale='min(960,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "33",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(smaller),
    ]
    try:
        _run_ffmpeg(retry_command, "二次视频压缩")
    except Exception:
        smaller.unlink(missing_ok=True)
        raise
    finally:
        output.unlink(missing_ok=True)
    if smaller.stat().st_size > max_bytes:
        smaller.unlink(missing_ok=True)
        raise RuntimeError(f"压缩后视频仍超过 {max_bytes // (1024 * 1024)}MB。")
    return smaller, True


def _native_video_media(video: Any, max_seconds: int, max_mb: int) -> Dict[str, str]:
    source, source_owned = _materialize_video(video)
    fitted: Optional[Path] = None
    fitted_owned = False
    try:
        fitted, fitted_owned = _fit_video(source, max_seconds, max_mb * 1024 * 1024)
        return {
            "kind": "video",
            "label": "<Video 1>",
            "mime_type": "video/mp4",
            "data": base64.b64encode(fitted.read_bytes()).decode("ascii"),
        }
    finally:
        if fitted_owned and fitted is not None:
            fitted.unlink(missing_ok=True)
        if source_owned:
            source.unlink(missing_ok=True)


def _sample_video_media(video: Any, max_seconds: int, frame_count: int) -> List[Dict[str, str]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("sample_frames 模式需要 ffmpeg。")

    source, source_owned = _materialize_video(video)
    frame_dir = Path(tempfile.mkdtemp(prefix="zf_multimodal_frames_"))
    try:
        duration = _video_duration(source)
        sampled_duration = min(duration, max_seconds) if duration else float(max_seconds)
        fps = max(frame_count / max(sampled_duration, 0.1), 0.01)
        pattern = frame_dir / "frame_%03d.jpg"
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-t",
            str(max_seconds),
            "-vf",
            f"fps={fps:.8f},scale='min(1024,iw)':-2",
            "-frames:v",
            str(frame_count),
            "-q:v",
            "3",
            str(pattern),
        ]
        _run_ffmpeg(command, "视频抽帧")
        frame_paths = sorted(frame_dir.glob("frame_*.jpg"))
        if not frame_paths:
            raise RuntimeError("视频抽帧没有产生任何图像。")
        total = len(frame_paths)
        return [
            {
                "kind": "image",
                "label": f"<Video 1, chronological frame {index}/{total}>",
                "mime_type": "image/jpeg",
                "data": base64.b64encode(frame_path.read_bytes()).decode("ascii"),
            }
            for index, frame_path in enumerate(frame_paths, start=1)
        ]
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
        if source_owned:
            source.unlink(missing_ok=True)


def _resolved_video_mode(api_protocol: str, video_mode: str) -> str:
    if video_mode != "auto":
        return video_mode
    if api_protocol == ANTHROPIC_MESSAGES:
        return "sample_frames"
    return "native"


def _prepare_media(
    api_protocol: str,
    images: Iterable[Any],
    video: Any,
    video_mode: str,
    video_max_seconds: int,
    video_max_mb: int,
    video_sample_frames: int,
    audio: Any = None,
    audio_max_seconds: int = 180,
    audio_max_mb: int = 18,
) -> Tuple[List[Dict[str, str]], Optional[Dict[str, str]], Optional[Dict[str, str]], str]:
    image_media = _collect_image_media(images)
    native_video = None
    native_audio = None
    note = ""
    if audio is not None:
        if api_protocol == ANTHROPIC_MESSAGES:
            raise ValueError("Anthropic Messages 当前不支持本节点的 AUDIO 输入；请改用 Gemini 或支持音频的 OpenAI 模型。")
        native_audio = _audio_media(audio, audio_max_seconds, audio_max_mb)
    if video is None:
        return image_media, native_video, native_audio, note

    resolved_mode = _resolved_video_mode(api_protocol, video_mode)
    if resolved_mode == "ignore":
        return image_media, native_video, native_audio, "Connected video was intentionally ignored by video_mode=ignore."
    if resolved_mode == "sample_frames":
        sampled_frames = _sample_video_media(video, video_max_seconds, video_sample_frames)
        image_media.extend(sampled_frames)
        note = (
            "The connected video is represented by chronological sampled frames. "
            "Frame sampling does not include the video's audio track."
        )
        return image_media, native_video, native_audio, note
    if api_protocol == ANTHROPIC_MESSAGES:
        raise ValueError("Anthropic Messages 不支持本节点的原生视频格式；请使用 auto 或 sample_frames。")
    native_video = _native_video_media(video, video_max_seconds, video_max_mb)
    return image_media, native_video, native_audio, note


def _data_uri(media: Dict[str, str]) -> str:
    return f"data:{media['mime_type']};base64,{media['data']}"


def _payload_media_items(media: Any, kind: str) -> List[Dict[str, str]]:
    """Normalize legacy single media and multi-media inputs without relabeling."""
    if media is None:
        return []
    if isinstance(media, dict):
        return [media]
    if isinstance(media, (list, tuple)) and all(isinstance(item, dict) for item in media):
        return list(media)
    raise ValueError(f"{kind} 请求素材必须是单个素材字典或素材字典列表。")


def _openai_chat_payload(
    model: str,
    role: str,
    prompt: str,
    images: List[Dict[str, str]],
    video: Any,
    audio: Any = None,
) -> Dict[str, Any]:
    videos = _payload_media_items(video, "视频")
    audios = _payload_media_items(audio, "音频")
    messages: List[Dict[str, Any]] = []
    if role.strip():
        messages.append({"role": "system", "content": role})
    if not images and not videos and not audios:
        messages.append({"role": "user", "content": prompt})
        return {"model": model, "messages": messages}

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append({"type": "text", "text": image["label"]})
        content.append({"type": "image_url", "image_url": {"url": _data_uri(image)}})
    for item in videos:
        content.append({"type": "text", "text": item["label"]})
        content.append({"type": "video_url", "video_url": {"url": _data_uri(item)}})
    for item in audios:
        content.append({"type": "text", "text": item["label"]})
        content.append({"type": "input_audio", "input_audio": {"data": item["data"], "format": item["format"]}})
    messages.append({"role": "user", "content": content})
    return {"model": model, "messages": messages}


def _openai_responses_payload(
    model: str,
    role: str,
    prompt: str,
    images: List[Dict[str, str]],
    video: Any,
    audio: Any = None,
) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for image in images:
        content.append({"type": "input_text", "text": image["label"]})
        content.append({"type": "input_image", "image_url": _data_uri(image)})
    for item in _payload_media_items(video, "视频"):
        content.append({"type": "input_text", "text": item["label"]})
        content.append({"type": "input_video", "video_url": _data_uri(item)})
    for item in _payload_media_items(audio, "音频"):
        content.append({"type": "input_text", "text": item["label"]})
        content.append({"type": "input_audio", "input_audio": {"data": item["data"], "format": item["format"]}})
    payload: Dict[str, Any] = {"model": model, "input": [{"role": "user", "content": content}]}
    if role.strip():
        payload["instructions"] = role
    return payload


def _anthropic_payload(
    model: str,
    role: str,
    prompt: str,
    images: List[Dict[str, str]],
    video: Any = None,
    audio: Any = None,
) -> Dict[str, Any]:
    if _payload_media_items(video, "视频"):
        raise ValueError("Anthropic Messages 不支持原生视频输入，请使用 sample_frames 视频抽帧模式。")
    if _payload_media_items(audio, "音频"):
        raise ValueError("Anthropic Messages 不支持音频输入，请使用支持音频的接口或移除音频素材。")
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append({"type": "text", "text": image["label"]})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image["mime_type"],
                    "data": image["data"],
                },
            }
        )
    payload: Dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": content}]}
    if role.strip():
        payload["system"] = role
    return payload


def _gemini_payload(
    role: str,
    prompt: str,
    images: List[Dict[str, str]],
    video: Any,
    audio: Any = None,
) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for image in images:
        parts.append({"text": image["label"]})
        parts.append({"inline_data": {"mime_type": image["mime_type"], "data": image["data"]}})
    for item in _payload_media_items(video, "视频"):
        parts.append({"text": item["label"]})
        parts.append({"inline_data": {"mime_type": item["mime_type"], "data": item["data"]}})
    for item in _payload_media_items(audio, "音频"):
        parts.append({"text": item["label"]})
        parts.append({"inline_data": {"mime_type": item["mime_type"], "data": item["data"]}})
    payload: Dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}
    if role.strip():
        payload["systemInstruction"] = {"parts": [{"text": role}]}
    return payload


def _apply_generation_parameters(
    payload: Dict[str, Any],
    api_protocol: str,
    parameter_mode: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    presence_penalty: float,
    frequency_penalty: float,
    reasoning_effort: str,
    seed: int,
) -> None:
    if api_protocol == OPENAI_CHAT:
        if parameter_mode == "minimal":
            return
        payload["max_tokens"] = int(max_tokens)
        payload["temperature"] = float(temperature)
        if parameter_mode == "full" or float(top_p) != 1.0:
            payload["top_p"] = float(top_p)
        if parameter_mode == "full" or float(presence_penalty) != 0.0:
            payload["presence_penalty"] = float(presence_penalty)
        if parameter_mode == "full" or float(frequency_penalty) != 0.0:
            payload["frequency_penalty"] = float(frequency_penalty)
        if reasoning_effort != "none":
            payload["reasoning_effort"] = reasoning_effort
        if int(seed) >= 0:
            payload["seed"] = int(seed)
        return

    if api_protocol == OPENAI_RESPONSES:
        if parameter_mode != "minimal":
            payload["max_output_tokens"] = int(max_tokens)
            payload["temperature"] = float(temperature)
            if parameter_mode == "full" or float(top_p) != 1.0:
                payload["top_p"] = float(top_p)
            if reasoning_effort != "none":
                payload["reasoning"] = {"effort": reasoning_effort}
        return

    if api_protocol == ANTHROPIC_MESSAGES:
        payload["max_tokens"] = int(max_tokens)
        if parameter_mode != "minimal":
            payload["temperature"] = float(temperature)
            if parameter_mode == "full" or float(top_p) != 1.0:
                payload["top_p"] = float(top_p)
        return

    if parameter_mode == "minimal":
        return
    generation_config: Dict[str, Any] = {
        "maxOutputTokens": int(max_tokens),
        "temperature": float(temperature),
    }
    if parameter_mode == "full" or float(top_p) != 1.0:
        generation_config["topP"] = float(top_p)
    if int(seed) >= 0:
        generation_config["seed"] = int(seed)
    payload["generationConfig"] = generation_config


def _build_request(
    config: Dict[str, Any],
    model: str,
    role: str,
    prompt: str,
    images: List[Dict[str, str]],
    video: Optional[Dict[str, str]],
    audio: Optional[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    top_p: float,
    presence_penalty: float,
    frequency_penalty: float,
    reasoning_effort: str,
    seed: int,
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    is_rh = config.get("provider") == "runninghub"
    if is_rh:
        config = _normalize_config(config)
        videos = _payload_media_items(video, "video")
        if videos and (images or len(videos) > 1):
            raise ValueError("RH native 模式仅用于单视频且不混接图片；图片与视频、多视频请用 sample_frames。")
        if _payload_media_items(audio, "audio"):
            raise ValueError("RH 模式不发送独立音频；请使用 RH 配置节点的显式忽略音频策略。")
    protocol = config["api_protocol"]
    api_key = _runninghub_key(config.get("auth_source", "rh_cloud")) if is_rh else _read_api_key(config["api_key_file"])
    url = _endpoint(protocol, config["api_base_url"], model)
    headers = _headers(protocol, api_key, config["anthropic_version"])

    if protocol == OPENAI_CHAT:
        payload = _openai_chat_payload(model, role, prompt, images, video, audio)
    elif protocol == OPENAI_RESPONSES:
        payload = _openai_responses_payload(model, role, prompt, images, video, audio)
    elif protocol == ANTHROPIC_MESSAGES:
        payload = _anthropic_payload(model, role, prompt, images, video, audio)
    else:
        payload = _gemini_payload(role, prompt, images, video, audio)

    _apply_generation_parameters(
        payload,
        protocol,
        config["parameter_mode"],
        temperature,
        max_tokens,
        top_p,
        presence_penalty,
        frequency_penalty,
        reasoning_effort,
        seed,
    )
    if is_rh:
        payload.pop("seed", None)
    return url, headers, payload


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int,
               allow_redirects: bool = True) -> Dict[str, Any]:
    secrets = _header_secrets(headers)
    last_error: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        if attempt:
            time.sleep(min(2 ** attempt, 5))
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout,
                                     allow_redirects=allow_redirects)
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < MAX_RETRIES:
                continue
            raise RuntimeError(f"API 请求失败：{_redact_text(str(exc), secrets)}") from None

        if response.status_code in RETRY_STATUS_CODES and attempt + 1 < MAX_RETRIES:
            last_error = RuntimeError(f"HTTP {response.status_code}")
            continue
        if not allow_redirects and 300 <= response.status_code < 400:
            raise RuntimeError("RH 接口返回重定向，已停止请求以保护系统凭据；请检查官方服务状态。")
        if not response.ok:
            detail = _redact_text(response.text, secrets)[:1200]
            raise RuntimeError(f"HTTP {response.status_code}：{detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"API 返回的不是有效 JSON：{_redact_text(response.text, secrets)[:500]}") from None
    raise RuntimeError(f"API 请求失败：{_redact_text(str(last_error), secrets)}")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks = []
    for item in content:
        if isinstance(item, dict) and item.get("text"):
            chunks.append(str(item["text"]))
    return "\n".join(chunks)


def _response_text(api_protocol: str, data: Dict[str, Any]) -> str:
    if api_protocol == OPENAI_CHAT:
        choices = data.get("choices") or []
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        return _content_text(message.get("content")) or str(first.get("text") or "")

    if api_protocol == OPENAI_RESPONSES:
        if data.get("output_text"):
            return str(data["output_text"])
        chunks = []
        for output in data.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if isinstance(content, dict) and content.get("text"):
                    chunks.append(str(content["text"]))
        return "".join(chunks)

    if api_protocol == ANTHROPIC_MESSAGES:
        return _content_text(data.get("content"))

    chunks = []
    for candidate in data.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []):
            if isinstance(part, dict) and part.get("text"):
                chunks.append(str(part["text"]))
    return "".join(chunks)


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")
    return cleaned.strip()


def _header_secrets(headers: Dict[str, str]) -> Tuple[str, ...]:
    values = []
    for name, value in headers.items():
        if name.lower() == "authorization":
            values.append(value.removeprefix("Bearer "))
        elif name.lower() in ("x-api-key", "x-goog-api-key"):
            values.append(value)
    return tuple(value for value in values if value)


def _redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***").replace(json.dumps(secret)[1:-1], "***")
    text = re.sub(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=_-]+", "<base64 media omitted>", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1***", text)
    return text


def _safe_raw_response(data: Dict[str, Any], secrets: Iterable[str] = ()) -> str:
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    return _redact_text(raw, secrets)


class ZFMultimodalAPIConfig:
    DESCRIPTION = (
        "配置自定义多模态大模型 API。Key 从插件目录内的文本文件读取，不保存到工作流。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_protocol": (API_PROTOCOLS, {"default": OPENAI_CHAT}),
                "api_base_url": (
                    "STRING",
                    {"default": "https://api.openai.com/v1", "multiline": False},
                ),
                "api_key_file": (
                    "STRING",
                    {
                        "default": "api_key.txt",
                        "multiline": False,
                        "tooltip": "只填本插件目录内的文件名。本地无鉴权 API 可留空。",
                    },
                ),
                "timeout": ("INT", {"default": 300, "min": 10, "max": 900, "step": 10}),
                "parameter_mode": (
                    PARAMETER_MODES,
                    {
                        "default": "auto",
                        "tooltip": "auto 只发送有意义的兼容参数；full 发送完整参数；minimal 尽量少发参数。",
                    },
                ),
                "video_mode": (
                    VIDEO_MODES,
                    {
                        "default": "auto",
                        "tooltip": "auto：OpenAI/Gemini 原生视频，Anthropic 自动抽帧；sample_frames 不包含音频。",
                    },
                ),
                "video_max_seconds": (
                    "INT",
                    {"default": 15, "min": 1, "max": 120, "step": 1},
                ),
                "video_max_mb": (
                    "INT",
                    {"default": 10, "min": 1, "max": 200, "step": 1},
                ),
                "video_sample_frames": (
                    "INT",
                    {"default": 8, "min": 1, "max": 32, "step": 1},
                ),
                "anthropic_version": (
                    "STRING",
                    {"default": "2023-06-01", "multiline": False},
                ),
                "audio_max_seconds": (
                    "INT",
                    {
                        "default": 180,
                        "min": 1,
                        "max": 600,
                        "step": 1,
                        "tooltip": "参考音频只发送开头这段时长；音乐分析通常建议 60–180 秒。",
                    },
                ),
                "audio_max_mb": (
                    "INT",
                    {
                        "default": 18,
                        "min": 1,
                        "max": 100,
                        "step": 1,
                        "tooltip": "内联音频大小上限；Gemini 官方内联请求总大小上限为 20MB，默认预留提示词空间。",
                    },
                ),
            },
            "optional": {
                # Append only: saved workflows keep the original widget order.
                "audio_mode": (["native", "ignore"], {
                    "default": "native",
                    "tooltip": "native发送所有已接音频；ignore明确不听取独立音频，适合不支持音频的视觉模型。",
                }),
                "request_max_mb": ("INT", {
                    "default": 64, "min": 1, "max": 512,
                    "tooltip": "整个JSON请求含Base64媒体的大小上限；按服务商实际限制设置。",
                }),
            },
        }

    RETURN_TYPES = ("ZF_MULTIMODAL_API_CONFIG",)
    RETURN_NAMES = ("api_config",)
    FUNCTION = "build"
    CATEGORY = "ZF/API"

    def build(
        self,
        api_protocol: str,
        api_base_url: str,
        api_key_file: str,
        timeout: int,
        parameter_mode: str,
        video_mode: str,
        video_max_seconds: int,
        video_max_mb: int,
        video_sample_frames: int,
        anthropic_version: str,
        audio_max_seconds: int = 180,
        audio_max_mb: int = 18,
        audio_mode: str = "native",
        request_max_mb: int = 64,
    ):
        return (
            _normalize_config(
                {
                    "api_protocol": api_protocol,
                    "api_base_url": api_base_url,
                    "api_key_file": api_key_file,
                    "timeout": timeout,
                    "parameter_mode": parameter_mode,
                    "video_mode": video_mode,
                    "video_max_seconds": video_max_seconds,
                    "video_max_mb": video_max_mb,
                    "video_sample_frames": video_sample_frames,
                    "anthropic_version": anthropic_version,
                    "audio_max_seconds": audio_max_seconds,
                    "audio_max_mb": audio_max_mb,
                    "audio_mode": audio_mode,
                    "request_max_mb": request_max_mb,
                }
            ),
        )


class ZFRunningHubAPIConfig:
    DESCRIPTION = (
        "RH 云端默认使用平台系统凭据，无需 TXT 或填写 Key；本地可显式选择 RH_API_KEY 环境变量。"
        "视频默认抽帧后与图片一起发送；独立音频明确忽略，不会被听取。"
        "原生视频仅用于单视频且无图片；实际模型支持与账户结算需在 RH 验证。"
    )
    RETURN_TYPES = ("ZF_MULTIMODAL_API_CONFIG",)
    RETURN_NAMES = ("api_config",)
    FUNCTION = "build"
    CATEGORY = "ZF/API"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "auth_source": (["rh_cloud", "rh_env"], {
                "default": "rh_cloud", "tooltip": "rh_cloud 只用 RH 平台凭据；rh_env 只用环境变量 RH_API_KEY，不会自动更换账户。",
            }),
            "endpoint_region": (["cn", "ai"], {"default": "cn"}),
            "timeout": ("INT", {"default": 300, "min": 10, "max": 900, "step": 10}),
            "video_mode": (["sample_frames", "native", "ignore"], {
                "default": "sample_frames", "tooltip": "抽帧可与图片一起分析；native 仅单视频且不混图片。独立 AUDIO 在 RH 模式下不发送。",
            }),
            "video_sample_frames": ("INT", {"default": 8, "min": 1, "max": 32}),
            "request_max_mb": ("INT", {"default": 64, "min": 1, "max": 512}),
        }}

    def build(self, auth_source="rh_cloud", endpoint_region="cn", timeout=300,
              video_mode="sample_frames", video_sample_frames=8, request_max_mb=64):
        return (_normalize_config({
            "provider": "runninghub", "auth_source": auth_source,
            "endpoint_region": endpoint_region, "timeout": timeout,
            "video_mode": video_mode, "video_sample_frames": video_sample_frames,
            "request_max_mb": request_max_mb,
        }),)


class ZFMultimodalLLM:
    DESCRIPTION = (
        "保留旧8图/1视频/1音频接口；新增视频帧批直连和多素材包，自动跳过空素材。"
    )
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("response", "raw_response")
    FUNCTION = "chat"
    CATEGORY = "ZF/API"
    OUTPUT_NODE = True
    API_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (
                    "STRING",
                    {
                        "default": "gpt-4o",
                        "multiline": False,
                        "tooltip": "原样传给 API 的模型名或服务商别名。",
                    },
                ),
                "role": (
                    "STRING",
                    {"multiline": True, "default": "You are a helpful assistant."},
                ),
                "prompt": ("STRING", {"multiline": True, "default": "Hello"}),
                "temperature": (
                    "FLOAT",
                    {"default": 0.6, "min": 0.0, "max": 2.0, "step": 0.1},
                ),
                "max_tokens": (
                    "INT",
                    {"default": 4096, "min": 1, "max": 131072, "step": 1},
                ),
                "top_p": (
                    "FLOAT",
                    {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
                "presence_penalty": (
                    "FLOAT",
                    {"default": 0.0, "min": -2.0, "max": 2.0, "step": 0.1},
                ),
                "frequency_penalty": (
                    "FLOAT",
                    {"default": 0.0, "min": -2.0, "max": 2.0, "step": 0.1},
                ),
                "reasoning_effort": (
                    ["none", "low", "medium", "high"],
                    {"default": "none"},
                ),
                "seed": (
                    "INT",
                    {
                        "default": -1,
                        "min": -1,
                        "max": 2147483647,
                        "step": 1,
                        "control_after_generate": True,
                    },
                ),
            },
            "optional": {
                "skip_error": ("BOOLEAN", {"default": False}),
                "image1": ("IMAGE",),
                "image2": ("IMAGE",),
                "image3": ("IMAGE",),
                "image4": ("IMAGE",),
                "image5": ("IMAGE",),
                "image6": ("IMAGE",),
                "image7": ("IMAGE",),
                "image8": ("IMAGE",),
                "video": ("VIDEO",),
                "api_config": ("ZF_MULTIMODAL_API_CONFIG",),
                "audio": ("AUDIO",),
                "media_bundle": ("ZF_MULTIMODAL_MEDIA_BUNDLE",),
                "video_frames": ("IMAGE",),
                "video_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
            },
        }

    def _error_result(self, error: Exception):
        message = f"[ERROR] ZFMultimodalLLM：{_redact_text(str(error))}"
        raw = json.dumps({"error": _redact_text(str(error))}, ensure_ascii=False, indent=2)
        return {"ui": {"text": [message, raw]}, "result": (message, raw)}

    def chat(
        self,
        model,
        role,
        prompt,
        temperature,
        max_tokens,
        top_p,
        presence_penalty,
        frequency_penalty,
        reasoning_effort,
        seed,
        skip_error=False,
        image1=None,
        image2=None,
        image3=None,
        image4=None,
        image5=None,
        image6=None,
        image7=None,
        image8=None,
        video=None,
        api_config=None,
        audio=None,
        media_bundle=None,
        video_frames=None,
        video_fps=24.0,
    ):
        request_secrets = ()
        try:
            config = _normalize_config(api_config)
            model = (model or "").strip()
            if not model:
                raise ValueError("model 不能为空。")
            processor = _media_processor()
            images = [
                image
                for image in (image1, image2, image3, image4, image5, image6, image7, image8)
                if not processor.is_absent(image)
            ]
            video = None if processor.is_absent(video) else video
            audio = None if processor.is_absent(audio) else audio
            video_frames = None if processor.is_absent(video_frames) else video_frames
            if media_bundle is not None:
                if images or video is not None or video_frames is not None or audio is not None:
                    raise ValueError("media_bundle 与传统媒体输入不能同时使用；请保留素材包或单独接口中的一种。")
                image_media, video_media, audio_media, media_note = processor.prepare_bundle(media_bundle, config, globals())
            elif video_frames is not None:
                if video is not None:
                    raise ValueError("video 与 video_frames 同时有素材；请选择 VIDEO 或帧批中的一种。")
                bundle = processor.legacy_frames_bundle(images, video_frames, video_fps, audio)
                image_media, video_media, audio_media, media_note = processor.prepare_bundle(bundle, config, globals())
            else:
                # Preserve legacy preparation/labels for existing workflows.
                selected_audio = None if config["audio_mode"] == "ignore" else audio
                image_media, video_media, audio_media, media_note = _prepare_media(
                    config["api_protocol"], images, video, config["video_mode"],
                    config["video_max_seconds"], config["video_max_mb"], config["video_sample_frames"],
                    selected_audio, config["audio_max_seconds"], config["audio_max_mb"],
                )
                if audio is not None and config["audio_mode"] == "ignore":
                    media_note += "\n<Audio 1> was explicitly ignored by audio_mode=ignore; it was NOT heard."
            effective_prompt = prompt or ""
            if media_note:
                effective_prompt = f"{effective_prompt}\n\n[Media note: {media_note}]"
            effective_role = role or ""
            if config.get("provider") == "runninghub":
                effective_role += (
                    "\nActual API media boundary: independent AUDIO inputs are not sent in RH mode. "
                    "Do not claim to have heard those tracks. Video samples are visual evidence only; "
                    "routing metadata alone is not evidence of sound, dialogue, lyrics or music. "
                    "A native video's sound is not verified by this adapter; do not claim audio understanding without evidence."
                )

            url, headers, payload = _build_request(
                config,
                model,
                effective_role,
                effective_prompt,
                image_media,
                video_media,
                audio_media,
                temperature,
                max_tokens,
                top_p,
                presence_penalty,
                frequency_penalty,
                reasoning_effort,
                seed,
            )
            request_secrets = _header_secrets(headers)
            payload_bytes = len(json.dumps(payload, ensure_ascii=True).encode("utf-8"))
            if payload_bytes > config["request_max_mb"] * 1024 * 1024:
                raise ValueError(f"JSON请求超过 request_max_mb={config['request_max_mb']}MB；请减少素材或按服务商限制调整。")
            log.info(
                "ZF multimodal API request: protocol=%s model=%s images=%d video=%s audio=%s endpoint=%s",
                config["api_protocol"],
                model,
                len(image_media),
                bool(video_media),
                bool(audio_media),
                url,
            )
            if config.get("provider") == "runninghub":
                data = _post_json(url, headers, payload, config["timeout"], allow_redirects=False)
            else:
                data = _post_json(url, headers, payload, config["timeout"])
            text = _redact_text(_clean_text(_response_text(config["api_protocol"], data)), request_secrets)
            if not text:
                raise RuntimeError("API 返回了空文本。")
            raw = _safe_raw_response(data, request_secrets)
            return {"ui": {"text": [text, raw]}, "result": (text, raw)}
        except Exception as error:
            safe_error = _redact_text(str(error), request_secrets)
            if skip_error:
                log.error("ZFMultimodalLLM failed: %s", safe_error)
                return self._error_result(RuntimeError(safe_error))
            if request_secrets:
                if isinstance(error, ValueError):
                    raise ValueError(safe_error) from None
                raise RuntimeError(safe_error) from None
            raise
