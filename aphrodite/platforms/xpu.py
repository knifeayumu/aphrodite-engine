from typing import TYPE_CHECKING, Optional

import torch
from loguru import logger

from .interface import DeviceCapability, Platform, PlatformEnum, _Backend

if TYPE_CHECKING:
    from aphrodite.common.config import AphroditeConfig
else:
    AphroditeConfig = None


class XPUPlatform(Platform):
    _enum = PlatformEnum.XPU
    device_name: str = "xpu"
    device_type: str = "xpu"
    dispatch_key: str = "XPU"
    # Intel XPU's device key is "GPU" for Ray.
    # see https://github.com/ray-project/ray/blob/6a5eb5865eeb9ccf058a79b44f107e327e360673/python/ray/_private/accelerators/intel_gpu.py#L20 # noqa: E501
    ray_device_key: str = "GPU"
    device_control_env_var: str = "ONEAPI_DEVICE_SELECTOR"

    @classmethod
    def get_attn_backend_cls(cls, selected_backend: _Backend, head_size: int,
                             dtype: torch.dtype, kv_cache_dtype: Optional[str],
                             block_size: int, use_v1: bool,
                             use_mla: bool) -> str:
        if selected_backend != _Backend.IPEX:
            logger.info("Cannot use {} backend on XPU.", selected_backend)
        logger.info("Using IPEX attention backend.")
        return "aphrodite.attention.backends.ipex_attn.IpexAttnBackend"

    @staticmethod
    def get_device_capability(
            device_id: int = 0) -> Optional[DeviceCapability]:
        # capacity format differs from cuda's and will cause unexpected
        # failure, so use None directly
        return None

    @staticmethod
    def get_device_name(device_id: int = 0) -> str:
        return torch.xpu.get_device_name(device_id)

    @classmethod
    def get_device_total_memory(cls, device_id: int = 0) -> int:
        device_props = torch.xpu.get_device_properties(device_id)
        return device_props.total_memory

    @classmethod
    def is_async_output_supported(cls, enforce_eager: Optional[bool]) -> bool:
        return True

    @staticmethod
    def inference_mode():
        return torch.no_grad()

    @classmethod
    def check_and_update_config(cls, aphrodite_config: AphroditeConfig) -> None:
        cache_config = aphrodite_config.cache_config
        if cache_config and cache_config.block_size is None:
            cache_config.block_size = 16

        # check and update model config
        model_config = aphrodite_config.model_config
        if model_config.dtype == torch.bfloat16:
            bf16_supported = cls.device_support_bf16()
            if not bf16_supported:
                logger.warning(
                    "bfloat16 is only supported on Intel Data Center GPU, "
                    "Intel Arc GPU is not supported yet. Your device is {},"
                    " which is not supported. will fallback to float16",
                    cls.get_device_name())
                model_config.dtype = torch.float16
        if not model_config.enforce_eager:
            logger.warning(
                "CUDA graph is not supported on XPU, fallback to the eager "
                "mode.")
            model_config.enforce_eager = True

        if aphrodite_config.speculative_config is not None:
            raise NotImplementedError(
                "XPU does not support speculative decoding")

        if aphrodite_config.device_config is not None:
            assert aphrodite_config.device_config.device_type == "xpu"

        # check and update parallel config
        parallel_config = aphrodite_config.parallel_config
        if parallel_config.worker_cls == "auto":
            parallel_config.worker_cls = "aphrodite.worker.xpu_worker.XPUWorker"

        if parallel_config.distributed_executor_backend is None:
            parallel_config.distributed_executor_backend = "ray"
        elif parallel_config.distributed_executor_backend == "mp":
            # FIXME:
            # spawn needs calling `if __name__ == '__main__':``
            # fork is not supported for xpu start new process.
            logger.error(
                "Both start methods (spawn and fork) have issue "
                "on XPU if you use mp backend, setting it to ray instead.")
            parallel_config.distributed_executor_backend = "ray"

        elif parallel_config.distributed_executor_backend != "ray":
            logger.warning(
                "{} is not supported on XPU, fallback to ray distributed"
                " executor backend.",
                parallel_config.distributed_executor_backend)
            parallel_config.distributed_executor_backend = "ray"

    @classmethod
    def is_pin_memory_available(cls):
        logger.warning("Pin memory is not supported on XPU.")
        return False

    @classmethod
    def get_current_memory_usage(cls,
                                 device: Optional[torch.types.Device] = None
                                 ) -> float:
        torch.xpu.reset_peak_memory_stats(device)
        return torch.xpu.max_memory_allocated(device)

    @classmethod
    def device_support_bf16(cls) -> bool:
        device_name = cls.get_device_name().lower()
        if device_name.count("arc") > 0:
            return False
        elif device_name.count("data center gpu") > 0:
            return True
        else:
            logger.warning("Unknown device name {}, always use float16",
                           device_name)
            return False

    @classmethod
    def get_device_communicator_cls(cls) -> str:
        return "aphrodite.distributed.device_communicators.xpu_communicator.XpuCommunicator"  # noqa
