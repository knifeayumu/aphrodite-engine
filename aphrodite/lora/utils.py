import os
import re
from typing import List, Optional, Set, Tuple, Type, Union

import huggingface_hub
from huggingface_hub.utils import (EntryNotFoundError, HfHubHTTPError,
                                   HFValidationError, RepositoryNotFoundError)
from loguru import logger
from torch import nn
from transformers import PretrainedConfig

from aphrodite.common.config import LoRAConfig
from aphrodite.lora.fully_sharded_layers import (
    ColumnParallelLinearWithShardedLoRA,
    MergedColumnParallelLinearWithShardedLoRA,
    MergedQKVParallelLinearWithShardedLoRA, QKVParallelLinearWithShardedLoRA,
    RowParallelLinearWithShardedLoRA)
from aphrodite.lora.layers import (BaseLayerWithLoRA,
                                   ColumnParallelLinearWithLoRA,
                                   LinearScalingRotaryEmbeddingWithLoRA,
                                   LogitsProcessorWithLoRA,
                                   MergedColumnParallelLinearWithLoRA,
                                   MergedQKVParallelLinearWithLoRA,
                                   QKVParallelLinearWithLoRA,
                                   ReplicatedLinearWithLoRA,
                                   RowParallelLinearWithLoRA,
                                   VocabParallelEmbeddingWithLoRA)
from aphrodite.modeling.layers.linear import LinearBase
from aphrodite.modeling.layers.logits_processor import LogitsProcessor
from aphrodite.modeling.layers.vocab_parallel_embedding import ParallelLMHead
from aphrodite.modeling.models.utils import WeightsMapper

_all_lora_classes: Set[Type[BaseLayerWithLoRA]] = {
    VocabParallelEmbeddingWithLoRA,
    ColumnParallelLinearWithLoRA,
    MergedColumnParallelLinearWithLoRA,
    QKVParallelLinearWithLoRA,
    MergedQKVParallelLinearWithLoRA,
    RowParallelLinearWithLoRA,
    ReplicatedLinearWithLoRA,
    LogitsProcessorWithLoRA,
    ColumnParallelLinearWithShardedLoRA,
    QKVParallelLinearWithShardedLoRA,
    MergedColumnParallelLinearWithShardedLoRA,
    MergedQKVParallelLinearWithShardedLoRA,
    RowParallelLinearWithShardedLoRA,
    LinearScalingRotaryEmbeddingWithLoRA,
}


def from_layer(layer: nn.Module,
               max_loras: int,
               lora_config: LoRAConfig,
               packed_modules_list: List,
               model_config: Optional[PretrainedConfig] = None) -> nn.Module:
    for lora_cls in _all_lora_classes:
        # specifying kwargs so they can be easily accessed in decorator
        if lora_cls.can_replace_layer(source_layer=layer,
                                      lora_config=lora_config,
                                      packed_modules_list=packed_modules_list,
                                      model_config=model_config):
            instance_layer = lora_cls(layer)
            instance_layer.create_lora_weights(max_loras, lora_config,
                                               model_config)
            return instance_layer
    return layer


def from_layer_logits_processor(
    layer: LogitsProcessor,
    lm_head: ParallelLMHead,
    max_loras: int,
    lora_config: LoRAConfig,
    model_config: Optional[PretrainedConfig] = None,
) -> LogitsProcessorWithLoRA:
    ret = LogitsProcessorWithLoRA(layer, lm_head.embedding_dim,
                                  lm_head.weight.dtype, lm_head.weight.device,
                                  lm_head.get_sharded_to_full_mapping())
    ret.create_lora_weights(max_loras, lora_config, model_config)
    return ret


def replace_submodule(model: nn.Module, module_name: str,
                      new_module: nn.Module) -> nn.Module:
    """Replace a submodule in a model with a new module."""
    parent = model.get_submodule(".".join(module_name.split(".")[:-1]))
    target_name = module_name.split(".")[-1]
    setattr(parent, target_name, new_module)
    return new_module


