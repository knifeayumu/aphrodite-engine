from .interface import CpuArchEnum, Platform, PlatformEnum, UnspecifiedPlatform

current_platform: Platform

# NOTE: we don't use `torch.version.cuda` / `torch.version.hip` because
# they only indicate the build configuration, not the runtime environment.
# For example, people can install a cuda build of pytorch but run on tpu.

is_tpu = False
try:
    # While it's technically possible to install libtpu on a non-TPU machine,
    # this is a very uncommon scenario. Therefore, we assume that libtpu is
    # installed if and only if the machine has TPUs.
    import libtpu  # noqa: F401
    is_tpu = True
except Exception:
    pass

is_cuda = False

try:
    import pynvml
    pynvml.nvmlInit()
    try:
        if pynvml.nvmlDeviceGetCount() > 0:
            is_cuda = True
    finally:
        pynvml.nvmlShutdown()
except Exception:
    pass

is_rocm = False

try:
    import amdsmi
    amdsmi.amdsmi_init()
    try:
        if len(amdsmi.amdsmi_get_processor_handles()) > 0:
            is_rocm = True
    finally:
        amdsmi.amdsmi_shut_down()
except Exception:
    pass

is_xpu = False

try:
    import torch
    if hasattr(torch, 'xpu') and torch.xpu.is_available():
        is_xpu = True
except Exception:
    pass

is_cpu = False
try:
    from importlib.metadata import version
    is_cpu = "cpu" in version("aphrodite-engine")
except Exception:
    pass

is_neuron = False
try:
    import transformers_neuronx  # noqa: F401
    is_neuron = True
except ImportError:
    pass

is_openvino = False
try:
    from importlib.metadata import version
    is_openvino = "openvino" in version("aphrodite-engine")
except Exception:
    pass

if is_tpu:
    # people might install pytorch built with cuda but run on tpu
    # so we need to check tpu first
    from .tpu import TpuPlatform
    current_platform = TpuPlatform()
elif is_cuda:
    from .cuda import CudaPlatform
    current_platform = CudaPlatform()
elif is_rocm:
    from .rocm import RocmPlatform
    current_platform = RocmPlatform()
elif is_xpu:
    from .xpu import XPUPlatform
    current_platform = XPUPlatform()
elif is_cpu:
    from .cpu import CpuPlatform
    current_platform = CpuPlatform()
elif is_neuron:
    from .neuron import NeuronPlatform
    current_platform = NeuronPlatform()
else:
    current_platform = UnspecifiedPlatform()

__all__ = ['Platform', 'PlatformEnum', 'current_platform', 'CpuArchEnum']
