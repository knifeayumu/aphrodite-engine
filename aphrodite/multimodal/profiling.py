from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Generic, NamedTuple, Optional, TypeVar, cast

import numpy as np
import numpy.typing as npt
from loguru import logger
from PIL import Image

import aphrodite.common.envs as envs
from aphrodite.common.logger import log_once

from .inputs import (MultiModalDataDict, MultiModalEncDecInputs,
                     MultiModalInputs, MultiModalKwargs,
                     MultiModalPlaceholderDict)
from .processing import (BaseMultiModalProcessor, BaseProcessingInfo,
                         EncDecMultiModalProcessor)


@dataclass
class ProcessorInputs:
    """
    Represents the keyword arguments to
    :meth:`aphrodite.multimodal.processing.BaseMultiModalProcessor.apply`.
    """
    prompt_text: str
    mm_data: MultiModalDataDict
    hf_processor_mm_kwargs: Mapping[str, object] = field(default_factory=dict)


class DummyEncoderData(NamedTuple):
    """Dummy data used for profiling."""

    prompt_token_ids: list[int]


class DummyDecoderData(NamedTuple):
    """Dummy data used for profiling."""

    prompt_token_ids: list[int]
    multi_modal_data: MultiModalKwargs
    multi_modal_placeholders: MultiModalPlaceholderDict


_I = TypeVar("_I", bound=BaseProcessingInfo)


class BaseDummyInputsBuilder(ABC, Generic[_I]):
    """
    Abstract base class that constructs the dummy data to profile
    multi-modal models.
    """

    def __init__(self, info: _I) -> None:
        super().__init__()

        self.info = info

    # TODO: @abstractmethod after transition
    def get_dummy_text(self, mm_counts: Mapping[str, int]) -> str:
        """
        Build the text input corresponding to :code:`mm_counts`.
        """
        if (type(self).get_dummy_processor_inputs ==
                BaseDummyInputsBuilder.get_dummy_processor_inputs):
            raise NotImplementedError

        log_once("WARNING", "`get_dummy_processor_inputs` has been split up "
                            "into `get_dummy_text` and `get_dummy_mm_data`. "
                            "These two methods will be marked as abstract "
                            "in an upcoming release.")

        seq_len = self.info.ctx.model_config.max_model_len
        return self.get_dummy_processor_inputs(seq_len, mm_counts).prompt_text

    # TODO: @abstractmethod after transition
    def get_dummy_mm_data(
        self,
        seq_len: int,
        mm_counts: Mapping[str, int],
    ) -> MultiModalDataDict:
        """
        Build the multimodal input which, after processing, results in
        the maximum possible number of placeholder tokens.
        """
        raise NotImplementedError

    def get_dummy_processor_inputs(
        self,
        seq_len: int,
        mm_counts: Mapping[str, int],
    ) -> ProcessorInputs:
        """
        Build the input which, after processing, results in
        the maximum possible number of placeholder tokens.
        """
        dummy_text = self.get_dummy_text(mm_counts)
        dummy_mm_data = self.get_dummy_mm_data(seq_len, mm_counts)

        return ProcessorInputs(prompt_text=dummy_text, mm_data=dummy_mm_data)

    def _get_dummy_audios(
        self,
        *,
        length: int,
        num_audios: int,
    ) -> list[npt.NDArray]:
        if num_audios == 0:
            return []
        audio = np.zeros((length, ))
        return [audio] * num_audios

    def _get_dummy_images(
        self,
        *,
        width: int,
        height: int,
        num_images: int,
    ) -> list[Image.Image]:
        if num_images == 0:
            return []
        image = Image.new("RGB", (width, height), color=255)
        return [image] * num_images

    def _get_dummy_videos(
        self,
        *,
        width: int,
        height: int,
        num_frames: int,
        num_videos: int,
    ) -> list[npt.NDArray]:
        if num_videos == 0:
            return []
        video = np.full((num_frames, width, height, 3), 255)
        return [video] * num_videos