def parse_fine_tuned_lora_name(
    name: str,
    weights_mapper: Optional[WeightsMapper] = None
) -> Optional[Tuple[str, bool, bool]]:
    """Parse the name of lora weights.

    args:
        name: the name of the fine-tuned LoRA, e.g.
            base_model.model.dense1.weight
        weights_mapper: maps the name of weight, e.g.
            `model.` -> `language_model.model.`,
    return:
        Tuple(module_name, is_lora_a, is_bias) or None if unsupported:
            module_name: the name of the module, e.g. model.dense1,
            is_lora_a whether the tensor is lora_a or lora_b.
            is_bias whether the tensor is lora bias.
            None if the weight name is not a supported LoRA weight format.
    """

    # LoRA weight qualified name usually starts with `base_model.model.`,
    # so we remove the prefix `base_model.model.` to make the following
    # mapping correctly.
    if "base_model.model." in name:
        name = name.replace("base_model.model.", "")
        name = weights_mapper._map_name(name) if weights_mapper else name
        # recover the prefix `base_model.model.`
        name = "base_model.model." + name

    # In some situations, we may not start with `base_model.model.`.
    # If we don't (e.g., ibm-granite/granite-speech-3.3-8b),
    # we should keep the prefix intact.
    start_index = 2 if "base_model.model." in name else 0

    parts = name.split(".")
    if parts[-1] == "weight" and (parts[-2] == "lora_A"
                                  or parts[-2] == "lora_B"):
        new_name = ".".join(parts[start_index:-2])
        return new_name, parts[-2] == "lora_A", False

    if parts[-1] == "lora_embedding_A" or parts[-1] == "lora_embedding_B":
        new_name = ".".join(parts[start_index:-1])
        return new_name, parts[-1] == "lora_embedding_A", False

    if parts[-1] == "bias":
        new_name = ".".join(parts[start_index:-2])
        return new_name, False, True

    # Return None for unsupported weights instead of raising an exception
    return None


def is_supported_lora_weight(name: str) -> bool:
    """Check if a weight name follows a supported LoRA format.
    
    args:
        name: the name of the weight to check
        
    return:
        True if the weight name follows a supported LoRA format, False otherwise.
    """
    return parse_fine_tuned_lora_name(name) is not None


def is_regex_target_modules(load_modules: Union[str, List[str]],
                            expected_lora_modules: List[str]) -> bool:
    """
    PEFT supports passing `target_modules` in the form of regular expressions, 
    such as `model.*(q_proj|k_proj|v_proj)$`. This function is mainly used to 
    determine whether the suffix in the regular expression is present in the 
    `expected_lora_modules`.
    """

    def is_valid_regex(pattern):
        try:
            re.compile(pattern)
            return True
        except re.error:
            return False

    def is_subset(sub_list, full_list):
        return set(sub_list).issubset(set(full_list))

    # Similar to PEFT's processing logic, regex-related operations are only
    #  executed when the load_modules is a `str`.
    if not isinstance(load_modules, str):
        return False

    if is_valid_regex(load_modules):
        match = re.search(r"\((.*?)\)\$?$", load_modules)
        if match:
            suffix = match.group(1).split("|")
            return is_subset(suffix, expected_lora_modules)
    return False


def get_supported_lora_modules(model: nn.Module) -> List[str]:
    """
    In Aphrodite, all linear layers support LoRA.
    """
    supported_lora_modules: Set[str] = set()
    # step1: traverse the model to get all the linear subfixes.
    for name, module in model.named_modules():
        if isinstance(module, (LinearBase, )):
            supported_lora_modules.add(name.split(".")[-1])
    # step 2: get the embedding modules if the model's mbedding_modules
    # is not empty.
    if model.embedding_modules:
        for name in model.embedding_modules:
            supported_lora_modules.add(name)
    return list(supported_lora_modules)


def get_adapter_absolute_path(lora_path: str) -> str:
    """
    Resolves the given lora_path to an absolute local path.

    If the lora_path is identified as a Hugging Face model identifier,
    it will download the model and return the local snapshot path.
    Otherwise, it treats the lora_path as a local file path and
    converts it to an absolute path.

    Parameters:
    lora_path (str): The path to the lora model, which can be an absolute path,
                     a relative path, or a Hugging Face model identifier.

    Returns:
    str: The resolved absolute local path to the lora model.
    """

    # Check if the path is an absolute path. Return it no matter exists or not.
    if os.path.isabs(lora_path):
        return lora_path

    # If the path starts with ~, expand the user home directory.
    if lora_path.startswith('~'):
        return os.path.expanduser(lora_path)

    # Check if the expanded relative path exists locally.
    if os.path.exists(lora_path):
        return os.path.abspath(lora_path)

    # If the path does not exist locally, assume it's a Hugging Face repo.
    try:
        local_snapshot_path = huggingface_hub.snapshot_download(
            repo_id=lora_path)
    except (HfHubHTTPError, RepositoryNotFoundError, EntryNotFoundError,
            HFValidationError):
        # Handle errors that may occur during the download
        # Return original path instead instead of throwing error here
        logger.exception("Error downloading the HuggingFace model")
        return lora_path

    return local_snapshot_path
