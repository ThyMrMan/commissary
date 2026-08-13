import asyncio
from concurrent.futures import TimeoutError as FuturesTimeoutError
import threading

_loop = None
_thread = None
_lock = threading.Lock()


def _run_loop(loop, ready):
    asyncio.set_event_loop(loop)
    # Scheduled via call_soon (same-thread), not set as the first statement:
    # this only fires once run_forever() actually starts pumping the loop's
    # ready queue, so _get_loop() can safely hand the loop to
    # run_coroutine_threadsafe() the instant `ready` is set, closing the
    # startup race where a caller submits before the loop is truly running.
    loop.call_soon(ready.set)
    loop.run_forever()


def _get_loop():
    global _loop, _thread
    if _loop is None or _loop.is_closed() or _thread is None or not _thread.is_alive():
        with _lock:
            if _loop is None or _loop.is_closed() or _thread is None or not _thread.is_alive():
                _loop = asyncio.new_event_loop()
                ready = threading.Event()
                _thread = threading.Thread(target=_run_loop, args=(_loop, ready), daemon=True)
                _thread.start()
                if not ready.wait(timeout=5):
                    raise RuntimeError("Async event loop thread failed to start")
    return _loop


def run_async(coro, timeout: float | None = None):
    """Drop-in replacement for asyncio.run() that uses a single shared event loop.

    A dedicated daemon thread runs one event loop for the entire process.
    Callers submit coroutines and block until the result is ready.
    This avoids creating/destroying event loops per call (FD leak),
    works correctly with both long-lived and short-lived threads, and lets
    concurrently-submitted coroutines interleave at their own await points
    instead of fully serializing one caller behind another.

    ``timeout`` bounds the WAIT, for callers running on a request thread. There
    are only 8 of those in total, so one unbounded wait on an unreachable
    service pins an eighth of the server's capacity until the worker timeout —
    and a poll that fires every few seconds queues more behind it. On expiry the
    coroutine is cancelled and TimeoutError is raised; a caller that can degrade
    (a status poll showing 0 B/s for one tick) should catch it. Omitted means
    wait forever, exactly as before, which is right for background work.
    """
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout)
    except FuturesTimeoutError:
        # Don't leave the coroutine running against a dead service behind us.
        future.cancel()
        raise TimeoutError(
            f"async call exceeded its {timeout}s budget and was cancelled"
        ) from None
