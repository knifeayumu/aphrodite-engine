"""Test that various errors are handled properly."""

import asyncio
import tempfile
import time
import uuid
from unittest.mock import Mock

import pytest

from tests.mq_llm_engine.utils import RemoteMQLLMEngine
from aphrodite import SamplingParams
from aphrodite.engine.args_tools import AsyncEngineArgs
from aphrodite.engine.aphrodite_engine import AphroditeEngine
from aphrodite.engine.multiprocessing import MQEngineDeadError
from aphrodite.engine.multiprocessing.engine import MQLLMEngine
from aphrodite.endpoints.openai.api_server import build_async_engine_client
from aphrodite.endpoints.openai.args import make_arg_parser
from aphrodite.lora.request import LoRARequest
from aphrodite.common.sequence import SequenceGroupMetadata
from aphrodite.usage.usage_lib import UsageContext
from aphrodite.common.utils import FlexibleArgumentParser

MODEL = "google/gemma-1.1-2b-it"
ENGINE_ARGS = AsyncEngineArgs(model=MODEL, enforce_eager=True)
RAISED_ERROR = KeyError
RAISED_VALUE = "foo"


@pytest.fixture(scope="function")
def tmp_socket():
    with tempfile.TemporaryDirectory() as td:
        yield f"ipc://{td}/{uuid.uuid4()}"


def run_with_evil_forward(engine_args: AsyncEngineArgs, ipc_path: str):
    # Make engine.
    engine = MQLLMEngine.from_engine_args(
        engine_args=engine_args,
        usage_context=UsageContext.UNKNOWN_CONTEXT,
        ipc_path=ipc_path)

    # Raise error during first forward pass.
    engine.engine.model_executor.execute_model = Mock(
        side_effect=RAISED_ERROR(RAISED_VALUE))

    # Run engine.
    engine.start()


@pytest.mark.asyncio
async def test_evil_forward(tmp_socket):
    with RemoteMQLLMEngine(engine_args=ENGINE_ARGS,
                           ipc_path=tmp_socket,
                           run_fn=run_with_evil_forward) as engine:

        client = await engine.make_client()

        # Server should be healthy after initial probe.
        await asyncio.sleep(2.0)
        await client.check_health()

        # Throws an error that should get ENGINE_DEAD_ERROR.
        with pytest.raises(MQEngineDeadError):
            async for _ in client.generate(prompt="Hello my name is",
                                           sampling_params=SamplingParams(),
                                           request_id=uuid.uuid4()):
                pass
        assert client.errored

        await asyncio.sleep(1.0)
        with pytest.raises(RAISED_ERROR):
            await client.check_health()
        assert client.errored

        # Shutdown.
        client.close()


def run_with_evil_model_executor_health(engine_args: AsyncEngineArgs,
                                        ipc_path: str):
    # Make engine.
    engine = MQLLMEngine.from_engine_args(
        engine_args=engine_args,
        usage_context=UsageContext.UNKNOWN_CONTEXT,
        ipc_path=ipc_path)

    # Raise error during first forward pass.
    engine.engine.model_executor.check_health = Mock(side_effect=RAISED_ERROR)

    # Run engine.
    engine.start()


@pytest.mark.asyncio
async def test_failed_health_check(tmp_socket):
    with RemoteMQLLMEngine(
            engine_args=ENGINE_ARGS,
            ipc_path=tmp_socket,
            run_fn=run_with_evil_model_executor_health) as engine:

        client = await engine.make_client()
        assert client.is_running

        # Health probe should throw RAISED_ERROR.
        await asyncio.sleep(15.)

        with pytest.raises(RAISED_ERROR):
            await client.check_health()
        assert client.errored

        # Generate call should throw ENGINE_DEAD_ERROR
        with pytest.raises(MQEngineDeadError):
            async for _ in client.generate(prompt="Hello my name is",
                                           sampling_params=SamplingParams(),
                                           request_id=uuid.uuid4()):
                pass

        client.close()


def run_with_evil_abort(engine_args: AsyncEngineArgs, ipc_path: str):
    # Make engine.
    engine = MQLLMEngine.from_engine_args(
        engine_args=engine_args,
        usage_context=UsageContext.UNKNOWN_CONTEXT,
        ipc_path=ipc_path)

    # Raise error during abort call.
    engine.engine.abort_request = Mock(side_effect=RAISED_ERROR)

    # Run engine.
    engine.start()


@pytest.mark.asyncio
async def test_failed_abort(tmp_socket):
    with RemoteMQLLMEngine(engine_args=ENGINE_ARGS,
                           ipc_path=tmp_socket,
                           run_fn=run_with_evil_abort) as engine:

        client = await engine.make_client()
        assert client.is_running

        # First check health should work.
        await client.check_health()

        # Trigger an abort on the client side.
        # This request ID does not exist, and will cause the engine to error
        await client.abort(request_id="foo")

        # Future generation requests will now fail
        # with reference to the original KeyError("foo")
        with pytest.raises(MQEngineDeadError) as execinfo:
            async for _ in client.generate(
                    prompt="Hello my name is",
                    sampling_params=SamplingParams(max_tokens=10),
                    request_id=uuid.uuid4()):
                pass
        assert "KeyError" in repr(execinfo.value)
        assert client.errored

        # This should raise the original error.
        with pytest.raises(RAISED_ERROR):
            await client.check_health()

        client.close()


