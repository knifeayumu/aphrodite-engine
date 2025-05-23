from typing import List
from unittest.mock import MagicMock

import pytest  # noqa

from aphrodite.common.config import CacheConfig, SchedulerConfig
from aphrodite.common.sequence import Logprob, SequenceGroup
from aphrodite.processing.scheduler import Scheduler

from .utils import create_dummy_prompt


def get_sequence_groups(scheduler_output):
    return [s.seq_group for s in scheduler_output.scheduled_seq_groups]


def append_new_token(seq_group, token_id: int):
    for seq in seq_group.get_seqs():
        seq.append_token_id(token_id, {token_id: Logprob(token_id)})


def schedule_and_update_computed_tokens(scheduler):
    metas, out, _ = scheduler.schedule()
    for s, meta in zip(out.scheduled_seq_groups, metas):
        s.seq_group.update_num_computed_tokens(meta.token_chunk_size)
    return metas, out


def test_simple():
    """Verify basic scheduling works."""
    block_size = 4
    num_seq_group = 4
    max_model_len = 16
    max_num_batched_tokens = 64
    scheduler_config = SchedulerConfig("generate",
                                       max_num_batched_tokens,
                                       num_seq_group,
                                       max_model_len,
                                       enable_chunked_prefill=True)
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 8
    cache_config.num_gpu_blocks = 8
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []

    # Add seq groups to scheduler.
    for i in range(num_seq_group):
        _, seq_group = create_dummy_prompt(str(i),
                                           prompt_length=block_size,
                                           block_size=block_size)
        scheduler.add_seq_group(seq_group)
        running.append(seq_group)

    # Schedule seq groups prompts.
    num_tokens = block_size * num_seq_group
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert set(get_sequence_groups(out)) == set(running)
    assert out.num_batched_tokens == num_tokens
    assert (not out.blocks_to_copy and not out.blocks_to_swap_in
            and not out.blocks_to_swap_out)
    assert len(seq_group_meta) == num_seq_group
    for s in running:
        append_new_token(s, 1)

    # Schedule seq groups generation.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert set(get_sequence_groups(out)) == set(running)
    assert out.num_batched_tokens == num_seq_group
    assert (not out.blocks_to_copy and not out.blocks_to_swap_in
            and not out.blocks_to_swap_out)
    assert len(seq_group_meta) == num_seq_group


def test_chunk():
    """Verify prefills are chunked properly."""
    block_size = 4
    max_seqs = 60
    max_model_len = 80
    max_num_batched_tokens = 64
    scheduler_config = SchedulerConfig(
        "generate",
        max_num_batched_tokens,
        max_seqs,
        max_model_len,
        enable_chunked_prefill=True,
    )
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 32
    cache_config.num_gpu_blocks = 32
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []

    # Add seq groups to scheduler.
    for i in range(2):
        _, seq_group = create_dummy_prompt(str(i),
                                           prompt_length=60,
                                           block_size=block_size)
        scheduler.add_seq_group(seq_group)
        running.append(seq_group)

    # Verify the second request is chunked.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    print()
    assert set(get_sequence_groups(out)) == set(running)
    assert seq_group_meta[0].token_chunk_size == 60
    # Verify it is chunked.
    assert seq_group_meta[1].token_chunk_size == 4
    assert out.num_prefill_groups == 2
    assert out.num_batched_tokens == 64
    # Only the first seq group has a new token appended.
    append_new_token(running[0], 1)

    # One chunked prefill, and one decoding.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert set(get_sequence_groups(out)) == set(running)
    # The first one is prefill. Scheduler guarantees ordering.
    assert seq_group_meta[0].token_chunk_size == 56
    # The second one is a chunked prefill.
    assert seq_group_meta[1].token_chunk_size == 1
    assert out.num_prefill_groups == 1
    assert out.num_batched_tokens == 57


