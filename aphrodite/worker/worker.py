"""A GPU worker class."""
import gc
import os
from typing import Dict, List, Optional, Set, Tuple, Type, Union

import torch
import torch.distributed
from loguru import logger

from aphrodite.common.config import (CacheConfig, DeviceConfig, LoadConfig,
                                     LoRAConfig, ModelConfig, ParallelConfig,
                                     PromptAdapterConfig, SchedulerConfig,
                                     SpeculativeConfig)
from aphrodite.common.sequence import (ExecuteModelRequest,
                                       IntermediateTensors,
                                       SequenceGroupMetadata,
                                       SequenceGroupMetadataDelta)
from aphrodite.common.utils import GiB_bytes, memory_profiling
from aphrodite.distributed import (ensure_model_parallel_initialized,
                                   get_tensor_model_parallel_rank,
                                   init_distributed_environment,
                                   set_custom_all_reduce)
from aphrodite.lora.request import LoRARequest
from aphrodite.modeling import set_random_seed
from aphrodite.modeling.layers.sampler import SamplerOutput
from aphrodite.modeling.model_loader.tensorizer import TensorizerConfig
from aphrodite.platforms import current_platform
from aphrodite.prompt_adapter.request import PromptAdapterRequest
from aphrodite.worker.cache_engine import CacheEngine
from aphrodite.worker.embedding_model_runner import EmbeddingModelRunner
from aphrodite.worker.enc_dec_model_runner import EncoderDecoderModelRunner
from aphrodite.worker.model_runner import GPUModelRunnerBase, ModelRunner
from aphrodite.worker.worker_base import (LocalOrDistributedWorkerBase,
                                          WorkerInput)


