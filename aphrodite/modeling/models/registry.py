import importlib
import string
import subprocess
import sys
import uuid
from functools import lru_cache, partial
from typing import Callable, Dict, List, Optional, Tuple, Type, Union

import torch.nn as nn
from loguru import logger

from aphrodite.common.utils import is_hip

from .interfaces import supports_multimodal, supports_pp

_GENERATION_MODELS = {
    "AquilaModel": ("llama", "LlamaForCausalLM"),
    "AquilaForCausalLM": ("llama", "LlamaForCausalLM"),  # AquilaChat2
    "BaiChuanForCausalLM": ("baichuan", "BaiChuanForCausalLM"),  # baichuan-7b
    "BaichuanForCausalLM": ("baichuan", "BaichuanForCausalLM"),  # baichuan-13b
    "BloomForCausalLM": ("bloom", "BloomForCausalLM"),
    "ChatGLMModel": ("chatglm", "ChatGLMForCausalLM"),
    "ChatGLMForConditionalGeneration": ("chatglm", "ChatGLMForCausalLM"),
    "CohereForCausalLM": ("commandr", "CohereForCausalLM"),
    "DbrxForCausalLM": ("dbrx", "DbrxForCausalLM"),
    "DeciLMForCausalLM": ("decilm", "DeciLMForCausalLM"),
    "DeepseekForCausalLM": ("deepseek", "DeepseekForCausalLM"),
    "DeepseekV2ForCausalLM": ("deepseek_v2", "DeepseekV2ForCausalLM"),
    "FalconForCausalLM": ("falcon", "FalconForCausalLM"),
    "GemmaForCausalLM": ("gemma", "GemmaForCausalLM"),
    "Gemma2ForCausalLM": ("gemma2", "Gemma2ForCausalLM"),
    "GPT2LMHeadModel": ("gpt2", "GPT2LMHeadModel"),
    "GPTBigCodeForCausalLM": ("gpt_bigcode", "GPTBigCodeForCausalLM"),
    "GPTJForCausalLM": ("gpt_j", "GPTJForCausalLM"),
    "GPTNeoXForCausalLM": ("gpt_neox", "GPTNeoXForCausalLM"),
    "InternLMForCausalLM": ("llama", "LlamaForCausalLM"),
    "InternLM2ForCausalLM": ("internlm2", "InternLM2ForCausalLM"),
    "JAISLMHeadModel": ("jais", "JAISLMHeadModel"),
    "LlamaForCausalLM": ("llama", "LlamaForCausalLM"),
    # For decapoda-research/llama-*
    "LLaMAForCausalLM": ("llama", "LlamaForCausalLM"),
    "MistralForCausalLM": ("llama", "LlamaForCausalLM"),
    "MixtralForCausalLM": ("mixtral", "MixtralForCausalLM"),
    "QuantMixtralForCausalLM": ("mixtral_quant", "MixtralForCausalLM"),
    # transformers's mpt class has lower case
    "MptForCausalLM": ("mpt", "MPTForCausalLM"),
    "MPTForCausalLM": ("mpt", "MPTForCausalLM"),
    "MiniCPMForCausalLM": ("minicpm", "MiniCPMForCausalLM"),
    "MiniCPM3ForCausalLM": ("minicpm3", "MiniCPM3ForCausalLM"),
    "NemotronForCausalLM": ("nemotron", "NemotronForCausalLM"),
    "OlmoForCausalLM": ("olmo", "OlmoForCausalLM"),
    "OlmoeForCausalLM": ("olmoe", "OlmoeForCausalLM"),
    "OPTForCausalLM": ("opt", "OPTForCausalLM"),
    "OrionForCausalLM": ("orion", "OrionForCausalLM"),
    "PhiForCausalLM": ("phi", "PhiForCausalLM"),
    "Phi3ForCausalLM": ("phi3", "Phi3ForCausalLM"),
    "PhiMoEForCausalLM": ("phimoe", "PhiMoEForCausalLM"),
    "Qwen2ForCausalLM": ("qwen2", "Qwen2ForCausalLM"),
    "Qwen2MoeForCausalLM": ("qwen2_moe", "Qwen2MoeForCausalLM"),
    "Qwen2VLForConditionalGeneration":
    ("qwen2_vl", "Qwen2VLForConditionalGeneration"),
    "RWForCausalLM": ("falcon", "FalconForCausalLM"),
    "StableLMEpochForCausalLM": ("stablelm", "StablelmForCausalLM"),
    "StableLmForCausalLM": ("stablelm", "StablelmForCausalLM"),
    "Starcoder2ForCausalLM": ("starcoder2", "Starcoder2ForCausalLM"),
    "ArcticForCausalLM": ("arctic", "ArcticForCausalLM"),
    "XverseForCausalLM": ("xverse", "XverseForCausalLM"),
    "Phi3SmallForCausalLM": ("phi3_small", "Phi3SmallForCausalLM"),
    "MLPSpeculatorPreTrainedModel": ("mlp_speculator", "MLPSpeculator"),
    "JambaForCausalLM": ("jamba", "JambaForCausalLM"),
    "MambaForCausalLM": ("mamba", "MambaForCausalLM"),
    "MedusaModel": ("medusa", "Medusa"),
    "EAGLEModel": ("eagle", "EAGLE"),
    "PersimmonForCausalLM": ("persimmon", "PersimmonForCausalLM"),
    "SolarForCausalLM": ("solar", "SolarForCausalLM"),
    "ExaoneForCausalLM": ("exaone", "ExaoneForCausalLM"),
    "GraniteForCausalLM": ("granite", "GraniteForCausalLM"),
    "GraniteMoeForCausalLM": ("granitemoe", "GraniteMoeForCausalLM"),
}

