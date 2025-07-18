from .data import (DecoderOnlyInputs, EmbedsInputs, EncoderDecoderInputs,
                   ExplicitEncoderDecoderPrompt, ProcessorInputs, PromptType,
                   SingletonInputs, SingletonPrompt, TextPrompt, TokenInputs,
                   TokensPrompt, build_explicit_enc_dec_prompt, embeds_inputs,
                   to_enc_dec_tuple_list, token_inputs, zip_enc_dec_prompts)
from .registry import (DummyData, InputContext, InputProcessingContext,
                       InputRegistry)

INPUT_REGISTRY = InputRegistry()
"""
The global :class:`~InputRegistry` which is used by
:class:`~aphrodite.AphroditeEngine`
to dispatch data processing according to the target model.
"""

__all__ = [
    "TextPrompt",
    "TokensPrompt",
    "PromptType",
    "SingletonPrompt",
    "ExplicitEncoderDecoderPrompt",
    "TokenInputs",
    "EmbedsInputs",
    "token_inputs",
    "embeds_inputs",
    "DecoderOnlyInputs",
    "EncoderDecoderInputs",
    "ProcessorInputs",
    "SingletonInputs",
    "build_explicit_enc_dec_prompt",
    "to_enc_dec_tuple_list",
    "zip_enc_dec_prompts",
    "INPUT_REGISTRY",
    "DummyData",
    "InputContext",
    "InputProcessingContext",
    "InputRegistry",
]
