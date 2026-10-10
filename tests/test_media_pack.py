import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("zf_media_pack", Path(__file__).resolve().parents[1] / "media_pack.py")
pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack)


class Tensor:
    def __init__(self, *shape):
        self.shape = shape

    def __bool__(self):
        raise AssertionError("A tensor must not be converted to bool")


class Video:
    def __init__(self, frames=1):
        self.frames = frames

    def get_frame_count(self):
        return self.frames


def plan_for(first=None, last=None, images=(), videos=(), soundtracks=(), drive=None, audios=()):
    plan = {"schema_version": pack.H3_PLAN_SCHEMA, "ready": True, "routes": {
        "first_frame": first, "last_frame": last, "ref_images": list(images), "ref_videos": list(videos),
        "ref_video_audios": list(soundtracks), "drive_audio": drive, "ref_audios": list(audios)}, "call_references": []}
    entries = {
        "picture": [(first, "first_frame"), (last, "last_frame"), *[(x, "reference") for x in images]],
        "video": [(x, "reference") for x in videos],
        "audio": [*[(x, "video_soundtrack") for x in videos if x in soundtracks], (drive, "drive_audio"), *[(x, "standalone") for x in audios]],
    }
    for kind, rows in entries.items():
        for ordinal, (item, origin) in enumerate((row for row in rows if row[0] is not None), 1):
            plan["call_references"].append({"kind": kind, "item_id": item, "origin": origin,
                "call_label": f"<{kind.title()} {ordinal}>", "call_ordinal": ordinal})
    return plan


