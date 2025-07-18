import pytest

from aphrodite.processing.block.utils import (STR_NOT_IMPL_ENC_DEC_PREFIX_CACHE,
                                   STR_NOT_IMPL_ENC_DEC_SWA)
from aphrodite.processing.block_manager import SelfAttnBlockSpaceManager
from aphrodite.processing.interfaces import AllocStatus
from aphrodite.common.sequence import Logprob, SequenceStatus
from aphrodite.common.utils import chunk_list

from ..utils import (create_dummy_prompt, create_seq_group,
                     create_seq_group_encoder_decoder)


@pytest.mark.parametrize("block_size", [16])
@pytest.mark.parametrize("num_gpu_blocks", [8, 40, 80])
@pytest.mark.parametrize("num_seqs_per_group", [1, 4])
@pytest.mark.parametrize("watermark", [0.0, 0.5])
def test_can_allocate_seq_group(block_size: int, num_seqs_per_group: int,
                                num_gpu_blocks: int, watermark: float):
    block_manager = SelfAttnBlockSpaceManager(
        block_size=block_size,
        num_gpu_blocks=num_gpu_blocks,
        num_cpu_blocks=1024,
        watermark=watermark,
    )
    num_watermark_blocks = int(watermark * num_gpu_blocks)

    num_output_blocks_per_seq = 1

    # NOTE: This should be num_output_blocks_per_seq * num_seqs_per_group, but
    # the current implementation assumes all seqs are new prompts / don't have
    # different output lens.
    num_output_blocks = num_output_blocks_per_seq

    for num_prompt_blocks in range(1, num_gpu_blocks - num_output_blocks):
        seq_group = create_seq_group(
            seq_prompt_len=block_size * num_prompt_blocks,
            seq_output_lens=[
                block_size * num_output_blocks_per_seq
                for _ in range(num_seqs_per_group)
            ],
        )

        assert num_prompt_blocks + num_output_blocks <= num_gpu_blocks

        can_allocate_result = block_manager.can_allocate(seq_group)

        num_required_blocks = num_prompt_blocks + num_output_blocks

        if num_gpu_blocks - num_required_blocks < num_watermark_blocks:
            assert can_allocate_result == AllocStatus.NEVER
        elif num_gpu_blocks >= num_required_blocks:
            assert can_allocate_result == AllocStatus.OK
        else:
            assert can_allocate_result == AllocStatus.LATER


@pytest.mark.parametrize("block_size", [16])
@pytest.mark.parametrize("num_gpu_blocks", [16, 80, 160])
@pytest.mark.parametrize("num_seqs_per_group", [1, 4])
@pytest.mark.parametrize("watermark", [0.0, 0.5])
def test_can_allocate_seq_group_encoder_decoder(block_size: int,
                                                num_seqs_per_group: int,
                                                num_gpu_blocks: int,
                                                watermark: float):
    block_manager = SelfAttnBlockSpaceManager(
        block_size=block_size,
        num_gpu_blocks=num_gpu_blocks,
        num_cpu_blocks=1024,
        watermark=watermark,
    )
    num_watermark_blocks = int(watermark * num_gpu_blocks)

    num_output_blocks_per_seq = 1

    # NOTE: This should be num_output_blocks_per_seq * num_seqs_per_group, but
    # the current implementation assumes all seqs are new prompts / don't have
    # different output lens.
    num_output_blocks = num_output_blocks_per_seq

    for bdx, num_prompt_blocks in enumerate(
            range(1, num_gpu_blocks - num_output_blocks)):
        num_cross_blocks_per_seq = num_prompt_blocks

        seq_group = create_seq_group_encoder_decoder(
            seq_prompt_len=block_size * num_prompt_blocks,
            seq_output_lens=[
                block_size * num_output_blocks_per_seq
                for _ in range(num_seqs_per_group)
            ],
            request_id=str(bdx))

        assert num_prompt_blocks + num_output_blocks <= num_gpu_blocks

        can_allocate_result = block_manager.can_allocate(seq_group)

        num_required_blocks = num_prompt_blocks + \
                              num_output_blocks + \
                              num_cross_blocks_per_seq

        if num_gpu_blocks - num_required_blocks < num_watermark_blocks:
            assert can_allocate_result == AllocStatus.NEVER
        elif num_gpu_blocks >= num_required_blocks:
            assert can_allocate_result == AllocStatus.OK
        else:
            assert can_allocate_result == AllocStatus.LATER


