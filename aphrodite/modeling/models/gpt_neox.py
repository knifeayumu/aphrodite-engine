# Adapted from
# https://github.com/huggingface/transformers/blob/v4.28.0/src/transformers/models/gpt_neox/modeling_gpt_neox.py
# Copyright 2023 The vLLM team.
# Copyright 2022 EleutherAI The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Inference-only GPT-NeoX model compatible with HuggingFace weights."""
from typing import Iterable, Optional, Set, Tuple, Union

import torch
from torch import nn
from transformers import GPTNeoXConfig

from aphrodite.attention import Attention
from aphrodite.common.config import AphroditeConfig, CacheConfig
from aphrodite.common.sequence import IntermediateTensors
from aphrodite.compilation.decorators import support_torch_compile
from aphrodite.distributed import (get_pp_group,
                                   get_tensor_model_parallel_world_size)
from aphrodite.modeling.layers.activation import get_act_fn
from aphrodite.modeling.layers.linear import (ColumnParallelLinear,
                                              QKVParallelLinear,
                                              RowParallelLinear)
from aphrodite.modeling.layers.logits_processor import LogitsProcessor
from aphrodite.modeling.layers.rotary_embedding import get_rope
from aphrodite.modeling.layers.vocab_parallel_embedding import (
    ParallelLMHead, VocabParallelEmbedding)
from aphrodite.modeling.model_loader.weight_utils import default_weight_loader
from aphrodite.modeling.sampling_metadata import SamplingMetadata
from aphrodite.quantization import QuantizationConfig

from .interfaces import SupportsPP
from .utils import (AutoWeightsLoader, is_pp_missing_parameter,
                    make_empty_intermediate_tensors_factory, make_layers,
                    maybe_prefix)


class GPTNeoXAttention(nn.Module):

    def __init__(
        self,
        config: GPTNeoXConfig,
        cache_config: Optional[CacheConfig] = None,
        quant_config: Optional[QuantizationConfig] = None,
        prefix: str = "",
    ):
        super().__init__()
        self.total_num_heads = config.num_attention_heads
        self.hidden_size = config.hidden_size
        self.head_size = self.hidden_size // self.total_num_heads
        self.bias = getattr(config, "attention_bias", True)

        tensor_model_parallel_world_size = (
            get_tensor_model_parallel_world_size())
        assert self.total_num_heads % tensor_model_parallel_world_size == 0
        self.num_heads = (self.total_num_heads //
                          tensor_model_parallel_world_size)

        self.query_key_value = QKVParallelLinear(
            config.hidden_size,
            self.head_size,
            self.total_num_heads,
            bias=self.bias,
            quant_config=quant_config,
        )
        self.dense = RowParallelLinear(
            config.hidden_size,
            config.hidden_size,
            bias=self.bias,
            quant_config=quant_config,
        )
        scaling = self.head_size**-0.5
        rotary_dim = int(self.head_size * config.rotary_pct)
        assert rotary_dim % 2 == 0
        rope_theta = getattr(config, "rope_theta", 10000)
        max_position_embeddings = getattr(config, "max_position_embeddings",
                                          8192)
        self.rotary_emb = get_rope(
            self.head_size,
            rotary_dim=rotary_dim,
            max_position=max_position_embeddings,
            base=rope_theta,
        )
        self.attn = Attention(self.num_heads,
                              self.head_size,
                              scaling,
                              cache_config=cache_config,
                              quant_config=quant_config,
                              prefix=f"{prefix}.attn")

    def forward(
        self,
        position_ids: torch.Tensor,
        hidden_states: torch.Tensor,
    ) -> torch.Tensor:
        qkv, _ = self.query_key_value(hidden_states)
        q, k, v = qkv.chunk(chunks=3, dim=-1)
        q, k = self.rotary_emb(position_ids, q, k)
        attn_output = self.attn(q, k, v)
        output, _ = self.dense(attn_output)
        return output