class MediaPackTests(unittest.TestCase):
    def test_all_empty_and_zero_length_inputs_are_skipped(self):
        bundle, = pack.ZFMultimodalMediaPack().pack(image1=None, image2=Tensor(0, 8, 8, 3),
            video1=Video(0), video_frames2=Tensor(0, 8, 8, 3), audio1={"waveform": Tensor(1, 2, 0), "sample_rate": 24000})
        self.assertEqual(bundle["manifest"]["counts"], {"images": 0, "videos": 0, "audios": 0})

    def test_generic_labels_compact_without_copying_media(self):
        picture, video, audio = Tensor(1, 8, 8, 3), Tensor(20, 8, 8, 3), {"waveform": Tensor(1, 2, 48000)}
        bundle, = pack.ZFMultimodalMediaPack().pack(image11=picture, video_frames3=video, audio7=audio)
        self.assertEqual(bundle["images"][0]["label"], "<Picture 1>")
        self.assertEqual(bundle["videos"][0]["label"], "<Video 1>")
        self.assertEqual(bundle["audios"][0]["label"], "<Audio 1>")
        self.assertIs(bundle["images"][0]["value"], picture)
        self.assertIs(bundle["videos"][0]["value"], video)
        self.assertIs(bundle["audios"][0]["value"], audio)
        json.dumps(bundle["manifest"], allow_nan=False)

    def test_same_video_slot_cannot_have_two_sources(self):
        with self.assertRaisesRegex(ValueError, "不可同时"):
            pack.ZFMultimodalMediaPack().pack(video1=Video(), video_frames1=Tensor(2, 8, 8, 3))

    def test_empty_native_video_does_not_conflict_with_frames(self):
        bundle, = pack.ZFMultimodalMediaPack().pack(video1=Video(0), video_frames1=Tensor(2, 8, 8, 3))
        self.assertEqual(bundle["videos"][0]["format"], "frames")

    def test_full_h3_capacity_and_seven_distinct_audio_slots(self):
        plan = plan_for("first", "last", [f"p{i}" for i in range(9)], ["v1", "v2", "v3"], ["v1", "v2", "v3"], "drive", ["a1", "a2", "a3"])
        inputs = {**{f"image{i}": Tensor(1, 8, 8, 3) for i in range(1, 12)},
                  **{f"video_frames{i}": Tensor(48, 8, 8, 3) for i in range(1, 4)},
                  **{f"audio{i}": {"waveform": Tensor(1, 2, 48000)} for i in range(1, 8)}}
        bundle, = pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(plan), **inputs)
        self.assertEqual(bundle["manifest"]["counts"], {"images": 11, "videos": 3, "audios": 7})
        self.assertEqual(bundle["audios"][-1]["label"], "<Audio 7>")
        self.assertEqual([row["origin"] for row in bundle["manifest"]["call_references"] if row["kind"] == "audio"],
                         ["video_soundtrack"] * 3 + ["drive_audio"] + ["standalone"] * 3)

    def test_missing_anchors_compact_and_duplicate_image_ids_remain(self):
        plan = plan_for(last="shared", images=["shared"])
        image = Tensor(1, 8, 8, 3)
        bundle, = pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(plan), image2=image, image3=image)
        self.assertEqual([row["label"] for row in bundle["images"]], ["<Picture 1>", "<Picture 2>"])
        self.assertEqual([row["item_id"] for row in bundle["manifest"]["call_references"]], ["shared", "shared"])

    def test_audio_labels_skip_absent_video_soundtracks(self):
        plan = plan_for(videos=["v1", "v2", "v3"], soundtracks=["v2"], drive="voice", audios=["a1"])
        bundle, = pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(plan),
            **{f"video_frames{i}": Tensor(48, 8, 8, 3) for i in range(1, 4)},
            **{f"audio{i}": {"waveform": Tensor(1, 1, 48000)} for i in (2, 4, 5)})
        rows = [row for row in bundle["manifest"]["call_references"] if row["kind"] == "audio"]
        self.assertEqual([(row["call_label"], row["input_name"]) for row in rows], [("<Audio 1>", "audio2"), ("<Audio 2>", "audio4"), ("<Audio 3>", "audio5")])

    def test_h3_rejects_missing_extra_or_zero_length_media(self):
        plan = json.dumps(plan_for(first="p1"))
        for inputs in ({}, {"image1": Tensor(0, 8, 8, 3)}, {"image2": Tensor(1, 8, 8, 3)}):
            with self.subTest(inputs=list(inputs)), self.assertRaisesRegex(ValueError, "不一致"):
                pack.ZFMultimodalMediaPack().pack(reference_plan_json=plan, **inputs)
        with self.assertRaisesRegex(ValueError, "计划为空"):
            pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(plan_for()), image1=Tensor(1, 8, 8, 3))

    def test_h3_rejects_missing_wrong_and_reordered_labels(self):
        baseline = plan_for("p1", "p2")
        for field, value in (("call_label", "<Picture 2>"), ("call_ordinal", 2), ("item_id", "p2"), ("origin", "reference")):
            plan = copy.deepcopy(baseline)
            plan["call_references"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "编号或路由"):
                pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(plan), image1=Tensor(1, 8, 8, 3), image2=Tensor(1, 8, 8, 3))
        baseline["call_references"].pop()
        with self.assertRaisesRegex(ValueError, "数量"):
            pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(baseline), image1=Tensor(1, 8, 8, 3), image2=Tensor(1, 8, 8, 3))

    def test_manifest_excludes_paths_arbitrary_text_and_tensors(self):
        plan = plan_for("p1")
        plan["decoded_manifests"] = {"path": "C:\\private\\photo.png"}
        plan["call_references"][0].update(name="C:\\private\\photo.png", source_handle="private", purpose="/home/private", source_in_seconds=2.5)
        bundle, = pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(plan), image1=Tensor(1, 8, 8, 3))
        encoded = json.dumps(bundle["manifest"], allow_nan=False)
        self.assertNotIn("private", encoded)
        self.assertIn("2.5", encoded)

    def test_h3_plan_errors_and_invalid_fps(self):
        for value in ("", "null", "{}", "not json", json.dumps(plan_for() | {"ready": False})):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pack.ZFMultimodalMediaPack().pack(reference_plan_json=value)
        for fps in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                pack.ZFMultimodalMediaPack().pack(video_fps=fps)

    def test_h3_missing_route_is_not_treated_as_an_empty_slot(self):
        plan = plan_for()
        del plan["routes"]["first_frame"]
        with self.assertRaisesRegex(ValueError, "缺少 routes.first_frame"):
            pack.ZFMultimodalMediaPack().pack(reference_plan_json=json.dumps(plan))