_EMBEDDING_MODELS = {
    "MistralModel": ("llama_embedding", "LlamaEmbeddingModel"),
    "Qwen2ForRewardModel": ("qwen2_rm", "Qwen2ForRewardModel"),
}

_MULTIMODAL_MODELS = {
    "Blip2ForConditionalGeneration":
    ("blip2", "Blip2ForConditionalGeneration"),
    "ChameleonForConditionalGeneration":
    ("chameleon", "ChameleonForConditionalGeneration"),
    "FuyuForCausalLM": ("fuyu", "FuyuForCausalLM"),
    "InternVLChatModel": ("internvl", "InternVLChatModel"),
    "LlavaForConditionalGeneration":
    ("llava", "LlavaForConditionalGeneration"),
    "LlavaNextForConditionalGeneration": ("llava_next",
                                          "LlavaNextForConditionalGeneration"),
    "LlavaNextVideoForConditionalGeneration":
    ("llava_next_video", "LlavaNextVideoForConditionalGeneration"),
    "LlavaOnevisionForConditionalGeneration":
    ("llava_onevision", "LlavaOnevisionForConditionalGeneration"),
    "MiniCPMV": ("minicpmv", "MiniCPMV"),
    "PaliGemmaForConditionalGeneration": ("paligemma",
                                          "PaliGemmaForConditionalGeneration"),
    "Phi3VForCausalLM": ("phi3v", "Phi3VForCausalLM"),
    "UltravoxModel": ("ultravox", "UltravoxModel"),
    "QWenLMHeadModel": ("qwen", "QWenLMHeadModel"),
    "Qwen2VLForConditionalGeneration": ("qwen2_vl",
                                        "Qwen2VLForConditionalGeneration"),
    "PixtralForConditionalGeneration": ("pixtral",
                                        "PixtralForConditionalGeneration"),
    "MolmoForCausalLM": ("molmo", "MolmoForCausalLM"),
    "MllamaForConditionalGeneration": ("mllama",
                                       "MllamaForConditionalGeneration"),
}

_CONDITIONAL_GENERATION_MODELS = {
    "BartModel": ("bart", "BartForConditionalGeneration"),
    "BartForConditionalGeneration": ("bart", "BartForConditionalGeneration"),
}

_MODELS = {
    **_GENERATION_MODELS,
    **_EMBEDDING_MODELS,
    **_MULTIMODAL_MODELS,
    **_CONDITIONAL_GENERATION_MODELS,
}


# Architecture -> type.
# out of tree models
_OOT_MODELS: Dict[str, Type[nn.Module]] = {}

# Models not supported by ROCm.
_ROCM_UNSUPPORTED_MODELS = []

# Models partially supported by ROCm.
# Architecture -> Reason.
_ROCM_SWA_REASON = ("Sliding window attention (SWA) is not yet supported in "
                    "Triton flash attention. For half-precision SWA support, "
                    "please use CK flash attention by setting "
                    "`APHRODITE_USE_TRITON_FLASH_ATTN=0`")
_ROCM_PARTIALLY_SUPPORTED_MODELS: Dict[str, str] = {
    "Qwen2ForCausalLM":
    _ROCM_SWA_REASON,
    "MistralForCausalLM":
    _ROCM_SWA_REASON,
    "MixtralForCausalLM":
    _ROCM_SWA_REASON,
    "PaliGemmaForConditionalGeneration":
    ("ROCm flash attention does not yet "
     "fully support 32-bit precision on PaliGemma"),
    "Phi3VForCausalLM":
    ("ROCm Triton flash attention may run into compilation errors due to "
     "excessive use of shared memory. If this happens, disable Triton FA "
     "by setting `APHRODITE_USE_TRITON_FLASH_ATTN=0`")
}