class GPTNeoXMLP(nn.Module):

    def __init__(
        self,
        config: GPTNeoXConfig,
        quant_config: Optional[QuantizationConfig] = None,
    ):
        super().__init__()
        self.dense_h_to_4h = ColumnParallelLinear(
            config.hidden_size,
            config.intermediate_size,
            quant_config=quant_config,
        )
        self.dense_4h_to_h = RowParallelLinear(
            config.intermediate_size,
            config.hidden_size,
            quant_config=quant_config,
        )
        self.act = get_act_fn(config.hidden_act)

    def forward(self, hidden_states):
        hidden_states, _ = self.dense_h_to_4h(hidden_states)
        hidden_states = self.act(hidden_states)
        hidden_states, _ = self.dense_4h_to_h(hidden_states)
        return hidden_states


class GPTNeoXLayer(nn.Module):

    def __init__(
        self,
        config: GPTNeoXConfig,
        cache_config: Optional[CacheConfig] = None,
        quant_config: Optional[QuantizationConfig] = None,
        prefix: str = "",
    ):
        super().__init__()
        self.use_parallel_residual = config.use_parallel_residual
        self.input_layernorm = nn.LayerNorm(config.hidden_size,
                                            eps=config.layer_norm_eps)
        self.post_attention_layernorm = nn.LayerNorm(config.hidden_size,
                                                     eps=config.layer_norm_eps)
        self.attention = GPTNeoXAttention(config,
                                          cache_config,
                                          quant_config,
                                          prefix=f"{prefix}.attention")
        self.mlp = GPTNeoXMLP(config, quant_config)

    def forward(
        self,
        position_ids: torch.Tensor,
        hidden_states: torch.Tensor,
    ) -> torch.Tensor:
        attn_input = self.input_layernorm(hidden_states)
        attn_output = self.attention(
            position_ids=position_ids,
            hidden_states=attn_input,
        )

        if self.use_parallel_residual:
            # pseudocode:
            # x = x + attn(ln1(x)) + mlp(ln2(x))
            mlp_input = self.post_attention_layernorm(hidden_states)
            mlp_output = self.mlp(mlp_input)
            hidden_states = mlp_output + attn_output + hidden_states
        else:
            # pseudocode:
            # x = x + attn(ln1(x))
            # x = x + mlp(ln2(x))
            attn_output = attn_output + hidden_states
            mlp_input = self.post_attention_layernorm(attn_output)
            mlp_output = self.mlp(mlp_input)
            hidden_states = mlp_output + attn_output
        return hidden_states