def test_complex():
    block_size = 4
    max_seqs = 60
    max_model_len = 80
    max_num_batched_tokens = 64
    scheduler_config = SchedulerConfig(
        "generate",
        max_num_batched_tokens,
        max_seqs,
        max_model_len,
        enable_chunked_prefill=True,
    )
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 64
    cache_config.num_gpu_blocks = 64
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []

    # Add seq groups to scheduler.
    for i in range(2):
        _, seq_group = create_dummy_prompt(str(i),
                                           prompt_length=60,
                                           block_size=block_size)
        scheduler.add_seq_group(seq_group)
        running.append(seq_group)
        assert seq_group.is_prefill()

    # Verify the second request is chunked.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)

    assert set(get_sequence_groups(out)) == set(running)
    assert seq_group_meta[0].token_chunk_size == 60
    # Verify it is chunked.
    assert seq_group_meta[1].token_chunk_size == 4
    assert not running[0].is_prefill()
    assert running[1].is_prefill()
    assert out.num_prefill_groups == 2
    assert out.num_batched_tokens == 64
    # Only the first seq group has a new token appended.
    append_new_token(running[0], 1)

    # Add 2 more requests.
    for i in range(2, 4):
        _, seq_group = create_dummy_prompt(str(i),
                                           prompt_length=60,
                                           block_size=block_size)
        scheduler.add_seq_group(seq_group)
        running.append(seq_group)

    # Decoding & chunked prefill & first chunk of 3rd request is scheduled.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(get_sequence_groups(out)) == 3
    # The first one is the first chunked prefill.
    assert seq_group_meta[0].token_chunk_size == 7
    # The second one is the second new chunked prefill.
    assert seq_group_meta[1].token_chunk_size == 56
    # The last one is decode.
    assert seq_group_meta[2].token_chunk_size == 1
    # Two of them are in chunked prefill.
    assert out.num_prefill_groups == 2
    assert out.num_batched_tokens == 64
    # The first 2 requests are now in decodine phase.
    append_new_token(running[0], 1)
    assert not running[0].is_prefill()
    append_new_token(running[1], 1)
    assert not running[1].is_prefill()
    # The third request is still in prefill stage.
    assert running[2].is_prefill()


def test_maximal_decoding():
    """Verify decoding requests are prioritized."""
    block_size = 4
    max_seqs = 2
    max_model_len = 8
    max_num_batched_tokens = 2
    scheduler_config = SchedulerConfig(
        "generate",
        max_num_batched_tokens,
        max_seqs,
        max_model_len,
        enable_chunked_prefill=True,
    )
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 8
    cache_config.num_gpu_blocks = 8
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []

    # Add seq groups to scheduler.
    for i in range(2):
        _, seq_group = create_dummy_prompt(str(i),
                                           prompt_length=2,
                                           block_size=block_size)
        scheduler.add_seq_group(seq_group)
        running.append(seq_group)
        assert seq_group.is_prefill()

    # The first prefill is scheduled.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(get_sequence_groups(out)) == 1
    assert seq_group_meta[0].token_chunk_size == 2
    assert not running[0].is_prefill()
    assert running[1].is_prefill()
    assert out.num_prefill_groups == 1
    assert out.num_batched_tokens == 2
    # Only the first seq group has a new token appended.
    append_new_token(running[0], 1)

    # Create one more seq_group.
    _, seq_group = create_dummy_prompt("3",
                                       prompt_length=2,
                                       block_size=block_size)
    scheduler.add_seq_group(seq_group)
    running.append(seq_group)
    assert seq_group.is_prefill()
    # The first decoding + second chunk is scheduled.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(get_sequence_groups(out)) == 2
    assert seq_group_meta[0].token_chunk_size == 1
    assert seq_group_meta[1].token_chunk_size == 1
    assert not running[0].is_prefill()
    assert running[1].is_prefill()
    assert running[2].is_prefill()
    assert out.num_prefill_groups == 1
    assert out.num_batched_tokens == 2
    append_new_token(running[0], 1)

    # Decoding + running prefill is prioritized.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(get_sequence_groups(out)) == 2
    assert seq_group_meta[0].token_chunk_size == 1
    assert seq_group_meta[1].token_chunk_size == 1
    assert not running[0].is_prefill()
    assert not running[1].is_prefill()
    assert out.num_prefill_groups == 1
    assert out.num_batched_tokens == 2
    append_new_token(running[0], 1)
    append_new_token(running[1], 1)

    # Only decoding is prioritized.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(get_sequence_groups(out)) == 2
    assert seq_group_meta[0].token_chunk_size == 1
    assert seq_group_meta[1].token_chunk_size == 1
    assert not running[0].is_prefill()
    assert not running[1].is_prefill()
    assert out.num_prefill_groups == 0
    assert out.num_batched_tokens == 2
    append_new_token(running[0], 1)
    append_new_token(running[1], 1)

    # After aborting the decoding request, the fcfs new prefill is prioritized.
    scheduler.abort_seq_group(running[0].request_id)
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(get_sequence_groups(out)) == 2
    assert seq_group_meta[0].token_chunk_size == 1
    assert seq_group_meta[1].token_chunk_size == 1
    assert not running[1].is_prefill()
    assert running[2].is_prefill()
    assert out.num_prefill_groups == 1
    assert out.num_batched_tokens == 2