@pytest.mark.parametrize("block_size", [16])
@pytest.mark.parametrize("num_gpu_blocks", [16])
@pytest.mark.parametrize("num_seqs_per_group", [1])
@pytest.mark.parametrize("watermark", [0.0, 0.5])
def test_can_allocate_encoder_decoder_fails_with_swa(block_size: int,
                                                     num_seqs_per_group: int,
                                                     num_gpu_blocks: int,
                                                     watermark: float):
    '''
    SWA short for Sliding Window Attention.

    At time of writing block manager does not support SWA.

    However even when SWA is implemented for block manager,
    there will still most likely be a separate workstream required
    to enable SWA for encoder/decoder models.

    Therefore this test enforces that one of the following cases
    hold true:
    1. Block manager does not support SWA at all (true at time of writing)
    2. Block manager fails with NotImplementError when SWA is enabled
       AND a SequenceGroup with an encoder sequence (i.e. in support of an
       encoder/decoder model) is passed into can_allocate() as an argument

    The setup for this test is stripped down version of
    test_can_allocate_seq_group_encoder_decoder()
    '''

    with pytest.raises((NotImplementedError, AssertionError)) as exc_info:
        block_manager = SelfAttnBlockSpaceManager(
            block_size=block_size,
            num_gpu_blocks=num_gpu_blocks,
            num_cpu_blocks=1024,
            watermark=watermark,
            sliding_window=5  # SWA
        )

        num_output_blocks_per_seq = 1
        num_prompt_blocks = 1
        num_output_blocks = num_output_blocks_per_seq
        seq_group = create_seq_group_encoder_decoder(
            seq_prompt_len=block_size * num_prompt_blocks,
            seq_output_lens=[
                block_size * num_output_blocks_per_seq
                for _ in range(num_seqs_per_group)
            ],
            request_id="0")

        assert num_prompt_blocks + num_output_blocks <= num_gpu_blocks
        block_manager.can_allocate(seq_group)

    # Assert that either
    # 1. Block manager constructor fails with assertion that sliding window
    #    is not yet supported (most likely near-term outcome at time of
    #    writing), or
    # 2. can_allocate() fails with NotImplementedError due to combination of
    #    encoder/decoder and sliding window attention
    if isinstance(exc_info.value, NotImplementedError):
        assert str(exc_info.value) == STR_NOT_IMPL_ENC_DEC_SWA
    elif isinstance(exc_info.value, AssertionError):
        assert str(exc_info.value) == "Sliding window not yet supported"


@pytest.mark.parametrize("block_size", [16])
@pytest.mark.parametrize("num_gpu_blocks", [16])
@pytest.mark.parametrize("num_seqs_per_group", [1])
@pytest.mark.parametrize("watermark", [0.0, 0.5])
def test_can_allocate_encoder_decoder_fails_with_prefix_cache(
        block_size: int, num_seqs_per_group: int, num_gpu_blocks: int,
        watermark: float):

    block_manager = SelfAttnBlockSpaceManager(
        block_size=block_size,
        num_gpu_blocks=num_gpu_blocks,
        num_cpu_blocks=1024,
        watermark=watermark,
        enable_caching=True  # Prefix cache
    )

    num_output_blocks_per_seq = 1
    num_prompt_blocks = 1
    num_output_blocks = num_output_blocks_per_seq
    seq_group = create_seq_group_encoder_decoder(
        seq_prompt_len=block_size * num_prompt_blocks,
        seq_output_lens=[
            block_size * num_output_blocks_per_seq
            for _ in range(num_seqs_per_group)
        ],
        request_id="0")

    assert num_prompt_blocks + num_output_blocks <= num_gpu_blocks

    # Assert that either can_allocate() fails with NotImplementedError
    # due to combination of encoder/decoder and prefix cache
    with pytest.raises(NotImplementedError) as exc_info:
        block_manager.can_allocate(seq_group)
    assert str(exc_info.value) == STR_NOT_IMPL_ENC_DEC_PREFIX_CACHE


