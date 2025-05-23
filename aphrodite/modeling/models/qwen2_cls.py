# coding=utf-8
# Adapted from
# https://huggingface.co/Qwen/Qwen2.5-Math-RM-72B/blob/main/modeling_qwen2_rm.py
# Copyright 2024 Kakao Corp. (Kanana-X Team)
# Copyright 2024 The Qwen team.
# Copyright 2023 The PygmalionAI team.
# Copyright 2023 The vLLM team.
"""Inference-only Qwen2-Classification model compatible with HF weights."""
from typing import Iterable, List, Optional, Tuple

import torch
from torch import nn
from transformers import Qwen2Config

from aphrodite.attention import AttentionMetadata
from aphrodite.common.config import CacheConfig, LoRAConfig
from aphrodite.common.sequence import IntermediateTensors, PoolerOutput
from aphrodite.modeling.layers.linear import RowParallelLinear
from aphrodite.modeling.layers.pooler import Pooler, PoolingType
from aphrodite.modeling.models.qwen2 import Qwen2Model
from aphrodite.modeling.pooling_metadata import PoolingMetadata
from aphrodite.quantization.base_config import QuantizationConfig

from .utils import AutoWeightsLoader


class Qwen2ForSequenceClassification(nn.Module):
    packed_modules_mapping = {
        "qkv_proj": [
            "q_proj",
            "k_proj",
            "v_proj",
        ],
        "gate_up_proj": [
            "gate_proj",
            "up_proj",
        ],
    }

    # LoRA specific attributes
    supported_lora_modules = [
        "qkv_proj",
        "o_proj",
        "gate_up_proj",
        "down_proj",
    ]
    embedding_modules = {}
    embedding_padding_modules = []

    def __init__(
        self,
        config: Qwen2Config,
        cache_config: Optional[CacheConfig] = None,
        quant_config: Optional[QuantizationConfig] = None,
        lora_config: Optional[LoRAConfig] = None,
    ) -> None:
        # TODO (@robertgshaw2): see if this can be moved out
        if (cache_config.sliding_window is not None
                and hasattr(config, "max_window_layers")):
            raise ValueError(
                "Sliding window for some but all layers is not "
                "supported. This model uses sliding window "
                f"but `max_window_layers` = {config.max_window_layers} is "
                f"less than `num_hidden_layers` = {config.num_hidden_layers}. "
                "Please open an issue to discuss this feature.")


        super().__init__()

        self.config = config
        self.lora_config = lora_config

        self.quant_config = quant_config
        self.model = Qwen2Model(config, cache_config, quant_config)

        self.score = RowParallelLinear(config.hidden_size,
                                       config.num_labels,
                                       quant_config=quant_config)
        self._pooler = Pooler(pooling_type=PoolingType.LAST,
                              normalize=False,
                              softmax=True)

    def forward(
        self,
        input_ids: torch.Tensor,
        positions: torch.Tensor,
        kv_caches: List[torch.Tensor],
        attn_metadata: AttentionMetadata,
        intermediate_tensors: Optional[IntermediateTensors] = None,
    ) -> torch.Tensor:
        hidden_states = self.model(input_ids, positions, kv_caches,
                                   attn_metadata, intermediate_tensors)
        logits, _ = self.score(hidden_states)
        return logits

    def pooler(
        self,
        hidden_states: torch.Tensor,
        pooling_metadata: PoolingMetadata,
    ) -> Optional[PoolerOutput]:
        return self._pooler(hidden_states, pooling_metadata)

    def load_weights(self, weights: Iterable[Tuple[str, torch.Tensor]]):
        loader = AutoWeightsLoader(self,
                                   ignore_unexpected_prefixes=["lm_head."])
        loader.load_weights(weights)
