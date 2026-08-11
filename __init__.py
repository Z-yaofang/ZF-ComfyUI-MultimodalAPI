from .nodes import ZFMultimodalAPIConfig, ZFMultimodalLLM


NODE_CLASS_MAPPINGS = {
    "ZFMultimodalAPIConfig": ZFMultimodalAPIConfig,
    "ZFMultimodalLLM": ZFMultimodalLLM,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ZFMultimodalAPIConfig": "ZF 多模态 API 配置",
    "ZFMultimodalLLM": "ZF 多模态反推（RH 接口兼容）",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