@pytest.mark.parametrize("block_size", [1, 8])
@pytest.mark.parametrize("prompt_len", [1, 7, 8])
@pytest.mark.parametrize("num_slots_to_append", [1, 8, 129])
@pytest.mark.parametrize("num_lookahead_slots", [0, 10])
def test_append_slots(block_size, prompt_len, num_slots_to_append,
                      num_lookahead_slots):
    """Verify append_slots consumes the correct number of blocks from the block
    table.
    """

    num_gpu_blocks = 1024
    watermark = 0.1
    block_manager = SelfAttnBlockSpaceManager(
        block_size=block_size,
        num_gpu_blocks=num_gpu_blocks,
        num_cpu_blocks=0,
        watermark=watermark,
    )

    seq_group = create_seq_group(
        seq_prompt_len=prompt_len,
        seq_output_lens=[0],
    )

    # Allocate seq
    assert block_manager.can_allocate(seq_group)
    block_manager.allocate(seq_group)

    # Seq seq to RUNNING
    seq = seq_group.get_seqs()[0]
    seq.status = SequenceStatus.RUNNING

    # Append tokens to the sequeqnce
    for token_id in range(num_slots_to_append):
        seq.append_token_id(token_id, {token_id: Logprob(0.0)})

    # Append slots for new tokens and lookahead slots.
    free_blocks_before_append = block_manager.get_num_free_gpu_blocks()
    block_manager.append_slots(seq, num_lookahead_slots)
    num_consumed_blocks = (free_blocks_before_append -
                           block_manager.get_num_free_gpu_blocks())

    # Expect consumed blocks to be new blocks required to support the new slots.
    expected_consumed_blocks = len(
        list(
            chunk_list(
                list(
                    range(prompt_len + num_slots_to_append +
                          num_lookahead_slots)),
                block_size))) - len(
                    list(chunk_list(list(range(prompt_len)), block_size)))
    assert num_consumed_blocks == expected_consumed_blocks


@pytest.mark.parametrize("block_size", [8])
@pytest.mark.parametrize("num_cpu_blocks", [4])
@pytest.mark.parametrize("num_gpu_blocks", [4])
@pytest.mark.parametrize("num_lookahead_slots", [0, 2, 10])
@pytest.mark.parametrize("enable_caching", [False, True])
def test_swap(block_size, num_cpu_blocks, num_gpu_blocks, num_lookahead_slots,
              enable_caching):
    """Verify blocks number on src/desc device is correct after swapping in/out
        sequence group (not missing or extra blocks).
    """
    block_manager = SelfAttnBlockSpaceManager(block_size,
                                              num_cpu_blocks,
                                              num_gpu_blocks,
                                              watermark=0,
                                              enable_caching=enable_caching)
    prompt, seq_group = create_dummy_prompt("1", prompt_length=block_size - 1)
    prompt.status = SequenceStatus.WAITING
    block_manager.allocate(seq_group)

    # Emulate a forward pass by appending a single token.
    # The block manager then knows how many unprocessed
    # tokens will be written in the next forward pass.
    token_id = 0
    prompt.status = SequenceStatus.RUNNING
    prompt.append_token_id(token_id, {token_id: Logprob(0.0)})

    # Swap seq group from GPU -> CPU.
    gpu_blocks = block_manager.get_block_table(prompt)
    assert block_manager.can_swap_out(seq_group)
    before_cpu_blocks = block_manager.get_num_free_cpu_blocks()
    before_gpu_blocks = block_manager.get_num_free_gpu_blocks()
    mapping = block_manager.swap_out(seq_group)
    mapping_keys = [key for key, _ in mapping]
    assert mapping_keys == gpu_blocks
    after_cpu_blocks = block_manager.get_num_free_cpu_blocks()
    after_gpu_blocks = block_manager.get_num_free_gpu_blocks()
    assert before_cpu_blocks == after_cpu_blocks + len(gpu_blocks)
    assert before_gpu_blocks + len(gpu_blocks) == after_gpu_blocks
    prompt.status = SequenceStatus.SWAPPED

    # Swap seq group from CPU -> GPU.
    assert block_manager.can_swap_in(seq_group, num_lookahead_slots)
    before_cpu_blocks = block_manager.get_num_free_cpu_blocks()
    before_gpu_blocks = block_manager.get_num_free_gpu_blocks()
    mapping = block_manager.swap_in(seq_group)
    cpu_blocks = block_manager.get_block_table(prompt)
    mapping_keys = [key for key, _ in mapping]
    assert mapping_keys == [cpu_blocks[0]]
    after_cpu_blocks = block_manager.get_num_free_cpu_blocks()
    after_gpu_blocks = block_manager.get_num_free_gpu_blocks()
    assert before_gpu_blocks == after_gpu_blocks + len(cpu_blocks)


