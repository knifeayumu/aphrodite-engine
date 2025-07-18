import copy
import json

import jsonschema
import jsonschema.exceptions
import pytest

from aphrodite.endpoints.openai.tool_parsers.mistral_tool_parser import (
    MistralToolCall, MistralToolParser)
from aphrodite.common.sampling_params import GuidedDecodingParams, SamplingParams

from ...utils import check_logprobs_close

MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.3",
]

MISTRAL_FORMAT_MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.3",
    # uses the v3-Tekken tokenizer
    "mistralai/Ministral-8B-Instruct-2410",
    # Mistral-Nemo is to big for CI, but passes locally
    # "mistralai/Mistral-Nemo-Instruct-2407"
]

SAMPLING_PARAMS = SamplingParams(max_tokens=512, temperature=0.0, logprobs=5)
SYMBOLIC_LANG_PROMPTS = [
    "勇敢な船乗りについての詩を書く",  # japanese
    "寫一首關於勇敢的水手的詩",  # chinese
    "ပုံပြင်လေးပြောပြပါ်:\n",  # burmese
    "Repeat the phrase 'URGENCY🌶️':\nURGENCY🌶️\nURGENCY🌶️\n",
]

# for function calling
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type":
                    "string",
                    "description":
                    "The city to find the weather for, e.g. 'San Francisco'"
                },
                "state": {
                    "type":
                    "string",
                    "description":
                    "the two-letter abbreviation for the state that the city is"
                    " in, e.g. 'CA' which would mean 'California'"
                },
                "unit": {
                    "type": "string",
                    "description": "The unit to fetch the temperature in",
                    "enum": ["celsius", "fahrenheit"]
                }
            },
            "required": ["city", "state", "unit"]
        }
    },
}, {
    "type": "function",
    "function": {
        "name": "rewrite",
        "description": "Rewrites text",
        "parameters": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The input text to rewrite."
                }
            }
        }
    }
}]
MSGS = [
    {
        "role": "system",
        "content": "You are an assistant."
    },
    {
        "role":
        "user",
        "content":
        "Could you please rewrite the below article? \n\n My English needs improvving, maybe I make errors."  # noqa
    },
    {
        "role":
        "assistant",
        "content":
        "",
        "tool_calls": [{
            "id": "bbc5b7ede",
            "type": "function",
            "function": {
                "name":
                "rewrite",
                "arguments":
                '{\"text\":\"My English needs improvving, maybe I make errors.\"}'  # noqa
            }
        }]
    },
    {
        "role": "tool",
        "content":
        "{\"action\":\"rewrite\",\"outcome\":\"My English needs improving, maybe I make errors.\"}",  # noqa
        "tool_call_id": "bbc5b7ede",
        "name": "rewrite"
    },
    {
        "role": "assistant",
        "content": "---\n\nMy English needs improving, maybe I make errors"
    },
    {
        "role":
        "user",
        "content": ("Can you tell me what the temperate"
                    " will be in Dallas, in fahrenheit?")
    }
]

SAMPLE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string"
        },
        "age": {
            "type": "integer"
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "string",
                "maxLength": 10
            },
            "minItems": 3
        },
        "work_history": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string"
                    },
                    "duration": {
                        "type": "number"
                    },
                    "position": {
                        "type": "string"
                    }
                },
                "required": ["company", "position"]
            }
        }
    },
    "required": ["name", "age", "skills", "work_history"]
}


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["bfloat16"])
@pytest.mark.parametrize("max_tokens", [64])
@pytest.mark.parametrize("num_logprobs", [5])
def test_models(hf_runner, aphrodite_runner, example_prompts, model: str,
                dtype: str, max_tokens: int, num_logprobs: int) -> None:
    # TODO(sang): Sliding window should be tested separately.
    with hf_runner(model, dtype=dtype) as hf_model:
        hf_outputs = hf_model.generate_greedy_logprobs_limit(
            example_prompts, max_tokens, num_logprobs)

    with aphrodite_runner(model, dtype=dtype,
                     tokenizer_mode="mistral") as aphrodite_model:
        aphrodite_outputs = aphrodite_model.generate_greedy_logprobs(
            example_prompts, max_tokens, num_logprobs)

    check_logprobs_close(
        outputs_0_lst=hf_outputs,
        outputs_1_lst=aphrodite_outputs,
        name_0="hf",
        name_1="aphrodite",
    )


