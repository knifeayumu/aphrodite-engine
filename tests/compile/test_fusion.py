import pytest
import torch

import aphrodite.common.envs as envs
import aphrodite.plugins
from aphrodite.compilation.fusion import (FUSED_OPS, QUANT_OPS, FusedRMSQuantKey,
                                     FusionPass, QuantKey)
from aphrodite.compilation.fx_utils import find_auto_fn, find_auto_fn_maybe
from aphrodite.compilation.noop_elimination import NoOpEliminationPass
from aphrodite.common.config import CompilationConfig, CompilationLevel, AphroditeConfig
from aphrodite.modeling.layers.layernorm import RMSNorm
from aphrodite.quantization.utils.w8a8_utils import (
    CUTLASS_FP8_SUPPORTED, Fp8LinearOp, maybe_create_device_identity)
from aphrodite.platforms import current_platform

from .backend import TestBackend

FP8_DTYPE = current_platform.fp8_dtype()


class TestModel(torch.nn.Module):

    def __init__(self, hidden_size: int, eps: float, static: bool,
                 cutlass_fp8_enabled: bool, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cutlass_fp8_enabled = cutlass_fp8_enabled
        self.norm = [RMSNorm(hidden_size, eps) for _ in range(3)]
        self.wscale = [torch.rand(1, dtype=torch.float32) for _ in range(2)]
        if static:
            self.scale = [torch.rand(1, dtype=torch.float32) for _ in range(2)]
        else:
            self.scale = [None for _ in range(2)]
        self.w = [
            torch.rand(hidden_size, hidden_size).to(dtype=FP8_DTYPE).t()
            for _ in range(2)
        ]
        self.fp8_linear = Fp8LinearOp(
            cutlass_fp8_supported=cutlass_fp8_enabled,
            use_per_token_if_dynamic=True)

    def forward(self, x):
        resid = torch.sqrt(x)
        y = self.norm[0](x)

        x2 = self.fp8_linear.apply(y,
                                   self.w[0],
                                   self.wscale[0],
                                   input_scale=self.scale[0])
        # make sure resid is used for replacement to work
        y2, resid = self.norm[1](x2, resid)

        x3 = self.fp8_linear.apply(y2,
                                   self.w[1],
                                   self.wscale[1],
                                   input_scale=self.scale[1])
        y3, resid = self.norm[2](x3, resid)  # use resid here
        return y3


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("hidden_size", [64, 3392, 4096])
@pytest.mark.parametrize("num_tokens", [7, 256, 533, 2048, 2049])
@pytest.mark.parametrize("eps", [1e-5, 1e-6])
@pytest.mark.parametrize("static", [True, False])
@pytest.mark.parametrize("cutlass_fp8_enabled",
                         [True, False] if CUTLASS_FP8_SUPPORTED else [False])
@pytest.mark.skipif(envs.APHRODITE_TARGET_DEVICE not in ["cuda", "rocm"],
                    reason="Only test on CUDA and ROCm")
def test_fusion_rmsnorm_quant(dtype, hidden_size, num_tokens, eps, static,
                              cutlass_fp8_enabled):
    torch.set_default_device("cuda")
    torch.set_default_dtype(dtype)
    torch.manual_seed(1)
    maybe_create_device_identity()  # needed for certain non-cutlass fp8 paths

    aphrodite_config = AphroditeConfig(compilation_config=CompilationConfig(
        level=CompilationLevel.PIECEWISE, custom_ops=["+rms_norm"]))
    aphrodite_config.compilation_config.pass_config = \
            CompilationConfig.PassConfig(enable_fusion=True,
                                              enable_noop=True)
    with aphrodite.common.config.set_current_aphrodite_config(aphrodite_config):
        # Reshape pass is needed for the fusion pass to work
        noop_pass = NoOpEliminationPass(aphrodite_config)
        fusion_pass = FusionPass.instance(aphrodite_config)

        backend = TestBackend(noop_pass, fusion_pass)
        model = TestModel(hidden_size, eps, static, cutlass_fp8_enabled)

        # First dimension dynamic
        x = torch.rand(num_tokens, hidden_size)
        torch._dynamo.mark_dynamic(x, 0)

        result = model(x)

        model2 = torch.compile(model, backend=backend)
        result2 = model2(x)

        # Higher tol for dynamic, even higher for bfloat16
        if static:
            ATOL, RTOL = (1e-3, 1e-3)
        elif dtype == torch.float16:
            ATOL, RTOL = (2e-3, 2e-3)
        else:
            ATOL, RTOL = (1e-2, 1e-2)

        torch.testing.assert_close(result, result2, atol=ATOL, rtol=RTOL)

        # Check substitution worked
        pre_nodes = backend.graph_pre_pass.nodes
        post_nodes = backend.graph_post_pass.nodes

        # static is per-tensor, dynamic is per-token
        key = QuantKey(dtype=FP8_DTYPE,
                       static=static,
                       per_tensor=static,
                       symmetric=True)
        rms_quant = FUSED_OPS[FusedRMSQuantKey(key, False)]
        add_rms_quant = FUSED_OPS[FusedRMSQuantKey(key, True)]
        fp8_quant = QUANT_OPS[key]

        # In pre-nodes, fp8 quant should be there and fused kernels should not
        assert find_auto_fn_maybe(pre_nodes, rms_quant) is None
        assert find_auto_fn_maybe(pre_nodes, add_rms_quant) is None
        find_auto_fn(pre_nodes, fp8_quant)

        # In post-nodes, fused kernels should be there and fp8 quant should not
        find_auto_fn(post_nodes, rms_quant)
        find_auto_fn(post_nodes, add_rms_quant)
        assert find_auto_fn_maybe(post_nodes, fp8_quant) is None