@pytest.mark.parametrize("block_size", [8])
@pytest.mark.parametrize("num_gpu_blocks", [4])
@pytest.mark.parametrize("num_lookahead_slots", [3, 8, 10])
@pytest.mark.parametrize("enable_caching", [True, False])
def test_can_swap(block_size, num_gpu_blocks, num_lookahead_slots,
                  enable_caching):
    """ Verify the block manager can correctly determine if a sequence group
        can be swapped in/out.
    """
    num_cpu_blocks = num_gpu_blocks
    block_manager = SelfAttnBlockSpaceManager(block_size,
                                              num_cpu_blocks,
                                              num_gpu_blocks,
                                              watermark=0,
                                              enable_caching=enable_caching)
    prompt, seq_group = create_dummy_prompt(
        "1", prompt_length=(num_gpu_blocks - 1) * block_size - 1)
    prompt.status = SequenceStatus.WAITING
    block_manager.allocate(seq_group)
    prompt.status = SequenceStatus.RUNNING

    # Swap seq group from GPU -> CPU.
    gpu_blocks = block_manager.get_block_table(prompt)
    assert block_manager.can_swap_out(seq_group)
    before_cpu_blocks = block_manager.get_num_free_cpu_blocks()
    before_gpu_blocks = block_manager.get_num_free_gpu_blocks()
    mapping = block_manager.swap_out(seq_group)
    mapping_keys = [key for key, _ in mapping]
    assert mapping_keys == gpu_blocks
    after_cpu_blocks = block_manager.get_num_free_cpu_blocks()
    after_gpu_blocks = block_manager.get_num_free_gpu_blocks()
    assert before_cpu_blocks == after_cpu_blocks + len(gpu_blocks)
    assert before_gpu_blocks + len(gpu_blocks) == after_gpu_blocks
    prompt.status = SequenceStatus.SWAPPED

    # At this moment, we still have enough free blocks to swap in the seq group.
    if num_lookahead_slots <= block_size:
        assert block_manager.can_swap_in(seq_group,
                                         num_lookahead_slots) == AllocStatus.OK
    else:
        assert block_manager.can_swap_in(
            seq_group, num_lookahead_slots) == AllocStatus.NEVER

    # During Swapped out, 2 cached blocks were evicted from the GPU,
    # so the prompt1 can't be swapped in
    prompt2_len = 2 * block_size - 1
    prompt2, seq_group2 = create_dummy_prompt(
        "2",
        prompt_length=prompt2_len,
        prompt_tokens=[10000 + i for i in range(prompt2_len)])
    prompt2.status = SequenceStatus.WAITING
    block_manager.allocate(seq_group2)

    # Swap seq group from CPU -> GPU.
    if num_lookahead_slots <= block_size:
        assert block_manager.can_swap_in(
            seq_group, num_lookahead_slots) == AllocStatus.LATER
    else:
        assert block_manager.can_swap_in(
            seq_group, num_lookahead_slots) == AllocStatus.NEVER


