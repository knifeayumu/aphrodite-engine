import functools
import gc
from typing import Callable, TypeVar

import pytest
import torch
from typing_extensions import ParamSpec

from aphrodite.distributed import cleanup_dist_env_and_memory
from aphrodite.modeling.model_loader.tensorizer import TensorizerConfig


@pytest.fixture(scope="function", autouse=True)
def use_v0_only(monkeypatch):
    """
    Tensorizer only tested on V0 so far.
    """
    monkeypatch.setenv('APHRODITE_USE_V1', '0')


@pytest.fixture(autouse=True)
def cleanup():
    cleanup_dist_env_and_memory(shutdown_ray=True)


_P = ParamSpec("_P")
_R = TypeVar("_R")


def retry_until_skip(n: int):

    def decorator_retry(func: Callable[_P, _R]) -> Callable[_P, _R]:

        @functools.wraps(func)
        def wrapper_retry(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            for i in range(n):
                try:
                    return func(*args, **kwargs)
                except AssertionError:
                    gc.collect()
                    torch.cuda.empty_cache()
                    if i == n - 1:
                        pytest.skip(f"Skipping test after {n} attempts.")

            raise AssertionError("Code should not be reached")

        return wrapper_retry

    return decorator_retry


@pytest.fixture(autouse=True)
def tensorizer_config():
    config = TensorizerConfig(tensorizer_uri="aphrodite")
    return config
