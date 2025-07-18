from itertools import cycle

import pytest

from aphrodite import SamplingParams

from ..utils import maybe_enable_chunked_prefill
from .conftest import run_equality_correctness_test


@pytest.mark.parametrize(
    "common_llm_kwargs",
    [{
        "model_name": "JackFram/llama-160m",

        # Skip cuda graph recording for fast test.
        "enforce_eager": True
    }])
@pytest.mark.parametrize("per_test_common_llm_kwargs", [{}])
@pytest.mark.parametrize("baseline_llm_kwargs", [{}])
@pytest.mark.parametrize("test_llm_kwargs", [{
    "speculative_config": {
        "model": "JackFram/llama-68m",
        "num_speculative_tokens": 3,
        "disable_logprobs": False,
    },
}, {
    "speculative_config": {
        "model": "JackFram/llama-68m",
        "num_speculative_tokens": 3,
        "disable_logprobs": True,
    },
}])
@pytest.mark.parametrize("batch_size", [8])
@pytest.mark.parametrize(
    "output_len",
    [
        # Use smaller output len for fast test.
        7,
    ])
@pytest.mark.parametrize("seed", [1])
@pytest.mark.parametrize("logprobs", [1, 6])
@pytest.mark.parametrize("prefill_chunk_size", [-1, 4, 12])
def test_logprobs_equality(aphrodite_runner, common_llm_kwargs,
                           per_test_common_llm_kwargs, baseline_llm_kwargs,
                           test_llm_kwargs, batch_size: int, output_len: int,
                           seed: int, logprobs: int, prefill_chunk_size: int):
    """Verify output logprobs are equal with and without speculative decoding,
        as well as with and without chunked prefill.
    """
    maybe_enable_chunked_prefill(prefill_chunk_size, common_llm_kwargs)
    run_equality_correctness_test(
        aphrodite_runner,
        common_llm_kwargs,
        per_test_common_llm_kwargs,
        baseline_llm_kwargs,
        test_llm_kwargs,
        batch_size,
        output_len,
        seed,
        temperature=0.0,
        logprobs=logprobs,
        prompt_logprobs=logprobs,
        disable_logprobs=test_llm_kwargs["speculative_config"]
        ["disable_logprobs"])


@pytest.mark.parametrize(
    "common_llm_kwargs",
    [{
        "model_name": "JackFram/llama-68m",

        # Skip cuda graph recording for fast test.
        "enforce_eager": True,
    }])
@pytest.mark.parametrize("per_test_common_llm_kwargs", [{}])
@pytest.mark.parametrize("baseline_llm_kwargs", [{}])
@pytest.mark.parametrize("test_llm_kwargs", [{
    "speculative_config": {
        "model": "JackFram/llama-160m",
        "num_speculative_tokens": 3,
        "disable_logprobs": False,
    },
}, {
    "speculative_config": {
        "model": "JackFram/llama-160m",
        "num_speculative_tokens": 6,
        "disable_logprobs": False,
    },
}])
@pytest.mark.parametrize("batch_size", [8])
@pytest.mark.parametrize(
    "output_len",
    [
        # Use smaller output len for fast test.
        32,
    ])
@pytest.mark.parametrize("seed", [1])
@pytest.mark.parametrize("logprobs", [1, 6])
def test_logprobs_different_k(aphrodite_runner, common_llm_kwargs,
                              per_test_common_llm_kwargs, baseline_llm_kwargs,
                              test_llm_kwargs, batch_size: int,
                              output_len: int, seed: int, logprobs: int):
    """Veriy logprob greedy equality with different speculation lens.
    """
    run_equality_correctness_test(
        aphrodite_runner,
        common_llm_kwargs,
        per_test_common_llm_kwargs,
        baseline_llm_kwargs,
        test_llm_kwargs,
        batch_size,
        output_len,
        seed,
        temperature=0.0,
        logprobs=logprobs,
        disable_logprobs=test_llm_kwargs["speculative_config"]
        ["disable_logprobs"])


@pytest.mark.parametrize(
    "common_llm_kwargs",
    [{
        "model_name": "JackFram/llama-68m",

        # Skip cuda graph recording for fast test.
        "enforce_eager": True,
    }])
@pytest.mark.parametrize("per_test_common_llm_kwargs", [{}])
@pytest.mark.parametrize("baseline_llm_kwargs", [{}])
@pytest.mark.parametrize(
    "test_llm_kwargs",
    [{
        "speculative_config": {
            "model": "JackFram/llama-160m",
            "num_speculative_tokens": 3,
            "disable_logprobs": False,
            # Artificially limit the draft model max model len; this forces
            # Aphrodite to skip speculation once the sequences grow beyond 32-k
            # tokens.
            "max_model_len": 32,
        },
    }])
@pytest.mark.parametrize("batch_size", [8])
@pytest.mark.parametrize(
    "output_len",
    [
        # Use smaller output len for fast test.
        32,
    ])
