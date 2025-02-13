import functools
from typing import Callable, List

from transformers import PreTrainedTokenizer

from aphrodite.common.logger import log_once
from aphrodite.common.sampling_params import SamplingParams
from aphrodite.common.sequence import (APHRODITE_INVALID_TOKEN_ID, Sequence,
                                       SequenceGroup, SequenceGroupOutput,
                                       SequenceOutput, SequenceStatus)
from aphrodite.common.utils import Counter
from aphrodite.engine.output_processor.interfaces import (
    SequenceGroupOutputProcessor)
from aphrodite.engine.output_processor.single_step import (
    single_step_process_prompt_logprob)
from aphrodite.engine.output_processor.stop_checker import StopChecker
from aphrodite.processing.scheduler import Scheduler
from aphrodite.transformers_utils.detokenizer import Detokenizer


class MultiStepOutputProcessor(SequenceGroupOutputProcessor):
    """SequenceGroupOutputProcessor which handles logic related to
    detokenization and stopping conditions. It specializes to "multi-step
    decoding", where Aphrodite's worker may generate multiple tokens per
    invocation. This is currently mutually exclusive with advanced sampling
    techniques like beam search, which motivates the separation of this logic
    from the single step output processor.

    This class is responsible for things such as correctly appending all new
    token ids to their sequence, detokenizing new token ids, truncating new
    output tokens after an eos token, and correctly handling the case where the
    number of new output tokens per sequence differs in a single batch.
    """

    def __init__(
        self,
        detokenizer: Detokenizer,
        scheduler: List[Scheduler],
        seq_counter: Counter,
        get_tokenizer_for_seq: Callable[[Sequence], PreTrainedTokenizer],
        stop_checker: StopChecker,
    ):
        self.detokenizer = detokenizer
        self.scheduler = scheduler
        self.seq_counter = seq_counter
        self.get_tokenizer_for_seq = get_tokenizer_for_seq
        self.stop_checker = stop_checker

    def process_prompt_logprob(self, seq_group: SequenceGroup,
                               outputs: List[SequenceGroupOutput]) -> None:
        """Process prompt logprobs associated with each step of a multi-step-
        scheduled computation.
        Args:
          seq_group: the outputs are associated with this :class:`SequenceGroup`
          outputs: the :class:`SequenceGroupOutput`s for all scheduler steps
        """
        for output in outputs:
            # Concatenate single-step prompt logprob processing results.
            single_step_process_prompt_logprob(self, seq_group, output)

    @staticmethod
    @functools.lru_cache()
    def _log_prompt_logprob_unsupported_warning_once():
        log_once(
            level="WARNING",
            message="Prompt logprob is not supported by multi step workers. "
            "(e.g., speculative decode uses multi step workers).")

    def process_outputs(self,
                        sequence_group: SequenceGroup,
                        outputs: List[SequenceGroupOutput],
                        is_async: bool = False) -> None:
        """Append new tokens in the outputs to sequences in the sequence group.

        This only supports sequence groups of size 1. It supports greater than
        one new token per sequence.

        This applies logic like stop condition checking and detokenization.
        It also handles cases where there are tokens emitted after
        the EOS token.
        is_async - Indicates whether this postprocessor runs in
            parallel with the GPU forward pass and is processing
            tokens from the previous step. If this is true, then
            no tokens need to be appended since it is already done
            externally (before the next schedule() call)
        """

        # Sequences can be in RUNNING or FINISHED_ABORTED state
        # once scheduled, as a sequence is moved to FINSIHED_ABORTED
        # if a client disconnects from the api server.
        seqs = sequence_group.get_seqs(status=SequenceStatus.RUNNING)
        if seqs is None:
            seqs = sequence_group.get_seqs(
                status=SequenceStatus.FINISHED_ABORTED)

        assert seqs, "Expected RUNNING or FINISHED_ABORTED sequences"
        assert len(seqs) == 1, (
            "Beam search not supported in multi-step decoding.")
        seq = seqs[0]

        if is_async:
            # Async case: We process tokens one by one. Here, we know the token
            # was already appended, so we only need to do the rest of the
            # postprocessor: Detokenization + stopping logic
            self._process_decode_and_stop(seq, sequence_group.sampling_params)
        else:
            # Standard multi-step case
            # Since there's only one sequence per sequence group,
            # we can take the first sample.
            samples = [output.samples[0] for output in outputs]

            # entries in sample tokens may be invalid (eg. due to spec decode
            # rejecting tokens).
            valid_samples = [
                sample for sample in samples
                if sample.output_token != APHRODITE_INVALID_TOKEN_ID
            ]
            assert valid_samples

            self._process_seq_outputs(seq, valid_samples,
                                      sequence_group.sampling_params)
    def _process_decode_and_stop(self, seq: Sequence,
                                 sampling_params: SamplingParams) -> None:
        new_char_count = 0
        if sampling_params.detokenize:
            new_char_count = self.detokenizer.decode_sequence_inplace(
                seq, sampling_params)
        # TODO(sang): Support lora.
        self.stop_checker.maybe_stop_sequence(
            seq,
            new_char_count=new_char_count,
            sampling_params=sampling_params,
        )

    def _process_seq_outputs(self, seq: Sequence,
                             valid_samples: List[SequenceOutput],
                             sampling_params: SamplingParams) -> None:
        output_token_ids = [sample.output_token for sample in valid_samples]
        output_logprobs = [sample.logprobs for sample in valid_samples]

        # Truncate to max_tokens if necessary.
        remaining_tokens = sampling_params.max_tokens - (seq.get_output_len() +
                                                         len(output_token_ids))
        if remaining_tokens < 0:
            valid_samples = valid_samples[:remaining_tokens]
            output_token_ids = output_token_ids[:remaining_tokens]

        # Truncate any tokens after EOS. This is required as spec decode
        # generates a fixed number of tokens without evaluating stopping
        # conditions within the block. This can cause an eos token to be
        # unintentionally ignored.
        if not sampling_params.ignore_eos:
            eos_token_id = self.get_tokenizer_for_seq(seq).eos_token_id
            # Avoiding .index calls as exception throwing in the happy path
            # is expensive.
            for i in range(len(output_token_ids)):
                if output_token_ids[i] == eos_token_id:
                    output_token_ids = output_token_ids[:i + 1]
                    valid_samples = valid_samples[:i + 1]
                    break

        # Incrementally append tokens to the sequence, as if we had only one new
        # token.
        for output_token_id, output_logprob in zip(output_token_ids,
                                                   output_logprobs):
            seq.append_token_id(
                token_id=output_token_id,
                logprobs=output_logprob,
            )

            self._process_decode_and_stop(seq, sampling_params)

            if seq.is_finished():
                break
