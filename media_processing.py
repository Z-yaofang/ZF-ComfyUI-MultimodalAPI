"""Media-bundle preparation. Tensor video frames need no UI conversion nodes."""
from __future__ import annotations

import json
import math
import tempfile
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

from PIL import Image


def is_absent(value):
    """Empty batches are absent; black frames and silent audio are real media."""
    if value is None:
        return True
    if isinstance(value, dict) and "waveform" in value:
        wave = value["waveform"]
        return wave is None or (hasattr(wave, "shape") and any(int(n) == 0 for n in wave.shape))
    shape = getattr(value, "shape", None)
    return shape is not None and len(shape) > 0 and int(shape[0]) == 0


def _frame_info(frames, fps, max_seconds):
    if getattr(frames, "ndim", None) != 4 or frames.shape[-1] not in (1, 3, 4):
        raise ValueError("video_frames 必须是 [帧数, 高, 宽, 通道] IMAGE 帧批。")
    if min(int(v) for v in frames.shape) < 1:
        raise ValueError("video_frames 包含空尺寸。")
    fps = float(fps)
    if not math.isfinite(fps) or not 1 <= fps <= 120:
        raise ValueError("video_fps 必须是 1–120 之间的有限数值。")
    count = min(int(frames.shape[0]), max(1, int(math.floor(max_seconds * fps))))
    return count, fps