def test_prompt_limit():
    """Verify max_num_batched_tokens < max_model_len is possible."""
    block_size = 4
    max_seqs = 32
    max_model_len = 64
    max_num_batched_tokens = 32
    scheduler_config = SchedulerConfig(
        "generate",
        max_num_batched_tokens,
        max_seqs,
        max_model_len,
        enable_chunked_prefill=True,
    )
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 16
    cache_config.num_gpu_blocks = 16
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []

    _, seq_group = create_dummy_prompt("1",
                                       prompt_length=48,
                                       block_size=block_size)
    scheduler.add_seq_group(seq_group)
    running.append(seq_group)
    assert seq_group.is_prefill()

    # The prompt length > max_num_batched_tokens should be still scheduled.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(get_sequence_groups(out)) == 1
    assert seq_group_meta[0].token_chunk_size == 32
    assert running[0].is_prefill()
    assert out.num_prefill_groups == 1
    assert out.num_batched_tokens == 32


def test_prompt_limit_exceed():
    block_size = 4
    max_seqs = 64
    max_model_len = 32
    max_num_batched_tokens = 64
    scheduler_config = SchedulerConfig("generate",
                                       max_num_batched_tokens,
                                       max_seqs,
                                       max_model_len,
                                       enable_chunked_prefill=True)
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 16
    cache_config.num_gpu_blocks = 16
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []
    _, seq_group = create_dummy_prompt("2",
                                       prompt_length=48,
                                       block_size=block_size)
    scheduler.add_seq_group(seq_group)
    running.append(seq_group)
    assert seq_group.is_prefill()
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert len(out.ignored_seq_groups) == 1
    assert out.ignored_seq_groups[0] == seq_group


def test_chunked_prefill_preempt():
    """Verify preempt works with chunked prefill requests"""
    block_size = 4
    max_seqs = 30
    max_model_len = 200
    max_num_batched_tokens = 30
    scheduler_config = SchedulerConfig(
        "generate",
        max_num_batched_tokens,
        max_seqs,
        max_model_len,
        enable_chunked_prefill=True,
    )
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 16
    cache_config.num_gpu_blocks = 16
    scheduler = Scheduler(scheduler_config, cache_config, None)

    _, seq_group = create_dummy_prompt("1",
                                       prompt_length=60,
                                       block_size=block_size)
    scheduler.add_seq_group(seq_group)
    _, out = schedule_and_update_computed_tokens(scheduler)
    # The request is chunked.
    # prefill scheduled now.
    assert len(out.scheduled_seq_groups) == 1
    assert out.num_prefill_groups == 1
    assert seq_group.is_prefill()
    assert out.num_batched_tokens == max_num_batched_tokens

    # The request should be preempted.
    scheduler.block_manager.can_append_slots = MagicMock()

    def cannot_append_second_group1(seq_group, num_lookahead_slots):
        return seq_group.request_id != "1"

    scheduler.block_manager.can_append_slots.side_effect = (
        cannot_append_second_group1)

    # The running prefill is now preempted.
    _, out = schedule_and_update_computed_tokens(scheduler)
    assert len(out.scheduled_seq_groups) == 0
    assert out.num_batched_tokens == 0
    assert out.blocks_to_swap_out == []
    assert out.blocks_to_swap_in == []

    # Make sure we can reschedule preempted request.
    _, out = schedule_and_update_computed_tokens(scheduler)
    assert len(out.scheduled_seq_groups) == 1
    assert out.num_prefill_groups == 1
    assert seq_group.is_prefill()
    assert out.num_batched_tokens == max_num_batched_tokens
    assert seq_group.get_num_uncomputed_tokens() == 30

    # We should be able to run prefill twice as it is chunked.
    def cannot_append_second_group2(seq_group, num_lookahead_slots):
        return True

    scheduler.block_manager.can_append_slots.side_effect = (
        cannot_append_second_group2)
    _, out = schedule_and_update_computed_tokens(scheduler)
    assert len(out.scheduled_seq_groups) == 1
    assert out.num_prefill_groups == 1
    assert not seq_group.is_prefill()
    assert out.num_batched_tokens == max_num_batched_tokens


