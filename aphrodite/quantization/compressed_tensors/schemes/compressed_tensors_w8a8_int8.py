from typing import Callable, List, Optional

import torch
from compressed_tensors.quantization import QuantizationStrategy
from torch.nn import Parameter

from aphrodite.modeling.parameter import (BaseAphroditeParameter,
                                          ChannelQuantScaleParameter,
                                          ModelWeightParameter,
                                          PerTensorScaleParameter)
from aphrodite.quantization.compressed_tensors.schemes import (
    CompressedTensorsScheme)
from aphrodite.quantization.utils.w8a8_utils import (apply_int8_linear,
                                                     convert_to_channelwise)


class CompressedTensorsW8A8Int8(CompressedTensorsScheme):

    def __init__(self, strategy: str, is_static_input_scheme: bool,
                 input_symmetric: bool):
        self.strategy = strategy
        self.is_static_input_scheme = is_static_input_scheme
        self.input_symmetric = input_symmetric

    @classmethod
    def get_min_capability(cls) -> int:
        # turing and up
        return 75

    def process_weights_after_loading(self, layer: torch.nn.Module) -> None:
        # WEIGHT
        # Cutlass kernels need transposed weight.
        weight = layer.weight
        layer.weight = Parameter(weight.t(), requires_grad=False)

        # WEIGHT SCALE
        # Cutlass kernels support only per-tensor and per-channel.
        # If we have a fused module (QKV, MLP) with per tensor scales (thus N
        # scales being passed to the kernel), convert to the per-channel case.
        is_fused_module = len(self.logical_widths) > 1
        if is_fused_module and self.strategy == QuantizationStrategy.TENSOR:
            ws_channelwise = convert_to_channelwise(layer.weight_scale,
                                                    self.logical_widths)
            layer.weight_scale = Parameter(ws_channelwise, requires_grad=False)
        else:
            layer.weight_scale = Parameter(layer.weight_scale.data,
                                           requires_grad=False)
        # INPUT SCALE
        if self.is_static_input_scheme:
            if self.input_symmetric:
                layer.input_scale = Parameter(layer.input_scale.max(),
                                              requires_grad=False)
                layer.input_zero_point = None
            else:
                # reconstruct the ranges
                int8_traits = torch.iinfo(torch.int8)
                azps = layer.input_zero_point.to(dtype=torch.int32)
                range_max = (layer.input_scale *
                             (int8_traits.max - azps)).max()
                range_min = (layer.input_scale *
                             (int8_traits.min - azps)).min()
                scale = (range_max - range_min) / (int8_traits.max -
                                                   int8_traits.min)
                layer.input_scale = Parameter(scale, requires_grad=False)
                # AZP loaded as int8 but used as int32
                azp = (int8_traits.min -
                       range_min / scale).to(dtype=torch.int32)
                layer.input_zero_point = Parameter(azp, requires_grad=False)
        else:
            layer.input_scale = None
            layer.input_zero_point = None
        # azp_adj is the AZP adjustment term, used to account for weights.
        # It does not depend on scales or azp, so it is the same for
        # static and dynamic quantization.
        if not self.input_symmetric:
            layer.azp_adj = layer.weight.sum(dim=0,
                                             keepdim=True,
                                             dtype=torch.int32)
        else:
            layer.azp_adj = None

    def create_weights(self, layer: torch.nn.Module,
                       output_partition_sizes: List[int],
                       input_size_per_partition: int,
                       params_dtype: torch.dtype, weight_loader: Callable,
                       **kwargs):
        self.logical_widths = output_partition_sizes

        # WEIGHT
        weight = ModelWeightParameter(data=torch.empty(
            sum(output_partition_sizes),
            input_size_per_partition,
            dtype=torch.int8),
                                      input_dim=1,
                                      output_dim=0,
                                      weight_loader=weight_loader)

        layer.register_parameter("weight", weight)

        # WEIGHT SCALE
        if self.strategy == QuantizationStrategy.CHANNEL:
            weight_scale = ChannelQuantScaleParameter(
                data=torch.empty((sum(output_partition_sizes), 1),
                                 dtype=torch.float32),
                output_dim=0,
                weight_loader=weight_loader)
        else:
            assert self.strategy == QuantizationStrategy.TENSOR
            weight_scale = PerTensorScaleParameter(data=torch.empty(
                len(output_partition_sizes), dtype=torch.float32),
                                                   weight_loader=weight_loader)
        layer.register_parameter("weight_scale", weight_scale)

        # INPUT SCALE
        if self.is_static_input_scheme:
            input_scale = BaseAphroditeParameter(data=torch.empty(
                1, dtype=torch.float32),
                                            weight_loader=weight_loader)
            layer.register_parameter("input_scale", input_scale)

            if not self.input_symmetric:
                # Note: compressed-tensors stores the zp using the same dtype
                # as the weights
                # AZP loaded as int8 but used as int32
                input_zero_point = BaseAphroditeParameter(
                    data=torch.empty(1, dtype=torch.int8),
                    weight_loader=weight_loader)
                layer.register_parameter("input_zero_point", input_zero_point)

    def apply_weights(self, layer: torch.nn.Module, x: torch.Tensor,
                      bias: Optional[torch.Tensor]) -> torch.Tensor:

        return apply_int8_linear(input=x,
                                 weight=layer.weight,
                                 weight_scale=layer.weight_scale,
                                 input_scale=layer.input_scale,
                                 input_zero_point=layer.input_zero_point,
                                 azp_adj=layer.azp_adj,
                                 bias=bias)