class MultiModalProfiler(Generic[_I]):
    """
    Contains code for running memory profiling for multi-modal models.
    """

    def __init__(
        self,
        processor: BaseMultiModalProcessor[_I],
    ) -> None:
        super().__init__()

        self.processor = processor

    @property
    def processing_info(self) -> BaseProcessingInfo:
        return self.processor.info

    @property
    def dummy_inputs(self) -> BaseDummyInputsBuilder[_I]:
        return self.processor.dummy_inputs

    def get_mm_limits(self) -> Mapping[str, int]:
        return self.processing_info.get_allowed_mm_limits()

    def _get_dummy_mm_inputs(
        self,
        seq_len: int,
        mm_counts: Optional[Mapping[str, int]] = None,
    ) -> MultiModalInputs:
        if mm_counts is None:
            mm_counts = self.get_mm_limits()

        factory = self.dummy_inputs
        processor_inputs = factory.get_dummy_processor_inputs(
            seq_len, mm_counts)

        return self.processor.apply(
            prompt=processor_inputs.prompt_text,
            mm_data=processor_inputs.mm_data,
            hf_processor_mm_kwargs=processor_inputs.hf_processor_mm_kwargs,
        )

    def _get_mm_num_tokens(
        self,
        mm_inputs: MultiModalInputs,
    ) -> Mapping[str, int]:
        placeholders_by_modality = mm_inputs["mm_placeholders"]

        return {
            modality: sum(item.get_num_embeds() for item in placeholders)
            for modality, placeholders in placeholders_by_modality.items()
        }

    def get_encoder_dummy_data(
        self,
        seq_len: int,
        mm_counts: Optional[Mapping[str, int]] = None,
    ) -> DummyEncoderData:
        mm_inputs = self._get_dummy_mm_inputs(seq_len, mm_counts)
        mm_inputs = cast(MultiModalEncDecInputs, mm_inputs)

        # For encoder-decoder models, use encoder prompt token ids instead of
        # decoder prompt to construct dummy seq_data for encoder profiling.
        encoder_prompt_token_ids = mm_inputs["encoder_prompt_token_ids"]

        total_len = len(encoder_prompt_token_ids)

        processor = cast(EncDecMultiModalProcessor, self.processor)
        if processor.pad_dummy_encoder_prompt:
            num_tokens_to_pad = max(total_len, seq_len) - total_len
            encoder_prompt_token_ids.extend([0] * num_tokens_to_pad)
        # NOTE: Whisper allows total_len > seq_len.
        elif total_len > seq_len and not envs.APHRODITE_USE_V1:
            # `max_num_batched_tokens` is defined by `SchedulerConfig`
            log_once(
                "WARNING",
                "The encoder sequence length used for profiling (max_num_batched_tokens / max_num_seqs = {}) "  # noqa: E501
                "is too short to hold the multi-modal embeddings in the worst case ({} tokens in total, out of which {} are reserved for multi-modal embeddings). "  # noqa: E501
                "This may cause certain multi-modal inputs to fail during inference, even when the input text is short. "  # noqa: E501
                "To avoid this, you should increase `max_model_len`, reduce `max_num_seqs`, and/or reduce `mm_counts`.",  # noqa: E501
                seq_len,
                total_len,
                str(self._get_mm_num_tokens(mm_inputs)),
            )

        return DummyEncoderData(encoder_prompt_token_ids)

    def get_decoder_dummy_data(
        self,
        seq_len: int,
        mm_counts: Optional[Mapping[str, int]] = None,
    ) -> DummyDecoderData:
        mm_inputs = self._get_dummy_mm_inputs(seq_len, mm_counts)

        prompt_token_ids = mm_inputs["prompt_token_ids"]
        total_len = len(prompt_token_ids)

        # V0 does not support chunked prefill.
        if total_len > seq_len and not envs.APHRODITE_USE_V1:
            # `max_num_batched_tokens` is defined by `SchedulerConfig`
            log_once(
                "WARNING",
                "The sequence length used for profiling (max_num_batched_tokens / max_num_seqs = {}) "  # noqa: E501
                "is too short to hold the multi-modal embeddings in the worst case ({} tokens in total, out of which {} are reserved for multi-modal embeddings). "  # noqa: E501
                "This may cause certain multi-modal inputs to fail during inference, even when the input text is short. "  # noqa: E501
                "To avoid this, you should increase `max_model_len`, reduce `max_num_seqs`, and/or reduce `mm_counts`.",  # noqa: E501
                seq_len,
                total_len,
                str(self._get_mm_num_tokens(mm_inputs)),
            )

        if total_len < seq_len:
            prompt_token_ids.extend([0] * (seq_len - total_len))

        return DummyDecoderData(
            prompt_token_ids=prompt_token_ids,
            multi_modal_data=mm_inputs["mm_kwargs"],
            multi_modal_placeholders=mm_inputs["mm_placeholders"],
        )

    def get_mm_max_tokens(
        self,
        seq_len: int,
        mm_counts: Optional[Mapping[str, int]] = None,
    ) -> Mapping[str, int]:
        mm_inputs = self._get_dummy_mm_inputs(seq_len, mm_counts)

        return self._get_mm_num_tokens(mm_inputs)