@support_torch_compile
class GPTNeoXModel(nn.Module):

    def __init__(self, *, aphrodite_config: AphroditeConfig, prefix: str = ""):
        super().__init__()

        config = aphrodite_config.model_config.hf_config
        cache_config = aphrodite_config.cache_config
        quant_config = aphrodite_config.quant_config

        self.config = config

        self.embed_in = VocabParallelEmbedding(
            config.vocab_size,
            config.hidden_size,
        )
        self.start_layer, self.end_layer, self.layers = make_layers(
            config.num_hidden_layers,
            lambda prefix: GPTNeoXLayer(
                config, cache_config, quant_config, prefix=prefix),
            prefix=f"{prefix}.layers",
        )
        self.final_layer_norm = nn.LayerNorm(config.hidden_size,
                                             eps=config.layer_norm_eps)
        self.make_empty_intermediate_tensors = (
            make_empty_intermediate_tensors_factory(["hidden_states"],
                                                    config.hidden_size))

    def get_input_embeddings(self, input_ids: torch.Tensor) -> torch.Tensor:
        return self.embed_in(input_ids)

    def forward(
        self,
        input_ids: torch.Tensor,
        position_ids: torch.Tensor,
        intermediate_tensors: Optional[IntermediateTensors],
        inputs_embeds: Optional[torch.Tensor] = None,
    ) -> Union[torch.Tensor, IntermediateTensors]:
        if get_pp_group().is_first_rank:
            if inputs_embeds is not None:
                hidden_states = inputs_embeds
            else:
                hidden_states = self.get_input_embeddings(input_ids)
        else:
            hidden_states = intermediate_tensors["hidden_states"]
        for layer in self.layers[self.start_layer:self.end_layer]:
            hidden_states = layer(position_ids, hidden_states)
        if not get_pp_group().is_last_rank:
            return IntermediateTensors({"hidden_states": hidden_states})
        hidden_states = self.final_layer_norm(hidden_states)
        return hidden_states

    def load_weights(self, weights: Iterable[Tuple[str,
                                                   torch.Tensor]]) -> Set[str]:
        params_dict = dict(self.named_parameters())
        loaded_params: Set[str] = set()
        for name, loaded_weight in weights:
            if ("attention.bias" in name or "attention.masked_bias" in name
                    or "rotary_emb.inv_freq" in name):
                continue
            if ("rotary_emb.cos_cached" in name
                    or "rotary_emb.sin_cached" in name):
                # Models trained using OpenRLHF may include
                # these tensors in the checkpoint. Skip them.
                continue
            if is_pp_missing_parameter(name, self):
                continue
            param = params_dict[name]

            if "query_key_value" in name:
                # NOTE: GPT-NeoX's fused QKV's output_dim has the shape of
                # (num_heads * 3 * head_size), while the
                # required shape is (3 * num_heads * head_size).
                # Thus, we need weight conversion.
                output_dim = getattr(param, "output_dim", None)
                num_heads = self.config.num_attention_heads
                if output_dim is not None:
                    loaded_weight_shape = loaded_weight.shape
                    loaded_weight = loaded_weight.view(
                        loaded_weight_shape[:output_dim] + (num_heads, 3, -1) +
                        loaded_weight_shape[output_dim + 1:])
                    loaded_weight = loaded_weight.transpose(
                        output_dim, output_dim + 1)
                    loaded_weight = loaded_weight.reshape(loaded_weight_shape)

            weight_loader = getattr(param, "weight_loader",
                                    default_weight_loader)
            weight_loader(param, loaded_weight)
            loaded_params.add(name)
        return loaded_params


class GPTNeoXForCausalLM(nn.Module, SupportsPP):

    def __init__(self, *, aphrodite_config: AphroditeConfig, prefix: str = ""):
        super().__init__()
        config = aphrodite_config.model_config.hf_config
        quant_config = aphrodite_config.quant_config
        self.config = config
        self.quant_config = quant_config
        self.gpt_neox = GPTNeoXModel(aphrodite_config=aphrodite_config,
                                     prefix=maybe_prefix(prefix, "gpt_neox"))
        self.embed_out = ParallelLMHead(
            config.vocab_size,
            config.hidden_size,
            quant_config=quant_config,
        )
        if self.config.tie_word_embeddings:
            self.embed_out.weight = self.gpt_neox.embed_in.weight
        self.logits_processor = LogitsProcessor(config.vocab_size)
        self.make_empty_intermediate_tensors = (
            self.gpt_neox.make_empty_intermediate_tensors)

    def get_input_embeddings(self, input_ids: torch.Tensor) -> torch.Tensor:
        return self.gpt_neox.get_input_embeddings(input_ids)

    def forward(
        self,
        input_ids: torch.Tensor,
        positions: torch.Tensor,
        intermediate_tensors: Optional[IntermediateTensors] = None,
        inputs_embeds: Optional[torch.Tensor] = None,
    ) -> Union[torch.Tensor, IntermediateTensors]:
        hidden_states = self.gpt_neox(input_ids, positions,
                                      intermediate_tensors, inputs_embeds)
        return hidden_states

    def compute_logits(
        self,
        hidden_states: torch.Tensor,
        sampling_metadata: SamplingMetadata,
    ) -> Optional[torch.Tensor]:
        logits = self.logits_processor(self.embed_out, hidden_states,
                                       sampling_metadata)
        return logits

    def load_weights(self, weights: Iterable[Tuple[str,
                                                   torch.Tensor]]) -> Set[str]:
        loader = AutoWeightsLoader(self)
        return loader.load_weights(weights)
