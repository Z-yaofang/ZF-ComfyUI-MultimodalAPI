import argparse
import json
from pathlib import Path


PLUGIN_ID = "Z-yaofang/ZF-ComfyUI-MultimodalAPI"
PLUGIN_VERSION = "0.1.0"


def config_inputs():
    names_and_types = [
        ("api_protocol", "COMBO"),
        ("api_base_url", "STRING"),
        ("api_key_file", "STRING"),
        ("timeout", "INT"),
        ("parameter_mode", "COMBO"),
        ("video_mode", "COMBO"),
        ("video_max_seconds", "INT"),
        ("video_max_mb", "INT"),
        ("video_sample_frames", "INT"),
        ("anthropic_version", "STRING"),
    ]
    return [
        {
            "name": name,
            "type": input_type,
            "link": None,
            "widget": {"name": name},
        }
        for name, input_type in names_and_types
    ]


def migrate_workflow(data):
    chat_nodes = [node for node in data.get("nodes", []) if node.get("type") == "RHLLMChatNode"]
    config_nodes = [node for node in data.get("nodes", []) if node.get("type") == "RHSettingsNode"]
    if not chat_nodes:
        raise ValueError("工作流内没有 RHLLMChatNode。")
    if not config_nodes:
        raise ValueError("工作流内没有 RHSettingsNode。")

    config_ids = {node["id"] for node in config_nodes}
    chat_ids = {node["id"] for node in chat_nodes}

    for node in config_nodes:
        node["type"] = "ZFMultimodalAPIConfig"
        node["size"] = [410, 330]
        node["inputs"] = config_inputs()
        node["outputs"] = [
            {
                "name": "api_config",
                "type": "ZF_MULTIMODAL_API_CONFIG",
                "links": node.get("outputs", [{}])[0].get("links") or [],
            }
        ]
        node["widgets_values"] = [
            "openai_chat_completions",
            "https://api.openai.com/v1",
            "api_key.txt",
            300,
            "auto",
            "auto",
            15,
            10,
            8,
            "2023-06-01",
        ]
        properties = node.setdefault("properties", {})
        properties["aux_id"] = PLUGIN_ID
        properties["ver"] = PLUGIN_VERSION
        properties["Node name for S&R"] = "ZFMultimodalAPIConfig"
        properties.pop("cnr_id", None)

    for node in chat_nodes:
        node["type"] = "ZFMultimodalLLM"
        node["size"] = [430, max(590, node.get("size", [430, 590])[1])]
        for input_data in node.get("inputs", []):
            if input_data.get("name") == "api_config":
                input_data["type"] = "ZF_MULTIMODAL_API_CONFIG"
            elif input_data.get("name") == "model":
                input_data["type"] = "STRING"
        properties = node.setdefault("properties", {})
        properties["aux_id"] = PLUGIN_ID
        properties["ver"] = PLUGIN_VERSION
        properties["Node name for S&R"] = "ZFMultimodalLLM"
        properties.pop("cnr_id", None)

    for link in data.get("links", []):
        if len(link) < 6:
            continue
        if link[1] in config_ids and link[3] in chat_ids:
            link[5] = "ZF_MULTIMODAL_API_CONFIG"

    return len(chat_nodes), len(config_nodes)


def main():
    parser = argparse.ArgumentParser(
        description="Copy an RH LLM workflow and replace only its LLM/config nodes with ZF nodes."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source = args.source.resolve()
    destination = args.destination.resolve()
    if source == destination:
        raise ValueError("目标文件必须与原工作流不同，迁移脚本不会覆盖原文件。")
    if destination.exists():
        raise FileExistsError(f"目标文件已经存在：{destination}")

    with source.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    chat_count, config_count = migrate_workflow(data)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
    print(
        f"Created {destination} with {chat_count} chat node(s) and "
        f"{config_count} config node(s) migrated."
    )


if __name__ == "__main__":
    main()
