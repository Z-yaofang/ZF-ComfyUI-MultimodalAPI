from .nodes import ZFLocalMultimodalLLM, ZFMultimodalAPIConfig, ZFMultimodalLLM


NODE_CLASS_MAPPINGS = {
    "ZFMultimodalAPIConfig": ZFMultimodalAPIConfig,
    "ZFMultimodalLLM": ZFMultimodalLLM,
    "ZFLocalMultimodalLLM": ZFLocalMultimodalLLM,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ZFMultimodalAPIConfig": "ZF 多模态 API 配置",
    "ZFMultimodalLLM": "ZF 多模态反推（RH 接口兼容）",
    "ZFLocalMultimodalLLM": "ZF 本地多模态反推（llama.cpp）",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
