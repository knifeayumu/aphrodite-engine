from contextlib import contextmanager
from typing import Any, Dict, Optional

from aphrodite.modeling.layers.fused_moe.layer import (
    FusedMoE, FusedMoEMethodBase, FusedMoeWeightScaleSupported)
from aphrodite.triton_utils import HAS_TRITON

_config: Optional[Dict[str, Any]] = None


@contextmanager
def override_config(config):
    global _config
    old_config = _config
    _config = config
    yield
    _config = old_config


def get_config() -> Optional[Dict[str, Any]]:
    return _config


__all__ = [
    "FusedMoE",
    "FusedMoEMethodBase",
    "FusedMoeWeightScaleSupported",
    "override_config",
    "get_config",
]

if HAS_TRITON:
    # import to register the custom ops
    import aphrodite.modeling.layers.fused_moe.fused_marlin_moe  # noqa
    import aphrodite.modeling.layers.fused_moe.fused_moe  # noqa
    from aphrodite.modeling.layers.fused_moe.cutlass_moe import cutlass_moe_fp8
    from aphrodite.modeling.layers.fused_moe.fused_moe import (
        fused_experts, fused_moe, fused_topk, get_config_file_name,
        grouped_topk)

    __all__ += [
        "fused_moe",
        "fused_topk",
        "fused_experts",
        "get_config_file_name",
        "grouped_topk",
        "cutlass_moe_fp8",
    ]