class Worker(LocalOrDistributedWorkerBase):
    """A worker class that executes (a partition of) the model on a GPU.

    Each worker is associated with a single GPU. The worker is responsible for
    maintaining the KV cache and executing the model on the GPU. In case of
    distributed inference, each worker is assigned a partition of the model.
    """

    def __init__(
        self,
        model_config: ModelConfig,
        parallel_config: ParallelConfig,
        scheduler_config: SchedulerConfig,
        device_config: DeviceConfig,
        cache_config: CacheConfig,
        load_config: LoadConfig,
        local_rank: int,
        rank: int,
        distributed_init_method: str,
        lora_config: Optional[LoRAConfig] = None,
        speculative_config: Optional[SpeculativeConfig] = None,
        prompt_adapter_config: Optional[PromptAdapterConfig] = None,
        is_driver_worker: bool = False,
        model_runner_cls: Optional[Type[GPUModelRunnerBase]] = None,
    ) -> None:
        self.model_config = model_config
        self.parallel_config = parallel_config
        self.parallel_config.rank = rank
        self.scheduler_config = scheduler_config
        self.device_config = device_config
        self.cache_config = cache_config
        self.local_rank = local_rank
        self.rank = rank
        self.distributed_init_method = distributed_init_method
        self.lora_config = lora_config
        self.prompt_adapter_config = prompt_adapter_config
        self.load_config = load_config
        self.is_driver_worker = is_driver_worker
        if parallel_config and is_driver_worker:
            assert rank % parallel_config.tensor_parallel_size == 0, \
                   "Driver worker should be rank 0 of tensor parallel group."

        if self.model_config.trust_remote_code:
            # note: lazy import to avoid importing torch before initializing
            from aphrodite.common.utils import init_cached_hf_modules
            init_cached_hf_modules()

        # Return hidden states from target model if the draft model is an
        # mlp_speculator
        speculative_args = {} if speculative_config is None \
            or (speculative_config.draft_model_config.model ==
                model_config.model) \
            or (speculative_config.draft_model_config.hf_config.model_type
                not in ["medusa", "mlp_speculator", "eagle"]) \
                    else {"return_hidden_states": True}

        ModelRunnerClass: Type[GPUModelRunnerBase] = ModelRunner
        if model_runner_cls is not None:
            ModelRunnerClass = model_runner_cls
        elif model_config.task == "embedding":
            ModelRunnerClass = EmbeddingModelRunner
        elif self._is_encoder_decoder_model():
            ModelRunnerClass = EncoderDecoderModelRunner
        self.model_runner: GPUModelRunnerBase = ModelRunnerClass(
            model_config,
            parallel_config,
            scheduler_config,
            device_config,
            cache_config,
            load_config=load_config,
            lora_config=self.lora_config,
            kv_cache_dtype=self.cache_config.cache_dtype,
            is_driver_worker=is_driver_worker,
            prompt_adapter_config=prompt_adapter_config,
            tp_rank=self.rank,
            **speculative_args,
        )
        # Uninitialized cache engine. Will be initialized by
        # initialize_cache.
        self.cache_engine: List[CacheEngine]
        # Initialize gpu_cache as embedding models don't initialize kv_caches
        self.gpu_cache: Optional[List[List[torch.Tensor]]] = None
        self._seq_group_metadata_cache: Dict[str, SequenceGroupMetadata] = {}

    def _is_encoder_decoder_model(self):
        return self.model_config.is_encoder_decoder_model

    def init_device(self) -> None:
        if self.device_config.device.type == "cuda":
            # torch.distributed.all_reduce does not free the input tensor until
            # the synchronization point. This causes the memory usage to grow
            # as the number of all_reduce calls increases. This env var disables
            # this behavior.
            # Related issue:
            # https://discuss.pytorch.org/t/cuda-allocation-lifetime-for-inputs-to-distributed-all-reduce/191573
            os.environ["TORCH_NCCL_AVOID_RECORD_STREAMS"] = "1"

            # This env var set by Ray causes exceptions with graph building.
            os.environ.pop("NCCL_ASYNC_ERROR_HANDLING", None)
            self.device = torch.device(f"cuda:{self.local_rank}")
            torch.cuda.set_device(self.device)

            _check_if_gpu_supports_dtype(self.model_config.dtype)
            gc.collect()
            torch.cuda.empty_cache()
            self.init_gpu_memory = torch.cuda.mem_get_info()[0]
        else:
            raise RuntimeError(
                f"Not support device type: {self.device_config.device}")
        # Initialize the distributed environment.
        init_worker_distributed_environment(self.parallel_config, self.rank,
                                            self.distributed_init_method,
                                            self.local_rank)
        # Set random seed.
        set_random_seed(self.model_config.seed)

    def load_model(self):
        self.model_runner.load_model()

    def save_sharded_state(
        self,
        path: str,
        pattern: Optional[str] = None,
        max_size: Optional[int] = None,
    ) -> None:
        self.model_runner.save_sharded_state(
            path,
            pattern=pattern,
            max_size=max_size,
        )

    def save_tensorized_model(
        self,
        tensorizer_config: TensorizerConfig,
    ) -> None:
        self.model_runner.save_tensorized_model(
            tensorizer_config=tensorizer_config, )

    @torch.inference_mode()
    def determine_num_available_blocks(self) -> Tuple[int, int]:
        """Profiles the peak memory usage of the model to determine how many
        KV blocks may be allocated without OOMs.

        The engine will first conduct a profiling of the existing memory usage.
        Then, it calculate the maximum possible number of GPU and CPU blocks
        that can be allocated with the remaining free memory.

        .. tip::
            You may limit the usage of GPU memory
            by adjusting the `gpu_memory_utilization` parameter.
        """
        # Profile the memory usage of the model and get the maximum number of
        # cache blocks that can be allocated with the remaining free memory.
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

        free_memory_pre_profile, total_gpu_memory = torch.cuda.mem_get_info()
        tp_rank = get_tensor_model_parallel_rank()

        # Execute a forward pass with dummy inputs to profile the memory usage
        # of the model.
        with memory_profiling(baseline_memory_in_bytes=total_gpu_memory -
                              self.init_gpu_memory,
                              weights_memory_in_bytes=self.model_runner.
                              get_model_memory_usage()) as result:
            self.model_runner.profile_run()
            torch.cuda.synchronize()

        self._assert_memory_footprint_increased_during_profiling()

        memory_for_current_instance = total_gpu_memory * \
            self.cache_config.gpu_memory_utilization
        available_kv_cache_memory = (memory_for_current_instance -
                                     result.non_kv_cache_memory_in_bytes)


        # Calculate the number of blocks that can be allocated with the
        # profiled peak memory.

        cache_block_size = self.get_cache_block_size_bytes()
        if cache_block_size == 0:
            num_gpu_blocks = 0
            num_cpu_blocks = 0
        else:
            # if single_user_mode is set to True, we only allocate enough blocks
            # for one sequence
            if self.scheduler_config.single_user_mode:
                num_gpu_blocks = (self.model_config.max_model_len +
                                  self.cache_config.block_size - 1
                                  ) // self.cache_config.block_size
                max_possible_blocks = int(
                    (total_gpu_memory *
                     self.cache_config.gpu_memory_utilization -
                     result.non_kv_cache_memory_in_bytes) // cache_block_size)
                num_gpu_blocks = min(num_gpu_blocks, max_possible_blocks)
                if tp_rank == 0:
                    logger.info(
                        f"Single sequence mode: Allocating {num_gpu_blocks} "
                        "blocks "
                        f"({num_gpu_blocks * self.cache_config.block_size} "
                        "tokens)")
            else:
                # Original logic for multi-sequence mode
                num_gpu_blocks = int(
                    available_kv_cache_memory // cache_block_size)

            num_cpu_blocks = int(self.cache_config.swap_space_bytes //
                                cache_block_size)

        num_gpu_blocks = max(num_gpu_blocks, 0)
        num_cpu_blocks = max(num_cpu_blocks, 0)

        if tp_rank == 0:
            tokens_per_block = self.cache_config.block_size
            blocks_per_seq = (self.model_config.max_model_len +
                              tokens_per_block - 1) // tokens_per_block
            memory_per_seq = blocks_per_seq * cache_block_size

            msg = (f"Memory profiling completed in {result.profile_time:.2f} seconds\n"  # noqa: E501
                  f"\nGPU memory breakdown:\n"
                  f"  Total GPU memory: {(total_gpu_memory / GiB_bytes):.2f} GiB\n"  # noqa: E501
                  f"  Memory utilization: {(self.cache_config.gpu_memory_utilization * 100):.0f}%\n"  # noqa: E501
                  f"  Available for Aphrodite: {(memory_for_current_instance / GiB_bytes):.2f} GiB\n"  # noqa: E501
                  f"  Model weights: {(result.weights_memory_in_bytes / GiB_bytes):.2f} GiB\n"  # noqa: E501
                  f"  Reserved for KV cache: {(available_kv_cache_memory / GiB_bytes):.2f} GiB\n"  # noqa: E501
                  f"  KV cache for {self.model_config.max_model_len} tokens: {memory_per_seq / GiB_bytes:.2f} GiB\n"  # noqa: E501
                  f"  Wasted memory: {(result.non_torch_increase_in_bytes / GiB_bytes):.2f} GiB\n"  # noqa: E501
                  f"  Peak activations memory: {(result.torch_peak_increase_in_bytes / GiB_bytes):.2f} GiB\n"  # noqa: E501
            )

            logger.info(msg)

        # Final cleanup
        if self.model_runner.lora_manager:
            self.model_runner.remove_all_loras()
        gc.collect()

        return num_gpu_blocks, num_cpu_blocks

    def _assert_memory_footprint_increased_during_profiling(self):
        # NOTE: Here we assume that the other processes using the same
        # GPU did not change their memory usage during the profiling.
        free_gpu_memory, _ = torch.cuda.mem_get_info()
        assert self.init_gpu_memory - free_gpu_memory > 0, (
            "Error in memory profiling. "
            f"Initial free memory {self.init_gpu_memory}, current free memory"
            f" {free_gpu_memory}. This happens when the GPU memory was "
            "not properly cleaned up before initializing the Aphrodite instance"
            )

    def initialize_cache(self, num_gpu_blocks: int,
                         num_cpu_blocks: int) -> None:
        """Allocate GPU and CPU KV cache with the specified number of blocks.

        This also warms up the model, which may record CUDA graphs.
        """
        raise_if_cache_size_invalid(num_gpu_blocks,
                                    self.cache_config.block_size,
                                    self.cache_config.is_attention_free,
                                    self.model_config.max_model_len)

        self.cache_config.num_gpu_blocks = num_gpu_blocks
        self.cache_config.num_cpu_blocks = num_cpu_blocks

        self._init_cache_engine()
        self._warm_up_model()

    def _init_cache_engine(self):
        assert self.cache_config.num_gpu_blocks is not None
        self.cache_engine = [
            CacheEngine(self.cache_config, self.model_config,
                        self.parallel_config, self.device_config, self.rank)
            for _ in range(self.parallel_config.pipeline_parallel_size)
        ]
        self.gpu_cache = [
            self.cache_engine[ve].gpu_cache
            for ve in range(self.parallel_config.pipeline_parallel_size)
        ]

    def _warm_up_model(self) -> None:
        if not self.model_config.enforce_eager:
            self.model_runner.capture_model(self.gpu_cache)
        # Reset the seed to ensure that the random state is not affected by
        # the model initialization and profiling.
        set_random_seed(self.model_config.seed)

    @property
    def do_metadata_broadcast(self) -> bool:
        return self.parallel_config.tensor_parallel_size > 1

    @property
    def kv_cache(self) -> Optional[List[List[torch.Tensor]]]:
        return self.gpu_cache

    @torch.inference_mode()
    def prepare_worker_input(
            self, execute_model_req: ExecuteModelRequest) -> WorkerInput:
        virtual_engine = execute_model_req.virtual_engine
        num_steps = execute_model_req.num_steps
        num_seq_groups = len(execute_model_req.seq_group_metadata_list)
        # `blocks_to_swap_in` and `blocks_to_swap_out` are cpu tensors.
        # they contain parameters to launch cudamemcpyasync.
        blocks_to_swap_in = torch.tensor(execute_model_req.blocks_to_swap_in,
                                         device="cpu",
                                         dtype=torch.int64).view(-1, 2)
        blocks_to_swap_out = torch.tensor(execute_model_req.blocks_to_swap_out,
                                          device="cpu",
                                          dtype=torch.int64).view(-1, 2)
        # `blocks_to_copy` is a gpu tensor. The src and tgt of
        # blocks to copy are in the same device, and `blocks_to_copy`
        # can be used directly within cuda kernels.
        blocks_to_copy = torch.tensor(execute_model_req.blocks_to_copy,
                                      device=self.device,
                                      dtype=torch.int64).view(-1, 2)

        return WorkerInput(num_seq_groups=num_seq_groups,
                           blocks_to_swap_in=blocks_to_swap_in,
                           blocks_to_swap_out=blocks_to_swap_out,
                           blocks_to_copy=blocks_to_copy,
                           virtual_engine=virtual_engine,
                           num_steps=num_steps)

    @torch.inference_mode()
    def execute_worker(self, worker_input: WorkerInput) -> None:
        virtual_engine = worker_input.virtual_engine
        # Issue cache operations.
        if (worker_input.blocks_to_swap_in is not None
                and worker_input.blocks_to_swap_in.numel() > 0):
            self.cache_engine[virtual_engine].swap_in(
                worker_input.blocks_to_swap_in)
        if (worker_input.blocks_to_swap_out is not None
                and worker_input.blocks_to_swap_out.numel() > 0):
            self.cache_engine[virtual_engine].swap_out(
                worker_input.blocks_to_swap_out)
        if (worker_input.blocks_to_copy is not None
                and worker_input.blocks_to_copy.numel() > 0):
            self.cache_engine[virtual_engine].copy(worker_input.blocks_to_copy)

    def _get_cached_seq_group_metadata(
            self,
            seq_group_metadata_list: List[Union[SequenceGroupMetadata,
                                                SequenceGroupMetadataDelta]],
            finished_request_ids: List[str]) -> List[SequenceGroupMetadata]:
        """Return a list of cached Sequence Group Metadata after updating its
        state.
        It is used because scheduler only sends delta to workers to reduce
        the data payload size. The function also cleans up cache based on
        a given `finished_request_ids`.
        """
        new_seq_group_metadata_list = []
        for metadata_or_delta in seq_group_metadata_list:
            request_id = metadata_or_delta.request_id
            if request_id not in self._seq_group_metadata_cache:
                # The first prefill.
                assert isinstance(metadata_or_delta, SequenceGroupMetadata)
                self._seq_group_metadata_cache[request_id] = metadata_or_delta
            else:
                # The first prefill is already cached.
                if isinstance(metadata_or_delta, SequenceGroupMetadataDelta):
                    self._seq_group_metadata_cache[request_id].apply_delta(
                        metadata_or_delta)
                else:
                    # If metadata snapshot is sent again, it is
                    # preempted. Reset the cache because we need to start
                    # from scratch.
                    assert isinstance(metadata_or_delta, SequenceGroupMetadata)
                    self._seq_group_metadata_cache[
                        request_id] = metadata_or_delta
            new_seq_group_metadata_list.append(
                self._seq_group_metadata_cache[request_id])
        # Clean up finished ids
        for finished_id in finished_request_ids:
            del self._seq_group_metadata_cache[finished_id]
        return new_seq_group_metadata_list

    def _execute_model_spmd(
        self,
        execute_model_req: ExecuteModelRequest,
        intermediate_tensors: Optional[IntermediateTensors] = None,
    ) -> Optional[List[SamplerOutput]]:
        if execute_model_req is not None:
            new_seq_group_metadata_list = self._get_cached_seq_group_metadata(
                execute_model_req.seq_group_metadata_list,
                execute_model_req.finished_requests_ids)
            execute_model_req.seq_group_metadata_list = (
                new_seq_group_metadata_list)
        output = super()._execute_model_spmd(execute_model_req,
                                             intermediate_tensors)
        return output

    def add_lora(self, lora_request: LoRARequest) -> bool:
        return self.model_runner.add_lora(lora_request)

    def remove_lora(self, lora_id: int) -> bool:
        return self.model_runner.remove_lora(lora_id)

    def pin_lora(self, lora_id: int) -> bool:
        return self.model_runner.pin_lora(lora_id)

    def list_loras(self) -> Set[int]:
        return self.model_runner.list_loras()

    def add_prompt_adapter(
            self, prompt_adapter_request: PromptAdapterRequest) -> bool:
        return self.model_runner.add_prompt_adapter(prompt_adapter_request)

    def remove_prompt_adapter(self, prompt_adapter_id: int) -> bool:
        return self.model_runner.remove_lora(prompt_adapter_id)

    def pin_prompt_adapter(self, prompt_adapter_id: int) -> bool:
        return self.model_runner.pin_prompt_adapter(prompt_adapter_id)

    def list_prompt_adapters(self) -> Set[int]:
        return self.model_runner.list_prompt_adapters()

    @property
    def max_model_len(self) -> int:
        return self.model_config.max_model_len

    @property
    def vocab_size(self) -> int:
        return self.model_runner.vocab_size

    def get_cache_block_size_bytes(self) -> int:
        """Get the size of the KV cache block size in bytes.
        """
        return CacheEngine.get_cache_block_size(self.cache_config,
                                                self.model_config,
                                                self.parallel_config)


def init_worker_distributed_environment(
    parallel_config: ParallelConfig,
    rank: int,
    distributed_init_method: Optional[str] = None,
    local_rank: int = -1,
) -> None:
    """Initialize the distributed environment."""
    set_custom_all_reduce(not parallel_config.disable_custom_all_reduce)

    init_distributed_environment(parallel_config.world_size, rank,
                                 distributed_init_method, local_rank)

    ensure_model_parallel_initialized(parallel_config.tensor_parallel_size,
                                      parallel_config.pipeline_parallel_size)


def _check_if_gpu_supports_dtype(torch_dtype: torch.dtype):
    # Check if the GPU supports the dtype.
    if torch_dtype == torch.bfloat16:
        compute_capability = current_platform.get_device_capability()
        if compute_capability[0] < 8:
            gpu_name = current_platform.get_device_name()
            raise ValueError(
                "Bfloat16 is only supported on GPUs with compute capability "
                f"of at least 8.0. Your {gpu_name} GPU has compute capability "
                f"{compute_capability[0]}.{compute_capability[1]}. "
                "You can use float16 instead by explicitly setting the"
                "`dtype` flag in CLI, for example: --dtype=half.")


def raise_if_cache_size_invalid(num_gpu_blocks, block_size, is_attention_free,
                                max_model_len) -> None:
    if is_attention_free and num_gpu_blocks != 0:
        raise ValueError("No memory should be allocated for the cache blocks "
                         f"for an attention-free model, but {num_gpu_blocks}"
                         "blocks are allocated.")
    if not is_attention_free and num_gpu_blocks <= 0:
        raise ValueError("No available memory for the cache blocks. "
                         "Try increasing `gpu_memory_utilization` when "
                         "initializing the engine.")
    max_seq_len = block_size * num_gpu_blocks
    rank = get_tensor_model_parallel_rank()
    if rank == 0:
        logger.info(f"Maximum sequence length allowed in the cache: "
                    f"{max_seq_len}")
    if not is_attention_free and max_model_len > max_seq_len:
        original_max_model_len = max_model_len
        max_model_len = max_seq_len
        # raise ValueError(
        #     f"The model's max seq len ({max_model_len}) "
        #     "is larger than the maximum number of tokens that can be "
        #     f"stored in KV cache ({max_seq_len}). Try increasing "
        #     "`gpu_memory_utilization` or decreasing `max_model_len` when "
        #     "initializing the engine.")
        # set the max_model_len to the max_seq_len, but raise a logger.error
        # so the user is made aware of this
        logger.error(
            f"The model's max seq len ({original_max_model_len}) "
            "is larger than the maximum number of tokens that can be "
            f"stored in KV cache ({max_seq_len}). "
            "Try increasing "
            "`gpu_memory_utilization`, setting "
            "`--enable-chunked-prefill`, or `--kv-cache-dtype fp8` "
            "when initializing the engine. The last two are currently "
            "mutually exclusive.\n"
            f"Forcing max_model_len to {max_seq_len}.")
