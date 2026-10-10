"""Runtime media bundles and a bridge to the existing H3 reference outlet."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping


BUNDLE_TYPE = "ZF_MULTIMODAL_MEDIA_BUNDLE"
BUNDLE_SCHEMA = "zf-media-bundle-v1"
H3_PLAN_SCHEMA = "zv-h3-reference-plan-v1"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_TIMES = ("source_in_seconds", "source_out_seconds", "timeline_in_seconds", "timeline_out_seconds")
H3_MEDIA_OUTPUTS = {
    **{f"image{i + 1}": i for i in range(11)},
    **{f"video_frames{i + 1}": 11 + i for i in range(3)},
    **{f"audio{i + 1}": slot for i, slot in enumerate((14, 15, 16, 17, 19, 20, 21))},
}


def _has_media(value, kind):
    if value is None:
        return False
    if kind in ("image", "frames"):
        shape = getattr(value, "shape", None)
        if shape is None or len(shape) not in ((4,) if kind == "frames" else (3, 4)):
            raise ValueError(f"{kind} 输入必须是 ComfyUI 图像张量。")
        return all(int(size) > 0 for size in shape)
    if kind == "audio" and isinstance(value, Mapping):
        waveform = value.get("waveform")
        if waveform is not None:
            shape = getattr(waveform, "shape", None)
            if shape is not None:
                return all(int(size) > 0 for size in shape)
    if kind == "video":
        frame_count = getattr(value, "get_frame_count", None)
        if callable(frame_count):
            return frame_count() > 0
    return True


def _parse_plan(value):
    try:
        plan = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("reference_plan_json 不是有效 JSON，请直接连接 H3 素材对齐出口。") from exc
    if not isinstance(plan, dict) or plan.get("schema_version") != H3_PLAN_SCHEMA:
        raise ValueError("H3 素材计划版本不受支持，请重新检测并对齐素材。")
    if plan.get("ready") is not True:
        raise ValueError("H3 素材计划未就绪，请重新检测并对齐素材。")
    if not isinstance(plan.get("routes"), dict) or not isinstance(plan.get("call_references"), list):
        raise ValueError("H3 素材计划缺少 routes 或 call_references。")
    return plan


def _h3_slots(plan):
    routes = plan["routes"]

    def route(name, maximum=None):
        if name not in routes:
            raise ValueError(f"H3 素材计划缺少 routes.{name}。")
        value = routes.get(name)
        if maximum is None:
            items = [value]
        else:
            if not isinstance(value, list) or len(value) > maximum:
                raise ValueError(f"H3 routes.{name} 必须是最多 {maximum} 项的列表。")
            items = value
        if any(item is not None and (not isinstance(item, str) or not _ID.fullmatch(item)) for item in items):
            raise ValueError(f"H3 routes.{name} 包含无效素材 ID。")
        if maximum is not None and any(item is None for item in items):
            raise ValueError(f"H3 routes.{name} 列表不可包含空素材 ID。")
        return value

    first, last = route("first_frame"), route("last_frame")
    images = route("ref_images", 9)
    videos = route("ref_videos", 3)
    soundtracks = route("ref_video_audios", 3)
    drive = route("drive_audio")
    audios = route("ref_audios", 3)
    if len(set(videos)) != len(videos) or len(set(soundtracks)) != len(soundtracks):
        raise ValueError("H3 视频或视频原声路由不可重复。")
    if any(item not in videos for item in soundtracks):
        raise ValueError("H3 视频原声必须属于本次参考视频。")

    slots = {"picture": [], "video": [], "audio": []}
    slots["picture"] = [
        ("image1", first, "first_frame", "first_frame"),
        ("image2", last, "last_frame", "last_frame"),
        *[(f"image{i + 3}", images[i] if i < len(images) else None, "reference", f"ref_image_{i + 1}") for i in range(9)],
    ]
    slots["video"] = [(f"video{i + 1}", videos[i] if i < len(videos) else None, "reference", f"ref_video_{i + 1}") for i in range(3)]
    slots["audio"] = [
        *[(f"audio{i + 1}", videos[i] if i < len(videos) and videos[i] in soundtracks else None,
           "video_soundtrack", f"ref_video_audio_{i + 1}") for i in range(3)],
        ("audio4", drive, "drive_audio", "drive_audio"),
        *[(f"audio{i + 5}", audios[i] if i < len(audios) else None, "standalone", f"ref_audio_{i + 1}") for i in range(3)],
    ]
    return slots


def _h3_manifest(plan, by_port):
    slots = _h3_slots(plan)
    calls = plan["call_references"]
    if any(not isinstance(row, dict) or row.get("kind") not in slots for row in calls):
        raise ValueError("H3 call_references 包含无效素材类型。")
    manifest = []
    for kind, label_kind in (("picture", "Picture"), ("video", "Video"), ("audio", "Audio")):
        expected = []
        for port, item_id, origin, route in slots[kind]:
            present = port in by_port
            if present != (item_id is not None):
                raise ValueError(f"H3 素材 {port} 与对齐计划不一致：" + ("缺少有效输入。" if item_id is not None else "计划为空但收到素材。"))
            if item_id is not None:
                expected.append((port, item_id, origin, route))
        rows = [row for row in calls if row["kind"] == kind]
        if len(rows) != len(expected):
            raise ValueError(f"H3 {label_kind} 调用数量与固定出口不一致，请重新对齐。")
        for ordinal, (row, (port, item_id, origin, route)) in enumerate(zip(rows, expected), 1):
            label = f"<{label_kind} {ordinal}>"
            if (row.get("call_label") != label or type(row.get("call_ordinal")) is not int
                    or row["call_ordinal"] != ordinal or row.get("item_id") != item_id or row.get("origin") != origin):
                raise ValueError(f"H3 {label} 的编号或路由与固定出口不一致，请重新对齐。")
            by_port[port]["label"] = label
            entry = {"kind": kind, "call_label": label, "call_ordinal": ordinal,
                     "item_id": item_id, "origin": origin, "route": route, "input_name": port}
            for key in _TIMES:
                value = row.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    entry[key] = value
            manifest.append(entry)
    return manifest


class ZFMultimodalMediaPack:
    DESCRIPTION = "整理全部多模态参考为一个运行时素材包，空输入自动跳过；H3 模式校验真实编号与出口。"
    RETURN_TYPES = (BUNDLE_TYPE,)
    RETURN_NAMES = ("media_bundle",)
    FUNCTION = "pack"
    CATEGORY = "ZF/API"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"video_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0})},
            "optional": {
                **{f"image{i}": ("IMAGE",) for i in range(1, 12)},
                **{f"video{i}": ("VIDEO",) for i in range(1, 4)},
                **{f"video_frames{i}": ("IMAGE",) for i in range(1, 4)},
                **{f"audio{i}": ("AUDIO",) for i in range(1, 8)},
                "reference_plan_json": ("STRING", {"forceInput": True}),
            },
        }

    def pack(self, video_fps=24.0, reference_plan_json=None, **kwargs):
        if isinstance(video_fps, bool) or not isinstance(video_fps, (int, float)) or not math.isfinite(video_fps) or not 1 <= video_fps <= 120:
            raise ValueError("video_fps 必须是 1–120 之间的有限正数。")
        images, videos, audios, by_port = [], [], [], {}
        for index in range(1, 12):
            port = f"image{index}"
            value = kwargs.get(port)
            if _has_media(value, "image"):
                row = {"label": f"<Picture {len(images) + 1}>", "value": value}
                images.append(row)
                by_port[port] = row
        for index in range(1, 4):
            port = f"video{index}"
            native, frames = kwargs.get(port), kwargs.get(f"video_frames{index}")
            has_native, has_frames = _has_media(native, "video"), _has_media(frames, "frames")
            if has_native and has_frames:
                raise ValueError(f"{port} 与 video_frames{index} 不可同时输入素材。")
            if has_native or has_frames:
                row = {"label": f"<Video {len(videos) + 1}>", "value": native if has_native else frames,
                       "format": "video" if has_native else "frames", "fps": float(video_fps)}
                videos.append(row)
                by_port[port] = row
        for index in range(1, 8):
            port = f"audio{index}"
            value = kwargs.get(port)
            if _has_media(value, "audio"):
                row = {"label": f"<Audio {len(audios) + 1}>", "value": value}
                audios.append(row)
                by_port[port] = row
        manifest = {"source": "manual", "counts": {"images": len(images), "videos": len(videos), "audios": len(audios)}}
        if reference_plan_json is not None:
            plan = _parse_plan(reference_plan_json)
            manifest.update(source="h3_reference_outlet", reference_plan_schema=H3_PLAN_SCHEMA,
                            call_references=_h3_manifest(plan, by_port))
        else:
            manifest["call_references"] = [{"input_name": port, "call_label": row["label"]} for port, row in by_port.items()]
        return ({"schema_version": BUNDLE_SCHEMA, "images": images, "videos": videos,
                 "audios": audios, "manifest": manifest},)


class ZFH3MediaBundle:
    DESCRIPTION = "直接连接 H3 素材出口的 reference_plan_json，一线收集完整参考；复用已解码输出，不重复读取素材。"
    RETURN_TYPES = (BUNDLE_TYPE,)
    RETURN_NAMES = ("media_bundle",)
    FUNCTION = "collect"
    CATEGORY = "ZF/API"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"reference_plan_json": ("STRING", {"forceInput": True})},
                "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}}

    def collect(self, reference_plan_json, prompt=None, unique_id=None):
        plan = _parse_plan(reference_plan_json)
        _h3_slots(plan)
        if not isinstance(prompt, Mapping) or str(unique_id) not in prompt:
            raise ValueError("H3 素材桥接需要 ComfyUI 执行图，请直接连接 H3 素材出口。")
        link = prompt[str(unique_id)].get("inputs", {}).get("reference_plan_json")
        if (not isinstance(link, list) or len(link) != 2 or not isinstance(link[0], str)
                or type(link[1]) is not int or link[1] != 22):
            raise ValueError("请将 H3 素材出口的 reference_plan_json（第 23 个输出）直接连接到素材桥接。")
        hub_id = link[0]
        if prompt.get(hub_id, {}).get("class_type") != "ZVH3ReferenceOutlet":
            raise ValueError("素材桥接仅接受 ZVH3ReferenceOutlet 的 reference_plan_json 输出。")

        # ComfyUI is optional for ordinary API use outside the graph executor.
        from comfy_execution.graph_utils import GraphBuilder

        graph = GraphBuilder()
        packed = graph.node("ZFMultimodalMediaPack", video_fps=24.0,
                            reference_plan_json=[hub_id, 22],
                            **{name: [hub_id, slot] for name, slot in H3_MEDIA_OUTPUTS.items()})
        packed.set_override_display_id(str(unique_id))
        return {"result": (packed.out(0),), "expand": graph.finalize()}