@pytest.mark.asyncio
async def test_batch_error(tmp_socket):
    with RemoteMQLLMEngine(engine_args=ENGINE_ARGS,
                           ipc_path=tmp_socket,
                           run_fn=run_with_evil_abort) as engine:

        client = await engine.make_client()
        assert client.is_running

        # First check health should work.
        await client.check_health()

        # Batch of requests
        async def do_generate(client):
            # min_tokens=2048 to keep busy the engine busy
            # to get enough time to get process a request
            # that will crash the engine
            params = SamplingParams(min_tokens=2048, max_tokens=2048)
            async for _ in client.generate(prompt="Hello my name is",
                                           sampling_params=params,
                                           request_id=uuid.uuid4()):
                pass

        tasks = [asyncio.create_task(do_generate(client)) for _ in range(10)]

        # This request will force a processing batch to raise
        # an exception and next the engine get errored
        await client.abort(request_id="foo")

        # The batch of those request failed, then they
        # should get the same exception as a MQEngineDeadError.
        errors = await asyncio.gather(*tasks, return_exceptions=True)
        for e in errors:
            assert isinstance(e, MQEngineDeadError)
            assert "KeyError" in repr(e)

        client.close()


@pytest.mark.asyncio
async def test_bad_request(tmp_socket):
    with RemoteMQLLMEngine(engine_args=ENGINE_ARGS,
                           ipc_path=tmp_socket) as engine:

        client = await engine.make_client()

        # Invalid request should fail, but not crash the server.
        with pytest.raises(ValueError):
            async for _ in client.generate(prompt="Hello my name is",
                                           sampling_params=SamplingParams(),
                                           request_id="abcd-1",
                                           lora_request=LoRARequest(
                                               "invalid-lora", 1,
                                               "invalid-path")):
                pass

        # This request should be okay.
        async for _ in client.generate(prompt="Hello my name is",
                                       sampling_params=SamplingParams(),
                                       request_id="abcd-2"):
            pass

        # Shutdown.
        client.close()


@pytest.mark.asyncio
async def test_mp_crash_detection(monkeypatch: pytest.MonkeyPatch):
    with monkeypatch.context() as m:

        parser = FlexibleArgumentParser(
            description="Aphrodite's remote OpenAI server.")
        parser = make_arg_parser(parser)
        args = parser.parse_args([])

        # When AphroditeEngine is loaded, it will crash.
        def mock_init():
            raise ValueError

        m.setattr(AphroditeEngine, "__init__", mock_init)

        start = time.perf_counter()
        async with build_async_engine_client(args):
            pass
        end = time.perf_counter()

        assert end - start < 60, (
            "Expected Aphrodite to gracefully shutdown in <60s "
            "if there is an error in the startup.")


@pytest.mark.asyncio
async def test_mp_cuda_init():
    # it should not crash, when cuda is initialized
    # in the API server process
    import torch
    torch.cuda.init()
    parser = FlexibleArgumentParser(description="Aphrodite's remote OpenAI server.")
    parser = make_arg_parser(parser)
    args = parser.parse_args([])

    async with build_async_engine_client(args):
        pass


@pytest.mark.asyncio
async def test_engine_process_death(tmp_socket):
    with RemoteMQLLMEngine(engine_args=ENGINE_ARGS,
                           ipc_path=tmp_socket) as engine:

        client = await engine.make_client()
        assert client.is_running

        # kill the engine process
        engine.proc.kill()

        # Generate call should fail
        with pytest.raises(MQEngineDeadError):
            async for _ in client.generate(prompt="Hello my name is",
                                           sampling_params=SamplingParams(),
                                           request_id=uuid.uuid4()):
                pass

        # And the health check should show the engine is dead
        with pytest.raises(RuntimeError, match="Engine process .* died"):
            await client.check_health()

        client.close()


def run_with_evil_input_processing(engine_args: AsyncEngineArgs,
                                   ipc_path: str):
    """Simulate an exception while preparing inputs for the model.
    In the wild, this could be something like a multimodal input processor
    failing on invalid image data."""

    # Make engine.
    engine = MQLLMEngine.from_engine_args(
        engine_args=engine_args,
        usage_context=UsageContext.UNKNOWN_CONTEXT,
        ipc_path=ipc_path)

    runner = engine.engine.model_executor.driver_worker.worker.model_runner

    # Raise error in the model runner when adding a sequence group.
    # See class ModelInputForGPUBuilder
    def raiser(_, seq_group_metadata: SequenceGroupMetadata):
        if seq_group_metadata.request_id.startswith("evil"):
            raise RAISED_ERROR(RAISED_VALUE)

    runner.builder.per_seq_group_compute_fns.append(raiser)

    # Run engine.
    engine.start()


@pytest.mark.asyncio
async def test_failed_inputs(tmp_socket):
    with RemoteMQLLMEngine(engine_args=ENGINE_ARGS,
                           ipc_path=tmp_socket,
                           run_fn=run_with_evil_input_processing) as engine:

        client = await engine.make_client()
        assert client.is_running

        # Engine should be healthy
        await client.check_health()

        async def run_failing_request():
            async for _ in client.generate(
                    prompt="Hello my name is",
                    sampling_params=SamplingParams(max_tokens=10),
                    request_id="evil" + str(uuid.uuid4())):
                pass

        async def run_passing_request():
            async for _ in client.generate(
                    prompt="Hello my name is",
                    sampling_params=SamplingParams(max_tokens=10),
                    request_id=str(uuid.uuid4())):
                pass

        passing_tasks = [
            asyncio.create_task(run_passing_request()) for _ in range(10)
        ]
        failing_tasks = [
            asyncio.create_task(run_failing_request()) for _ in range(10)
        ]
        await asyncio.gather(*failing_tasks, return_exceptions=True)
        await asyncio.gather(*passing_tasks)

        # All the bad inputs should have raised
        for task in failing_tasks:
            with pytest.raises(RAISED_ERROR):
                task.result()

        # But all good inputs should have still succeeded
        for task in passing_tasks:
            task.result()

        # And the engine should remain healthy
        assert not client.errored
        await client.check_health()

        client.close()