@pytest.mark.parametrize("num_lookahead_slots", [0, 2, 10])
@pytest.mark.parametrize("enable_caching", [False, True])
def test_swap_in_infeasible(num_lookahead_slots, enable_caching):
    """Verifies that swapping fails if there is not enough free blocks
    to account for unseen tokens and lookahead_slots.
    """
    block_size = 8
    num_cpu_blocks = 1
    num_gpu_blocks = 1
    block_manager = SelfAttnBlockSpaceManager(block_size,
                                              num_cpu_blocks,
                                              num_gpu_blocks,
                                              watermark=0,
                                              enable_caching=enable_caching)
    prompt_length = block_size - 3
    assert prompt_length > 0
    prompt, seq_group = create_dummy_prompt("1", prompt_length=prompt_length)
    prompt.status = SequenceStatus.WAITING
    block_manager.allocate(seq_group)
    # Emulate a forward pass by appending a single token.
    # The block manager then knows how many unprocessed
    # tokens will be written in the next forward pass.
    token_id = 0
    prompt.status = SequenceStatus.RUNNING
    prompt.append_token_id(token_id, {token_id: Logprob(0.0)})

    # Swap seq group from GPU -> CPU.
    assert block_manager.can_swap_out(seq_group)
    block_manager.swap_out(seq_group)
    prompt.status = SequenceStatus.SWAPPED

    # Swap seq group from CPU -> GPU.
    # The number of unseen tokens is 1. If the number of existing
    # tokens plus the unseen ones and number of lookahead slots exceeds
    # the total number of available GPU blocks then the swap
    # should fail.
    num_unseen_tokens = 1
    if (num_lookahead_slots + num_unseen_tokens +
            prompt_length) <= (block_size * num_gpu_blocks):
        assert block_manager.can_swap_in(seq_group,
                                         num_lookahead_slots) == AllocStatus.OK
    else:
        assert block_manager.can_swap_in(
            seq_group, num_lookahead_slots) == AllocStatus.NEVER


# TODO(cade/kaiyang): add comprehensive tests for swapping at allocator level.


@pytest.mark.parametrize("block_size", [8, 16])
@pytest.mark.parametrize("prompt_len", [10, 300, 1000])
@pytest.mark.parametrize("num_slots_to_append", [50])
@pytest.mark.parametrize("sliding_window", [20, 32, 200, 512])
def test_sliding_window(block_size, prompt_len, num_slots_to_append,
                        sliding_window):
    """Verify append_slots consumes the correct number of blocks from the block
    table.
    """

    num_gpu_blocks = 1024
    watermark = 0.1
    block_manager = SelfAttnBlockSpaceManager(
        block_size=block_size,
        num_gpu_blocks=num_gpu_blocks,
        num_cpu_blocks=0,
        watermark=watermark,
        sliding_window=sliding_window,
    )

    def check_used(min_n, max_n=None):
        if max_n is None:
            max_n = min_n
        used = num_gpu_blocks - block_manager.get_num_free_gpu_blocks()
        assert min_n <= used
        assert used <= max_n

    def num_blocks(num_tokens):
        return (num_tokens + block_size - 1) // block_size

    check_used(0)

    seq_group = create_seq_group(
        seq_prompt_len=prompt_len,
        seq_output_lens=[0],
    )

    check_used(0)

    # Allocate seq
    assert block_manager.can_allocate(seq_group)
    block_manager.allocate(seq_group)

    check_used(num_blocks(prompt_len))

    # Seq seq to RUNNING
    seq = seq_group.get_seqs()[0]
    seq.status = SequenceStatus.RUNNING

    seq.data.update_num_computed_tokens(prompt_len)
    check_used(num_blocks(prompt_len))

    # this is how we compute it in SelfAttnBlockSpaceManager.__init__
    sliding_blocks = (sliding_window // block_size) + 2
    # plus one block for null block
    sliding_blocks += 1

    # Append tokens to the sequeqnce
    for token_id in range(num_slots_to_append):
        seq.append_token_id(token_id, {token_id: Logprob(0.0)})
        seq.data.update_num_computed_tokens(1)
        block_manager.append_slots(seq, num_lookahead_slots=0)
        if prompt_len < sliding_window + 10:
            check_used(0, sliding_blocks + 1)
        else:
            check_used(sliding_blocks, sliding_blocks + 1)