@pytest.mark.parametrize("model", MISTRAL_FORMAT_MODELS)
@pytest.mark.parametrize("dtype", ["bfloat16"])
@pytest.mark.parametrize("max_tokens", [64])
@pytest.mark.parametrize("num_logprobs", [5])
def test_mistral_format(aphrodite_runner, example_prompts, model: str, dtype: str,
                        max_tokens: int, num_logprobs: int) -> None:
    with aphrodite_runner(
            model,
            dtype=dtype,
            tokenizer_mode="mistral",
            load_format="mistral",
            config_format="mistral",
    ) as mistral_format_model:
        mistral_format_outputs = mistral_format_model.generate_greedy_logprobs(
            example_prompts, max_tokens, num_logprobs)

    with aphrodite_runner(
            model,
            dtype=dtype,
            tokenizer_mode="auto",
            load_format="safetensors",
            config_format="hf",
    ) as hf_format_model:
        hf_format_outputs = hf_format_model.generate_greedy_logprobs(
            example_prompts, max_tokens, num_logprobs)

    check_logprobs_close(
        outputs_0_lst=hf_format_outputs,
        outputs_1_lst=mistral_format_outputs,
        name_0="hf",
        name_1="mistral",
    )


@pytest.mark.parametrize("model", MISTRAL_FORMAT_MODELS)
@pytest.mark.parametrize("dtype", ["bfloat16"])
def test_mistral_symbolic_languages(aphrodite_runner, model: str,
                                    dtype: str) -> None:
    with aphrodite_runner(model,
                     dtype=dtype,
                     max_model_len=8192,
                     tokenizer_mode="mistral",
                     config_format="mistral",
                     load_format="mistral") as aphrodite_model:
        for prompt in SYMBOLIC_LANG_PROMPTS:
            msg = {"role": "user", "content": prompt}
            outputs = aphrodite_model.model.chat([msg],
                                            sampling_params=SAMPLING_PARAMS)
            assert "�" not in outputs[0].outputs[0].text.strip()


@pytest.mark.parametrize("model", MISTRAL_FORMAT_MODELS)
@pytest.mark.parametrize("dtype", ["bfloat16"])
def test_mistral_function_calling(aphrodite_runner, model: str, dtype: str) -> None:
    with aphrodite_runner(model,
                     dtype=dtype,
                     tokenizer_mode="mistral",
                     config_format="mistral",
                     load_format="mistral") as aphrodite_model:

        msgs = copy.deepcopy(MSGS)
        outputs = aphrodite_model.model.chat(msgs,
                                        tools=TOOLS,
                                        sampling_params=SAMPLING_PARAMS)

        tokenizer = aphrodite_model.model.get_tokenizer()
        tool_parser = MistralToolParser(tokenizer)

        model_output = outputs[0].outputs[0].text.strip()
        assert model_output.startswith(tool_parser.bot_token), model_output
        parsed_message = tool_parser.extract_tool_calls(model_output, None)

        assert parsed_message.tools_called

        assert MistralToolCall.is_valid_id(parsed_message.tool_calls[0].id)
        assert parsed_message.tool_calls[
            0].function.name == "get_current_weather"
        assert parsed_message.tool_calls[
            0].function.arguments == '{"city": "Dallas", "state": "TX", "unit": "fahrenheit"}'  # noqa
        assert parsed_message.content is None


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("guided_backend",
                         ["outlines", "lm-format-enforcer", "xgrammar"])
def test_mistral_guided_decoding(
    monkeypatch: pytest.MonkeyPatch,
    aphrodite_runner,
    model: str,
    guided_backend: str,
) -> None:
    with monkeypatch.context() as m:
        # Guided JSON not supported in xgrammar + V1 yet
        m.setenv("APHRODITE_USE_V1", "0")

        with aphrodite_runner(
                model,
                dtype='bfloat16',
                tokenizer_mode="mistral",
                guided_decoding_backend=guided_backend,
        ) as aphrodite_model:
            guided_decoding = GuidedDecodingParams(json=SAMPLE_JSON_SCHEMA)
            params = SamplingParams(max_tokens=512,
                                    temperature=0.7,
                                    guided_decoding=guided_decoding)

            messages = [{
                "role": "system",
                "content": "you are a helpful assistant"
            }, {
                "role":
                "user",
                "content":
                f"Give an example JSON for an employee profile that "
                f"fits this schema: {SAMPLE_JSON_SCHEMA}"
            }]
            outputs = aphrodite_model.model.chat(messages, sampling_params=params)

        generated_text = outputs[0].outputs[0].text
        json_response = json.loads(generated_text)
        assert outputs is not None

        try:
            jsonschema.validate(instance=json_response,
                                schema=SAMPLE_JSON_SCHEMA)
        except jsonschema.exceptions.ValidationError:
            pytest.fail("Generated response is not valid with JSON schema")