@pytest.mark.parametrize("seed", [1])
@pytest.mark.parametrize("logprobs", [1])
def test_logprobs_when_skip_speculation(aphrodite_runner, common_llm_kwargs,
                                        per_test_common_llm_kwargs,
                                        baseline_llm_kwargs, test_llm_kwargs,
                                        batch_size: int, output_len: int,
                                        seed: int, logprobs: int):
    """Verify logprobs greedy equality when some sequences skip speculation.
    """
    run_equality_correctness_test(
        aphrodite_runner,
        common_llm_kwargs,
        per_test_common_llm_kwargs,
        baseline_llm_kwargs,
        test_llm_kwargs,
        batch_size,
        output_len,
        seed,
        temperature=0.0,
        logprobs=logprobs,
        disable_logprobs=test_llm_kwargs["speculative_config"]
        ["disable_logprobs"])


@pytest.mark.parametrize(
    "common_llm_kwargs",
    [{
        "model_name": "JackFram/llama-68m",

        # Skip cuda graph recording for fast test.
        "enforce_eager": True,
    }])
@pytest.mark.parametrize("per_test_common_llm_kwargs", [{}])
@pytest.mark.parametrize("baseline_llm_kwargs", [{}])
@pytest.mark.parametrize("test_llm_kwargs", [{
    "speculative_config": {
        "model": "JackFram/llama-160m",
        "num_speculative_tokens": 3,
        "disable_logprobs": False,
    },
}])
@pytest.mark.parametrize("batch_size", [1])
@pytest.mark.parametrize(
    "output_len",
    [
        # Use smaller output len for fast test.
        32,
    ])
@pytest.mark.parametrize("seed", [1])
@pytest.mark.parametrize("logprobs", [6])
def test_logprobs_temp_1(aphrodite_runner, common_llm_kwargs,
                         per_test_common_llm_kwargs, baseline_llm_kwargs,
                         test_llm_kwargs, batch_size: int, output_len: int,
                         seed: int, logprobs: int):
    """Verify at least one logprob result has num_logprobs+1, which tests the
    case where the sampled token is not in top-k logprobs.

    Ideally, this test should validate equality with non-spec by getting
    logprobs. This is left as future improvement.
    """
    temperature = 1.0

    prompts = [
        "Hello, my name is",
        "The president of the United States is",
        "The capital of France is",
        "The future of AI is",
        "San Francisco is know for its",
        "Facebook was created in 2004 by",
        "Curious George is a",
        "Python 3.11 brings improvements to its",
    ]

    prompts = [prompt for prompt, _ in zip(cycle(prompts), range(batch_size))]

    sampling_params = SamplingParams(
        max_tokens=output_len,
        ignore_eos=True,
        temperature=temperature,
        logprobs=logprobs,
    )

    sd_args = {
        **common_llm_kwargs,
        **per_test_common_llm_kwargs,
        **test_llm_kwargs,
    }

    with aphrodite_runner(**sd_args) as aphrodite_model:
        sd_outputs = aphrodite_model.generate_w_logprobs(prompts, sampling_params)

    num_returned_logprobs = [
        len(seq_logprobs) for seq_logprobs in sd_outputs[-1]
    ]

    # Assert one of the returned logprobs has > num_logprobs (indicating the
    # sampled token is not in top-k).
    assert any(
        [num_returned > logprobs for num_returned in num_returned_logprobs])


@pytest.mark.parametrize(
    "common_llm_kwargs",
    [{
        "model_name": "JackFram/llama-160m",
        # Skip cuda graph recording for fast test.
        "enforce_eager": True,
    }])
@pytest.mark.parametrize("per_test_common_llm_kwargs", [{}])
@pytest.mark.parametrize("baseline_llm_kwargs", [{}])
@pytest.mark.parametrize("test_llm_kwargs", [{
    "speculative_config": {
        "model": "JackFram/llama-68m",
        "num_speculative_tokens": 3,
        "disable_logprobs": True,
    },
}])
@pytest.mark.parametrize("seed", [1])
@pytest.mark.parametrize("batch_size", [4])
@pytest.mark.parametrize(
    "output_len",
    [
        # Use smaller output len for fast test.
        32,
    ])
@pytest.mark.parametrize("logprobs", [0])
def test_logprobs_disabled(aphrodite_runner, common_llm_kwargs,
                           per_test_common_llm_kwargs, baseline_llm_kwargs,
                           test_llm_kwargs, batch_size: int, output_len: int,
                           seed: int, logprobs: int):
    """Check the behavior when logprobs are disabled.
    Token choices should match with the base model.
    """
    run_equality_correctness_test(
        aphrodite_runner,
        common_llm_kwargs,
        per_test_common_llm_kwargs,
        baseline_llm_kwargs,
        test_llm_kwargs,
        batch_size,
        output_len,
        seed,
        temperature=0.0,
        logprobs=logprobs,
        disable_logprobs=test_llm_kwargs["speculative_config"]
        ["disable_logprobs"])
