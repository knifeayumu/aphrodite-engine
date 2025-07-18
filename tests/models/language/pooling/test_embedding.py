import pytest

from aphrodite.common.config import PoolerConfig
from aphrodite.platforms import current_platform

from ...utils import check_embeddings_close


@pytest.mark.parametrize(
    "model",
    [
        # [Encoder-only]
        pytest.param("BAAI/bge-base-en-v1.5",
                     marks=[pytest.mark.core_model, pytest.mark.cpu_model]),
        pytest.param("sentence-transformers/all-MiniLM-L12-v2"),
        pytest.param("intfloat/multilingual-e5-small"),
        pytest.param("Alibaba-NLP/gte-Qwen2-7B-instruct"),
        # [Decoder-only]
        pytest.param("BAAI/bge-multilingual-gemma2",
                     marks=[pytest.mark.core_model]),
        pytest.param("intfloat/e5-mistral-7b-instruct",
                     marks=[pytest.mark.core_model, pytest.mark.cpu_model]),
        pytest.param("Alibaba-NLP/gte-Qwen2-1.5B-instruct"),
        pytest.param("ssmits/Qwen2-7B-Instruct-embed-base"),
        # [Cross-Encoder]
        pytest.param("sentence-transformers/stsb-roberta-base-v2"),
    ],
)
@pytest.mark.parametrize("dtype", ["half"])
def test_models(
    hf_runner,
    aphrodite_runner,
    example_prompts,
    model,
    dtype: str,
    monkeypatch,
) -> None:

    if model == "BAAI/bge-multilingual-gemma2" and current_platform.is_rocm():
        # ROCm Triton FA does not currently support sliding window attention
        # switch to use ROCm CK FA backend
        monkeypatch.setenv("APHRODITE_USE_TRITON_FLASH_ATTN", "False")

    aphrodite_extra_kwargs = {}
    if model == "ssmits/Qwen2-7B-Instruct-embed-base":
        aphrodite_extra_kwargs["override_pooler_config"] = \
            PoolerConfig(pooling_type="MEAN")

    if model == "Alibaba-NLP/gte-Qwen2-1.5B-instruct":
        aphrodite_extra_kwargs["hf_overrides"] = {"is_causal": True}

    # The example_prompts has ending "\n", for example:
    # "Write a short story about a robot that dreams for the first time.\n"
    # sentence_transformers will strip the input texts, see:
    # https://github.com/UKPLab/sentence-transformers/blob/v3.1.1/sentence_transformers/models/Transformer.py#L159
    # This makes the input_ids different between hf_model and aphrodite_model.
    # So we need to strip the input texts to avoid test failing.
    example_prompts = [str(s).strip() for s in example_prompts]

    with hf_runner(model, dtype=dtype,
                   is_sentence_transformer=True) as hf_model:
        hf_outputs = hf_model.encode(example_prompts)

    with aphrodite_runner(model,
                     task="embed",
                     dtype=dtype,
                     max_model_len=None,
                     **aphrodite_extra_kwargs) as aphrodite_model:
        aphrodite_outputs = aphrodite_model.encode(example_prompts)

    check_embeddings_close(
        embeddings_0_lst=hf_outputs,
        embeddings_1_lst=aphrodite_outputs,
        name_0="hf",
        name_1="aphrodite",
        tol=1e-2,
    )