class ModelRegistry:
    @staticmethod
    def _get_module_cls_name(model_arch: str) -> Tuple[str, str]:
        module_relname, cls_name = _MODELS[model_arch]
        return f"aphrodite.modeling.models.{module_relname}", cls_name

    @staticmethod
    @lru_cache(maxsize=128)
    def _try_get_model_stateful(model_arch: str) -> Optional[Type[nn.Module]]:
        if model_arch not in _MODELS:
            return None
        module_name, cls_name = ModelRegistry._get_module_cls_name(model_arch)
        module = importlib.import_module(module_name)
        return getattr(module, cls_name, None)

    @staticmethod
    def _try_get_model_stateless(model_arch: str) -> Optional[Type[nn.Module]]:
        if model_arch in _OOT_MODELS:
            return _OOT_MODELS[model_arch]
        if is_hip():
            if model_arch in _ROCM_UNSUPPORTED_MODELS:
                raise ValueError(
                    f"Model architecture {model_arch} is not supported by "
                    "ROCm for now."
                )
            if model_arch in _ROCM_PARTIALLY_SUPPORTED_MODELS:
                logger.warning(
                    f"Model architecture {model_arch} is partially supported "
                    f"by ROCm: {_ROCM_PARTIALLY_SUPPORTED_MODELS[model_arch]}"
                )
        return None

    @staticmethod
    def _try_load_model_cls(model_arch: str) -> Optional[Type[nn.Module]]:
        model = ModelRegistry._try_get_model_stateless(model_arch)
        if model is not None:
            return model
        return ModelRegistry._try_get_model_stateful(model_arch)

    @staticmethod
    def resolve_model_cls(
        architectures: Union[str, List[str]],
    ) -> Tuple[Type[nn.Module], str]:
        if isinstance(architectures, str):
            architectures = [architectures]
        if not architectures:
            logger.warning("No model architectures are specified")
        for arch in architectures:
            model_cls = ModelRegistry._try_load_model_cls(arch)
            if model_cls is not None:
                return (model_cls, arch)
        raise ValueError(
            f"Model architectures {architectures} are not supported for now. "
            f"Supported architectures: {ModelRegistry.get_supported_archs()}"
        )

    @staticmethod
    def get_supported_archs() -> List[str]:
        return list(_MODELS.keys()) + list(_OOT_MODELS.keys())

    @staticmethod
    def register_model(model_arch: str, model_cls: Type[nn.Module]):
        if model_arch in _MODELS:
            logger.warning(
                "Model architecture %s is already registered, and will be "
                "overwritten by the new model class %s.",
                model_arch,
                model_cls.__name__,
            )
        _OOT_MODELS[model_arch] = model_cls

    @staticmethod
    @lru_cache(maxsize=128)
    def _check_stateless(
        func: Callable[[Type[nn.Module]], bool],
        model_arch: str,
        *,
        default: Optional[bool] = None,
    ) -> bool:
        """
        Run a boolean function against a model and return the result.
        If the model is not found, returns the provided default value.
        If the model is not already imported, the function is run inside a
        subprocess to avoid initializing CUDA for the main program.
        """
        model = ModelRegistry._try_get_model_stateless(model_arch)
        if model is not None:
            return func(model)
        if model_arch not in _MODELS and default is not None:
            return default
        module_name, cls_name = ModelRegistry._get_module_cls_name(model_arch)
        valid_name_characters = string.ascii_letters + string.digits + "._"
        if any(s not in valid_name_characters for s in module_name):
            raise ValueError(f"Unsafe module name detected for {model_arch}")
        if any(s not in valid_name_characters for s in cls_name):
            raise ValueError(f"Unsafe class name detected for {model_arch}")
        if any(s not in valid_name_characters for s in func.__module__):
            raise ValueError(f"Unsafe module name detected for {func}")
        if any(s not in valid_name_characters for s in func.__name__):
            raise ValueError(f"Unsafe class name detected for {func}")
        err_id = uuid.uuid4()
        stmts = ";".join(
            [
                f"from {module_name} import {cls_name}",
                f"from {func.__module__} import {func.__name__}",
                f"assert {func.__name__}({cls_name}), '{err_id}'",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", stmts], capture_output=True
        )
        if result.returncode != 0:
            err_lines = [line.decode() for line in result.stderr.splitlines()]
            if err_lines and err_lines[-1] != f"AssertionError: {err_id}":
                err_str = "\n".join(err_lines)
                raise RuntimeError(
                    "An unexpected error occurred while importing the model in "
                    f"another process. Error log:\n{err_str}"
                )
        return result.returncode == 0

    @staticmethod
    def is_embedding_model(architectures: Union[str, List[str]]) -> bool:
        if isinstance(architectures, str):
            architectures = [architectures]
        if not architectures:
            logger.warning("No model architectures are specified")
        return any(arch in _EMBEDDING_MODELS for arch in architectures)

    @staticmethod
    def is_multimodal_model(architectures: Union[str, List[str]]) -> bool:
        if isinstance(architectures, str):
            architectures = [architectures]
        if not architectures:
            logger.warning("No model architectures are specified")
        is_mm = partial(
            ModelRegistry._check_stateless, supports_multimodal, default=False
        )
        return any(is_mm(arch) for arch in architectures)

    @staticmethod
    def is_pp_supported_model(architectures: Union[str, List[str]]) -> bool:
        if isinstance(architectures, str):
            architectures = [architectures]
        if not architectures:
            logger.warning("No model architectures are specified")
        is_pp = partial(
            ModelRegistry._check_stateless, supports_pp, default=False
        )
        return any(is_pp(arch) for arch in architectures)