class FakeGraphBuilder:
    def __init__(self):
        self.graph = {}

    def node(self, kind, **inputs):
        row = {"class_type": kind, "inputs": inputs}
        self.graph["bridge.0.0.1"] = row
        return types.SimpleNamespace(out=lambda slot: ["bridge.0.0.1", slot],
                                     set_override_display_id=lambda value: row.update(override_display_id=value))

    def finalize(self):
        return self.graph


class H3BridgeTests(unittest.TestCase):
    def graph(self, source="hub", slot=22, kind="ZVH3ReferenceOutlet"):
        return {"hub": {"class_type": kind, "inputs": {}},
                "bridge": {"class_type": "ZFH3MediaBundle", "inputs": {"reference_plan_json": [source, slot]}}}

    def test_expansion_links_existing_cached_outputs_without_decoder(self):
        graph_module = types.ModuleType("comfy_execution.graph_utils")
        graph_module.GraphBuilder = FakeGraphBuilder
        with patch.dict(sys.modules, {"comfy_execution.graph_utils": graph_module}):
            result = pack.ZFH3MediaBundle().collect(json.dumps(plan_for()), prompt=self.graph(), unique_id="bridge")
        self.assertEqual(len(result["expand"]), 1)
        node = next(iter(result["expand"].values()))
        self.assertEqual(node["class_type"], "ZFMultimodalMediaPack")
        self.assertEqual(node["inputs"]["reference_plan_json"], ["hub", 22])
        self.assertEqual(node["inputs"]["audio4"], ["hub", 17])
        self.assertEqual(node["inputs"]["audio5"], ["hub", 19])
        self.assertNotIn(["hub", 18], node["inputs"].values())
        # Resolve against the already computed outlet, just as execution.get_input_data does.
        cached_outputs = [None] * 24
        cached_outputs[22] = json.dumps(plan_for())
        resolved = {key: cached_outputs[value[1]] if isinstance(value, list) else value for key, value in node["inputs"].items()}
        bundle, = pack.ZFMultimodalMediaPack().pack(**resolved)
        self.assertEqual(bundle["manifest"]["counts"], {"images": 0, "videos": 0, "audios": 0})
        self.assertTrue(all(value[0] == "hub" for value in node["inputs"].values() if isinstance(value, list)))
        self.assertNotIn("ZVH3ReferenceOutlet", [n["class_type"] for n in result["expand"].values()])

    def test_bridge_rejects_indirect_wrong_and_missing_source(self):
        for prompt in (None, {}, self.graph(slot=23), self.graph(source="missing"), self.graph(kind="OtherNode")):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                pack.ZFH3MediaBundle().collect(json.dumps(plan_for()), prompt=prompt, unique_id="bridge")

    def test_installed_graph_builder_keeps_external_hub_links(self):
        path = Path(__file__).resolve().parents[3] / "comfy_execution" / "graph_utils.py"
        if not path.is_file():
            self.skipTest("ComfyUI is not installed beside this standalone plugin checkout")
        spec = importlib.util.spec_from_file_location("zf_test_graph_utils", path)
        graph_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(graph_module)
        graph_module.GraphBuilder.set_default_prefix("bridge", 0)
        with patch.dict(sys.modules, {"comfy_execution.graph_utils": graph_module}):
            result = pack.ZFH3MediaBundle().collect(json.dumps(plan_for()), prompt=self.graph(), unique_id="bridge")
        self.assertIn(result["result"][0][0], result["expand"])
        node = next(iter(result["expand"].values()))
        self.assertEqual(node["inputs"]["reference_plan_json"], ["hub", 22])
        self.assertEqual(node["inputs"]["image11"], ["hub", 10])
        self.assertEqual(node["inputs"]["video_frames3"], ["hub", 13])
        self.assertEqual(node["inputs"]["audio7"], ["hub", 21])


if __name__ == "__main__":
    unittest.main()
