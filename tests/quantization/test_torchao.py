import importlib.metadata
import importlib.util

import pytest
import torch

DTYPE = ["bfloat16"]

TORCHAO_AVAILABLE = importlib.util.find_spec("torchao") is not None


@pytest.mark.skipif(not TORCHAO_AVAILABLE, reason="torchao is not available")
def test_pre_quantized_model(aphrodite_runner):
    with aphrodite_runner("drisspg/float8_dynamic_act_float8_weight-opt-125m",
                     quantization="torchao",
                     dtype="bfloat16",
                     enforce_eager=True) as llm:
        output = llm.generate_greedy(["The capital of France is"],
                                     max_tokens=32)
    assert output
    print(output)


@pytest.mark.skipif(not TORCHAO_AVAILABLE, reason="torchao is not available")
@pytest.mark.parametrize(
    "pt_load_map_location",
    [
        "cuda:0",
        # {"": "cuda"},
    ])
def test_opt_125m_int4wo_model_loading_with_params(aphrodite_runner,
                                                   pt_load_map_location):
    """
    Test loading roberta-base model with no lm_head.
    """
    torch._dynamo.reset()
    model_name = "jerryzh168/opt-125m-int4wo"
    with aphrodite_runner(model_name=model_name,
                     quantization="torchao",
                     dtype="bfloat16",
                     pt_load_map_location=pt_load_map_location) as llm:
        output = llm.generate_greedy(["The capital of France is"],
                                     max_tokens=32)

        assert output
        print(output)


if __name__ == "__main__":
    pytest.main([__file__])