def test_chunked_prefill_max_seqs():
    block_size = 4
    max_seqs = 2
    max_model_len = 80
    max_num_batched_tokens = 64
    scheduler_config = SchedulerConfig(
        "generate",
        max_num_batched_tokens,
        max_seqs,
        max_model_len,
        enable_chunked_prefill=True,
    )
    cache_config = CacheConfig(block_size, 1.0, 1, "auto")
    cache_config.num_cpu_blocks = 128
    cache_config.num_gpu_blocks = 128
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []

    _, seq_group = create_dummy_prompt("1",
                                       prompt_length=65,
                                       block_size=block_size)
    scheduler.add_seq_group(seq_group)
    running.append(seq_group)
    # The first prefill is chunked.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert seq_group_meta[0].token_chunk_size == max_num_batched_tokens
    assert len(get_sequence_groups(out)) == 1

    # Add new requests.
    for i in range(4):
        _, seq_group = create_dummy_prompt(str(i),
                                           prompt_length=65,
                                           block_size=block_size)
        scheduler.add_seq_group(seq_group)
        running.append(seq_group)

    # Make sure only 2 requests are scheduled.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert out.num_batched_tokens == max_num_batched_tokens
    assert len(get_sequence_groups(out)) == 2
    assert not running[0].is_prefill()
    assert running[1].is_prefill()
    append_new_token(running[0], 1)

    # Although we have enough token budget, we can only schedule max_seqs.
    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert seq_group_meta[0].token_chunk_size == 2
    assert seq_group_meta[1].token_chunk_size == 1
    assert out.num_batched_tokens == 3
    assert len(get_sequence_groups(out)) == max_seqs
    assert not running[0].is_prefill()
    assert not running[1].is_prefill()


def test_perfix_caching():
    """Verify allocating full blocks when prefix caching is enabled."""
    block_size = 4
    max_seqs = 10
    max_model_len = 80
    max_num_batched_tokens = 64
    scheduler_config = SchedulerConfig(
        "generate",
        max_num_batched_tokens,
        max_seqs,
        max_model_len,
        enable_chunked_prefill=True,
    )
    cache_config = CacheConfig(block_size,
                               1.0,
                               1,
                               "auto",
                               enable_prefix_caching=True)
    cache_config.num_cpu_blocks = 0
    cache_config.num_gpu_blocks = 32
    scheduler = Scheduler(scheduler_config, cache_config, None)
    running: List[SequenceGroup] = []

    # Add seq groups to scheduler.
    for i in range(2):
        _, seq_group = create_dummy_prompt(str(i),
                                           block_size=block_size,
                                           prompt_length=50)
        scheduler.add_seq_group(seq_group)
        running.append(seq_group)

    seq_group_meta, out = schedule_and_update_computed_tokens(scheduler)
    assert set(get_sequence_groups(out)) == set(running)
    assert seq_group_meta[0].token_chunk_size == 50
    # Verify it is chunked. Note that although the budget is 64-50=14,
    # we only allocate full blocks for prefix caching, so only 4*(14//4)=12
    # tokens are allocated.
    assert seq_group_meta[1].token_chunk_size == 12
    assert out.num_prefill_groups == 2
    assert out.num_batched_tokens == 62
