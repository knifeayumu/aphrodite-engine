"""Utilities for selecting and loading models."""
import contextlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Type

import torch
import transformers
from loguru import logger
from torch import nn
from transformers.dynamic_module_utils import get_class_from_dynamic_module

from aphrodite.common.config import ModelConfig, ModelImpl
from aphrodite.modeling.models import ModelRegistry
from aphrodite.modeling.models.adapters import (as_classification_model,
                                                as_embedding_model,
                                                as_reward_model)
from aphrodite.quantization.base_config import QuantizationConfig


@contextlib.contextmanager
def set_default_torch_dtype(dtype: torch.dtype):
    """Sets the default torch dtype to the given dtype."""
    old_dtype = torch.get_default_dtype()
    torch.set_default_dtype(dtype)
    yield
    torch.set_default_dtype(old_dtype)


def resolve_transformers_arch(model_config: ModelConfig,
                              architectures: list[str]):
    for i, arch in enumerate(architectures):
        if arch == "TransformersForCausalLM":
            continue
        auto_map: dict[str, str] = getattr(model_config.hf_config, "auto_map",
                                           None) or dict()
        # Make sure that config class is always initialized before model class,
        # otherwise the model class won't be able to access the config class,
        # the expected auto_map should have correct order like:
        # "auto_map": {
        #     "AutoConfig": "<your-repo-name>--<config-name>",
        #     "AutoModel": "<your-repo-name>--<config-name>",
        #     "AutoModelFor<Task>": "<your-repo-name>--<config-name>",
        # },
        auto_modules = {
            name:
            get_class_from_dynamic_module(module,
                                          model_config.model,
                                          revision=model_config.revision)
            for name, module in sorted(auto_map.items(), key=lambda x: x[0])
        }
        model_module = getattr(transformers, arch, None)
        if model_module is None:
            if "AutoModel" not in auto_map:
                raise ValueError(
                    f"Cannot find model module. '{arch}' is not a registered "
                    "model in the Transformers library (only relevant if the "
                    "model is meant to be in Transformers) and 'AutoModel' is "
                    "not present in the model config's 'auto_map' (relevant "
                    "if the model is custom).")
            model_module = auto_modules["AutoModel"]
        # TODO: Further clean up these raises.
        # perhaps handled them in _ModelRegistry._raise_for_unsupported?
        if model_config.model_impl == ModelImpl.TRANSFORMERS:
            if not model_module.is_backend_compatible():
                raise ValueError(
                    f"The Transformers implementation of {arch} is not "
                    "compatible with Aphrodite.")
            architectures[i] = "TransformersForCausalLM"
        if model_config.model_impl == ModelImpl.AUTO:
            if not model_module.is_backend_compatible():
                raise ValueError(
                    f"{arch} has no Aphrodite implementation and the Transformers "
                    "implementation is not compatible with Aphrodite. Try setting "
                    "APHRODITE_USE_V1=0.")
            logger.warning(
                "{} has no Aphrodite implementation, falling back to Transformers "
                "implementation. Some features may not be supported and "
                "performance may not be optimal.", arch)
            architectures[i] = "TransformersForCausalLM"
    return architectures


def get_model_architecture(
        model_config: ModelConfig) -> Tuple[Type[nn.Module], str]:
    architectures = getattr(model_config.hf_config, "architectures", [])

    # Special handling for quantized Mixtral.
    # FIXME: This is a temporary hack.
    mixtral_supported = [
        "fp8", "compressed-tensors", "gptq_marlin", "awq_marlin"
    ]

    if (model_config.quantization is not None
            and model_config.quantization not in mixtral_supported
            and "MixtralForCausalLM" in architectures):
        architectures = ["QuantMixtralForCausalLM"]

    aphrodite_supported_archs = ModelRegistry.get_supported_archs()
    aphrodite_not_supported = not any(arch in aphrodite_supported_archs
                                 for arch in architectures)
    if (model_config.model_impl == ModelImpl.TRANSFORMERS or
            model_config.model_impl != ModelImpl.APHRODITE and aphrodite_not_supported):
        architectures = resolve_transformers_arch(model_config, architectures)

    model_cls, arch = ModelRegistry.resolve_model_cls(architectures)
    if model_config.task == "embed":
        model_cls = as_embedding_model(model_cls)
    elif model_config.task == "classify":
        model_cls = as_classification_model(model_cls)
    elif model_config.task == "reward":
        model_cls = as_reward_model(model_cls)

    return model_cls, arch


def get_architecture_class_name(model_config: ModelConfig) -> str:
    return get_model_architecture(model_config)[1]


@dataclass
class ParamMapping:
    """
    A class to handle parameter mapping for model weight loading.
    It creates a bidirectional mapping between packed parameters and their 
    constituent parts.
    """
    packed_mapping: Dict[str, List[str]]
    inverse_packed_mapping: Dict[str, Tuple[str,
                                            int]] = field(default_factory=dict)

    def __post_init__(self):
        for packed_name, sub_params in self.packed_mapping.items():
            # Skip self-contained cases (e.g., {"W_pack": ["W_pack"]})
            if len(sub_params) == 1 and sub_params[0] == packed_name:
                continue
            for index, param_name in enumerate(sub_params):
                self.inverse_packed_mapping[param_name] = (
                    packed_name,
                    index,
                )

    def get_sub_modules(self,
                        module_name: str) -> Optional[Tuple[str, List[str]]]:
        for key, value in self.packed_mapping.items():
            if module_name.endswith(key):
                return key, value
        return None


def configure_quant_config(quant_config: QuantizationConfig,
                           model_class: Type[nn.Module]):
    """
    Pass packed_modules_mapping by reference to quant_config so that
    quant_config can properly match fused modules
    """

    packed_mapping = getattr(model_class, "packed_modules_mapping", None)
    if packed_mapping is not None:
        # pass packed_modules_mapping by reference to quant_config
        quant_config.packed_modules_mapping = packed_mapping
    else:
        logger.warning(
            "The model class {} has not defined `packed_modules_mapping`, "
            "this may lead to incorrect mapping of quantized or ignored "
            "modules", model_class.__name__)