def _resize(pil, longest_side, even=False):
    w, h = pil.size
    scale = min(1.0, longest_side / max(w, h))
    width, height = max(1, round(w * scale)), max(1, round(h * scale))
    if even:
        width, height = max(2, width // 2 * 2), max(2, height // 2 * 2)
    if (width, height) != pil.size:
        pil = pil.resize((width, height), Image.Resampling.LANCZOS)
    return pil.convert("RGB")


def sample_frames(frames, fps, max_seconds, sample_count, label, helpers):
    count, fps = _frame_info(frames, fps, max_seconds)
    total = min(count, sample_count)
    indices = [round(i * (count - 1) / (total - 1)) for i in range(total)] if total > 1 else [0]
    media = []
    for ordinal, index in enumerate(indices, 1):
        pil = next(helpers["_iter_pil_images"](frames[index:index + 1]))
        frame_label = f"{label[:-1]}, chronological frame {ordinal}/{total} at {index / fps:.3f} seconds>"
        media.append(helpers["_jpeg_media"](_resize(pil, 1024), frame_label))
    return media


@contextmanager
def encoded_frames(frames, fps, max_seconds, helpers):
    """Encode a video-only stream; bundle Audio labels are sent separately."""
    count, fps = _frame_info(frames, fps, max_seconds)
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("帧批的 native 模式需要 PyAV；请安装 av 或改为 sample_frames。") from exc
    handle = tempfile.NamedTemporaryFile(prefix="zf_api_video_", suffix=".mp4", delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        with av.open(str(path), "w") as container:
            first = next(helpers["_iter_pil_images"](frames[:1]))
            first = _resize(first, 1280, even=True)
            stream = container.add_stream("libx264", rate=Fraction(str(fps)).limit_denominator(100000))
            stream.width, stream.height = first.size
            stream.pix_fmt = "yuv420p"
            stream.options = {"crf": "28", "preset": "veryfast"}
            for index in range(count):
                pil = first if index == 0 else _resize(next(helpers["_iter_pil_images"](frames[index:index + 1])), 1280, even=True)
                for packet in stream.encode(av.VideoFrame.from_image(pil)):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        yield path
    finally:
        path.unlink(missing_ok=True)


def _validate_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.get("schema_version") != "zf-media-bundle-v1":
        raise ValueError("media_bundle 不是有效的 ZF 素材包；请连接素材打包节点。")
    for name, prefix, maximum in (("images", "Picture", 11), ("videos", "Video", 3), ("audios", "Audio", 7)):
        rows = bundle.get(name)
        if not isinstance(rows, list) or len(rows) > maximum:
            raise ValueError(f"素材包 {name} 必须是列表，最多 {maximum} 项。")
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict) or row.get("label") != f"<{prefix} {index}>":
                raise ValueError(f"素材包 {name} 编号必须连续且与实际素材顺序一致。")
            if is_absent(row.get("value")):
                raise ValueError(f"{row['label']} 声明了素材但内容为空，请重新打包。")


def prepare_bundle(bundle, config, helpers):
    _validate_bundle(bundle)
    images, videos, audios, notes = [], [], [], []
    mode = helpers["_resolved_video_mode"](config["api_protocol"], config["video_mode"])
    if bundle["audios"] and config["audio_mode"] == "native" and config["api_protocol"] == "anthropic_messages":
        raise ValueError("Anthropic Messages 不支持 AUDIO；请选择支持音频的接口，或显式设置 audio_mode=ignore（不听取音频）。")
    if bundle["videos"] and mode == "native" and config["api_protocol"] == "anthropic_messages":
        raise ValueError("Anthropic Messages 不支持原生视频；请使用 sample_frames 或 auto。")

    def budget():
        size = sum(len(row.get("data", "")) for row in (*images, *videos, *audios))
        if size > config["request_max_mb"] * 1024 * 1024:
            raise ValueError(f"素材包内联数据超过 request_max_mb={config['request_max_mb']}MB；请减少素材、抽帧数或压缩大小。")

    for row in bundle["images"]:
        encoded = helpers["_collect_image_media"]([row["value"]])
        for item in encoded:
            item["label"] = item["label"].replace("<Picture 1", row["label"][:-1], 1)
        images.extend(encoded)
        budget()
    for row in bundle["videos"]:
        label, value, kind = row["label"], row["value"], row.get("format")
        if kind not in ("frames", "video"):
            raise ValueError(f"{label} 的 format 必须是 frames 或 video。")
        if mode == "ignore":
            notes.append(f"{label} was explicitly ignored by video_mode=ignore; it was NOT inspected.")
            continue
        if mode == "sample_frames":
            if kind == "frames":
                sampled = sample_frames(value, row.get("fps", 24), config["video_max_seconds"], config["video_sample_frames"], label, helpers)
            else:
                sampled = helpers["_sample_video_media"](value, config["video_max_seconds"], config["video_sample_frames"])
                for item in sampled:
                    item["label"] = item["label"].replace("<Video 1", label[:-1], 1)
            images.extend(sampled)
            notes.append(f"{label}: chronological visual samples only, up to {config['video_max_seconds']} seconds; these images contain NO sound.")
        else:
            if kind == "frames":
                with encoded_frames(value, row.get("fps", 24), config["video_max_seconds"], helpers) as path:
                    item = helpers["_native_video_media"](path, config["video_max_seconds"], config["video_max_mb"])
                notes.append(f"{label}: video encoded from frames with no embedded audio; separately labelled Audio inputs carry any supplied sound.")
            else:
                item = helpers["_native_video_media"](value, config["video_max_seconds"], config["video_max_mb"])
            item["label"] = label
            videos.append(item)
        budget()
    for row in bundle["audios"]:
        if config["audio_mode"] == "ignore":
            notes.append(f"{row['label']} was explicitly ignored by audio_mode=ignore; its sound, dialogue, lyrics and music were NOT heard.")
            continue
        item = helpers["_audio_media"](row["value"], config["audio_max_seconds"], config["audio_max_mb"])
        item["label"] = row["label"]
        audios.append(item)
        budget()
    if audios:
        notes.append(f"Separately supplied audio labels: {', '.join(item['label'] for item in audios)}. Each is limited to its first {config['audio_max_seconds']} seconds. Audio numbers do not imply a pairing with same-numbered videos.")
    notes.append("Metadata is a routing manifest, not evidence that an ignored or unprovided asset was inspected.")
    manifest = bundle.get("manifest", {})
    notes.append("Material routing: " + json.dumps(manifest, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return images, videos, audios, "\n".join(notes)


def legacy_frames_bundle(images, frames, fps, audio):
    return {
        "schema_version": "zf-media-bundle-v1",
        "images": [{"label": f"<Picture {i}>", "value": value} for i, value in enumerate(images, 1)],
        "videos": [] if is_absent(frames) else [{"label": "<Video 1>", "value": frames, "format": "frames", "fps": fps}],
        "audios": [] if is_absent(audio) else [{"label": "<Audio 1>", "value": audio}],
        "manifest": {"source": "direct_inputs", "video_fps": fps},
    }
