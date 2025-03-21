import os
import random
import tempfile
from unittest.mock import patch

from aphrodite.common.config import (CacheConfig, DeviceConfig, LoadConfig,
                                     LoRAConfig, ModelConfig, ParallelConfig,
                                     SchedulerConfig)
from aphrodite.lora.models import LoRAMapping
from aphrodite.lora.request import LoRARequest
from aphrodite.worker.worker import Worker


@patch.dict(os.environ, {"RANK": "0"})
def test_worker_apply_lora(sql_lora_files):
    worker = Worker(
        model_config=ModelConfig(
            "meta-llama/Llama-2-7b-hf",
            "meta-llama/Llama-2-7b-hf",
            tokenizer_mode="auto",
            trust_remote_code=False,
            seed=0,
            dtype="float16",
            revision=None,
        ),
        load_config=LoadConfig(
            download_dir=None,
            load_format="dummy",
        ),
        parallel_config=ParallelConfig(1, 1, False),
        scheduler_config=SchedulerConfig(32, 32, 32),
        device_config=DeviceConfig("cuda"),
        cache_config=CacheConfig(block_size=16,
                                 gpu_memory_utilization=1.,
                                 swap_space=0,
                                 cache_dtype="auto"),
        local_rank=0,
        rank=0,
        lora_config=LoRAConfig(max_lora_rank=8, max_cpu_loras=32,
                               max_loras=32),
        distributed_init_method=f"file://{tempfile.mkstemp()[1]}",
    )
    worker.init_device()
    worker.load_model()

    worker.model_runner.set_active_loras([], LoRAMapping([], []))
    assert worker.list_loras() == set()

    n_loras = 32
    lora_requests = [
        LoRARequest(str(i + 1), i + 1, sql_lora_files) for i in range(n_loras)
    ]

    worker.model_runner.set_active_loras(lora_requests, LoRAMapping([], []))
    assert worker.list_loras() == {
        lora_request.lora_int_id
        for lora_request in lora_requests
    }

    for i in range(32):
        random.seed(i)
        iter_lora_requests = random.choices(lora_requests,
                                            k=random.randint(1, n_loras))
        random.shuffle(iter_lora_requests)
        iter_lora_requests = iter_lora_requests[:-random.randint(0, n_loras)]
        worker.model_runner.set_active_loras(iter_lora_requests,
                                             LoRAMapping([], []))
        assert worker.list_loras().issuperset(
            {lora_request.lora_int_id
             for lora_request in iter_lora_requests})
