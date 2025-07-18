import math

import pytest

from aphrodite import PoolingParams

from ...utils import check_embeddings_close, matryoshka_fy

SCORING_MODELS = [
    "jinaai/jina-reranker-v2-base-multilingual",  # Roberta
]

TEXTS_1 = ["Organic skincare products for sensitive skin"]

TEXTS_2 = [
    "Organic skincare for sensitive skin with aloe vera and chamomile.",
    "New makeup trends focus on bold colors and innovative techniques",
    "Bio-Hautpflege für empfindliche Haut mit Aloe Vera und Kamille",
    "Neue Make-up-Trends setzen auf kräftige Farben und innovative Techniken",  # noqa: E501
    "Cuidado de la piel orgánico para piel sensible con aloe vera y manzanilla",  # noqa: E501
    "Las nuevas tendencias de maquillaje se centran en colores vivos y técnicas innovadoras",  # noqa: E501
    "针对敏感肌专门设计的天然有机护肤产品",
    "新的化妆趋势注重鲜艳的颜色和创新的技巧",
    "敏感肌のために特別に設計された天然有機スキンケア製品",
    "新しいメイクのトレンドは鮮やかな色と革新的な技術に焦点を当てています",
]

EMBEDDING_MODELS = [
    "jinaai/jina-embeddings-v3",
]

EMBEDDING_PROMPTS = [
    "Follow the white rabbit.",  # English
    "Sigue al conejo blanco.",  # Spanish
    "Suis le lapin blanc.",  # French
    "跟着白兔走。",  # Chinese
    "اتبع الأرنب الأبيض.",  # Arabic
    "Folge dem weißen Kaninchen.",  # German
]


@pytest.fixture(scope="module", params=SCORING_MODELS)
def model_name(request):
    yield request.param


@pytest.mark.parametrize("dtype", ["half"])
def test_llm_1_to_1(aphrodite_runner, hf_runner, model_name, dtype: str):

    text_pair = [TEXTS_1[0], TEXTS_2[0]]

    with hf_runner(model_name, dtype=dtype, is_cross_encoder=True) as hf_model:
        hf_outputs = hf_model.predict([text_pair]).tolist()

    with aphrodite_runner(model_name, task="score", dtype=dtype,
                     max_model_len=None) as aphrodite_model:
        aphrodite_outputs = aphrodite_model.score(text_pair[0], text_pair[1])

    assert len(aphrodite_outputs) == 1
    assert len(hf_outputs) == 1

    assert math.isclose(hf_outputs[0], aphrodite_outputs[0], rel_tol=0.01)


@pytest.mark.parametrize("dtype", ["half"])
def test_llm_1_to_N(aphrodite_runner, hf_runner, model_name, dtype: str):

    text_pairs = [[TEXTS_1[0], text] for text in TEXTS_2]

    with hf_runner(model_name, dtype=dtype, is_cross_encoder=True) as hf_model:
        hf_outputs = hf_model.predict(text_pairs).tolist()

    with aphrodite_runner(model_name, task="score", dtype=dtype,
                     max_model_len=None) as aphrodite_model:
        aphrodite_outputs = aphrodite_model.score(TEXTS_1[0], TEXTS_2)

    assert len(aphrodite_outputs) == 10
    assert len(hf_outputs) == 10

    assert math.isclose(hf_outputs[0], aphrodite_outputs[0], rel_tol=0.01)
    assert math.isclose(hf_outputs[1], aphrodite_outputs[1], rel_tol=0.01)


@pytest.fixture(scope="module", params=EMBEDDING_MODELS)
def emb_model_name(request):
    yield request.param


def test_is_matryoshka(aphrodite_runner, emb_model_name):
    with aphrodite_runner(emb_model_name, task="embed",
                     max_model_len=None) as aphrodite_model:
        assert aphrodite_model.model.llm_engine.model_config.is_matryoshka


@pytest.mark.parametrize("model", EMBEDDING_MODELS)
@pytest.mark.parametrize("dtype", ["half"])
def test_embeddings(
    hf_runner,
    aphrodite_runner,
    model,
    dtype: str,
    monkeypatch,
) -> None:

    example_prompts = EMBEDDING_PROMPTS

    with hf_runner(
            model,
            dtype=dtype,
            is_sentence_transformer=True,
    ) as hf_model:
        hf_outputs = hf_model.encode(example_prompts, task="text-matching")

    with aphrodite_runner(model, task="embed", dtype=dtype,
                     max_model_len=None) as aphrodite_model:
        aphrodite_outputs = aphrodite_model.encode(example_prompts)

    check_embeddings_close(
        embeddings_0_lst=hf_outputs,
        embeddings_1_lst=aphrodite_outputs,
        name_0="hf",
        name_1="aphrodite",
        tol=1e-2,
    )


@pytest.mark.parametrize("model", EMBEDDING_MODELS)
@pytest.mark.parametrize("dtype", ["half"])
@pytest.mark.parametrize("dimensions", [16, 32])
def test_matryoshka(
    hf_runner,
    aphrodite_runner,
    model,
    dtype: str,
    dimensions: int,
    monkeypatch,
) -> None:

    example_prompts = EMBEDDING_PROMPTS

    with hf_runner(
            model,
            dtype=dtype,
            is_sentence_transformer=True,
    ) as hf_model:
        hf_outputs = hf_model.encode(example_prompts, task="text-matching")
        hf_outputs = matryoshka_fy(hf_outputs, dimensions)

    with aphrodite_runner(model, task="embed", dtype=dtype,
                     max_model_len=None) as aphrodite_model:
        matryoshka_dimensions = (
            aphrodite_model.model.llm_engine.model_config.matryoshka_dimensions)
        assert matryoshka_dimensions is not None

        if dimensions not in matryoshka_dimensions:
            with pytest.raises(ValueError):
                aphrodite_model.encode(
                    example_prompts,
                    pooling_params=PoolingParams(dimensions=dimensions))
        else:
            aphrodite_outputs = aphrodite_model.encode(
                example_prompts,
                pooling_params=PoolingParams(dimensions=dimensions))

            check_embeddings_close(
                embeddings_0_lst=hf_outputs,
                embeddings_1_lst=aphrodite_outputs,
                name_0="hf",
                name_1="aphrodite",
                tol=1e-2,
            )
