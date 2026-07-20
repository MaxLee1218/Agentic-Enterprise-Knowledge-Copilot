"""Bounded execution mechanism for synchronous tool adapters."""

from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from copilot.contracts import JsonObject
from copilot.tools.base import Tool, ToolExecutionContext, ToolExecutionOutput
from copilot.tools.exceptions import ToolExecutionError, ToolTimeoutError


class ThreadPoolToolRunner:
    """Run synchronous adapters with a bounded caller-side timeout.

    Python cannot forcibly stop an arbitrary running thread. A timed-out result is therefore
    cancelled when possible and always ignored, as required by the frozen late-result rule.
    Tool implementations must still use their own I/O cancellation primitives where available.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="copilot-tool")

    def run(
        self,
        tool: Tool,
        arguments: JsonObject,
        context: ToolExecutionContext,
        timeout_seconds: float,
    ) -> ToolExecutionOutput:
        """Return an adapter payload or a safe typed runtime signal."""
        future: Future[ToolExecutionOutput] = self._pool.submit(tool.execute, arguments, context)
        try:
            output = future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise ToolTimeoutError() from exc
        except Exception as exc:
            raise ToolExecutionError() from exc
        if not isinstance(output, ToolExecutionOutput):
            raise ToolExecutionError()
        return output

    def close(self) -> None:
        """Release worker resources without waiting for ignored late results."""
        self._pool.shutdown(wait=False, cancel_futures=True)
