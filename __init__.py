from .nodes import ZFMultimodalAPIConfig, ZFMultimodalLLM, ZFRunningHubAPIConfig
from .media_pack import ZFMultimodalMediaPack, ZFH3MediaBundle


NODE_CLASS_MAPPINGS = {
    "ZFMultimodalAPIConfig": ZFMultimodalAPIConfig,
    "ZFRunningHubAPIConfig": ZFRunningHubAPIConfig,
    "ZFMultimodalLLM": ZFMultimodalLLM,
    "ZFMultimodalMediaPack": ZFMultimodalMediaPack,
    "ZFH3MediaBundle": ZFH3MediaBundle,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ZFMultimodalAPIConfig": "ZF 多模态 API 配置",
    "ZFRunningHubAPIConfig": "ZF RH 云端 API 配置",
    "ZFMultimodalLLM": "ZF 多模态反推（RH 接口兼容）",
    "ZFMultimodalMediaPack": "ZF 多模态素材打包",
    "ZFH3MediaBundle": "ZF H3 素材包（一线连接）",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
