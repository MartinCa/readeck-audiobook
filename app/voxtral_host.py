"""Starting and stopping the Voxtral container on demand.

The vLLM-Omni server holds about 14 GB of VRAM for as long as it runs, and its
sleep mode cannot give that back and be woken again without keeping the weights
in host RAM (see the README). So the container is stopped while nobody needs it: a job starts it
and waits until it answers, and it is stopped again after
``VOXTRAL_IDLE_STOP_SECONDS`` without one. Cold start is minutes, so this suits
background generation.

The app does this through the Docker API on a socket proxy (``DOCKER_PROXY_URL``),
never the socket itself; the proxy only needs to allow container inspection,
start and stop. Without ``DOCKER_PROXY_URL`` and ``VOXTRAL_CONTAINER`` the server
is assumed to be always on and nothing here does anything.
"""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime

import httpx

from app import config

logger = logging.getLogger(__name__)

STATE_UNMANAGED = "unmanaged"  # the app does not control the container
STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"
STATE_ERROR = "error"  # the Docker proxy cannot be reached or refuses

# How often to look at a starting container, and how long Docker gives the server
# to shut down before killing it.
POLL_SECONDS = 3.0
STOP_GRACE_SECONDS = 30

# Jobs currently using the server; it is never stopped under one.
_users = 0
_stop_timer: asyncio.TimerHandle | None = None
_stop_deadline: float | None = None  # time.monotonic() when the idle stop fires
_stop_task: asyncio.Task | None = None
_starting = False
_stopping = False
_start_triggered_at: datetime | None = None
_lock: asyncio.Lock | None = None


def managed() -> bool:
    return bool(config.VOXTRAL_URL and config.DOCKER_PROXY_URL and config.VOXTRAL_CONTAINER)


def _guard() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# ── Docker API ─────────────────────────────────────────────────────────────────


async def _docker(method: str, path: str, **kwargs) -> httpx.Response:
    """One call to the Docker API; failures become RuntimeError with the fix in it."""
    url = f"{config.DOCKER_PROXY_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=STOP_GRACE_SECONDS + 15) as client:
            response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Cannot reach the Docker proxy at {config.DOCKER_PROXY_URL}: {exc!r}"
        ) from exc
    if response.status_code == 403:
        raise RuntimeError(
            f"The Docker proxy refused {method} {path}. It must allow CONTAINERS=1, "
            "ALLOW_START=1 and ALLOW_STOP=1."
        )
    if response.status_code == 404:
        raise RuntimeError(f"Docker has no container named {config.VOXTRAL_CONTAINER!r}")
    if response.status_code >= 400:
        raise RuntimeError(
            f"Docker returned {response.status_code} for {method} {path}: {response.text[:300]}"
        )
    return response


async def _inspect() -> dict:
    response = await _docker("GET", f"/containers/{config.VOXTRAL_CONTAINER}/json")
    return response.json().get("State", {})


def _parse_time(value: str | None) -> datetime | None:
    """Docker's RFC 3339 timestamps carry nanoseconds, which fromisoformat rejects."""
    if not value or value.startswith("0001-"):
        return None
    try:
        trimmed = re.sub(r"(\.\d{6})\d*", r"\1", value).replace("Z", "+00:00")
        return datetime.fromisoformat(trimmed)
    except ValueError:
        return None


async def _answering() -> bool:
    """Whether the server is up and has its model loaded."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{config.VOXTRAL_URL}/v1/audio/voices")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


# ── Starting ───────────────────────────────────────────────────────────────────


async def _ensure_running() -> None:
    global _starting, _start_triggered_at
    async with _guard():
        if await _answering():
            return
        state = await _inspect()
        already_running = bool(state.get("Running"))
        if not already_running:
            logger.info(
                "Starting the Voxtral container %s (a cold start takes minutes)",
                config.VOXTRAL_CONTAINER,
            )
            await _docker("POST", f"/containers/{config.VOXTRAL_CONTAINER}/start")
            _start_triggered_at = datetime.now(UTC)
        _starting = True
        began = time.monotonic()
        try:
            while not await _answering():
                if time.monotonic() - began > config.VOXTRAL_START_TIMEOUT_SECONDS:
                    if already_running:
                        raise RuntimeError(
                            f"The Voxtral container is running but the server at "
                            f"{config.VOXTRAL_URL} did not answer within "
                            f"{config.VOXTRAL_START_TIMEOUT_SECONDS}s. Check VOXTRAL_URL and the "
                            "container's log."
                        )
                    raise RuntimeError(
                        f"The Voxtral server did not answer within "
                        f"{config.VOXTRAL_START_TIMEOUT_SECONDS}s of starting"
                    )
                state = await _inspect()
                if state.get("Status") in {"exited", "dead"}:
                    raise RuntimeError(
                        f"The Voxtral container stopped while starting (exit code "
                        f"{state.get('ExitCode')}); see its log"
                    )
                await asyncio.sleep(POLL_SECONDS)
            logger.info("Voxtral server is up after %.0fs", time.monotonic() - began)
        finally:
            _starting = False
            _start_triggered_at = None


async def acquire() -> None:
    """Hold the server up for a job, starting it and waiting for it if need be."""
    global _users
    _users += 1
    _cancel_stop()
    if managed():
        await _ensure_running()


def release() -> None:
    """The job is done with the server; arm the idle stop once nobody is left."""
    global _users
    _users -= 1
    if _users == 0:
        _arm_stop(config.VOXTRAL_IDLE_STOP_SECONDS)


# ── Stopping ───────────────────────────────────────────────────────────────────


def _cancel_stop() -> None:
    global _stop_timer, _stop_deadline
    if _stop_timer is not None:
        _stop_timer.cancel()
    _stop_timer = _stop_deadline = None


def _arm_stop(seconds: float) -> None:
    global _stop_timer, _stop_deadline
    _cancel_stop()
    if not managed() or seconds <= 0:
        return
    loop = asyncio.get_running_loop()
    _stop_deadline = time.monotonic() + seconds
    _stop_timer = loop.call_later(seconds, _fire_stop)


def _fire_stop() -> None:
    global _stop_timer, _stop_deadline, _stop_task
    _stop_timer = _stop_deadline = None
    _stop_task = asyncio.get_running_loop().create_task(_stop_if_idle())


async def _stop_if_idle() -> None:
    global _stopping
    async with _guard():
        if _users > 0:
            return
        try:
            if not (await _inspect()).get("Running"):
                # Already stopped, say after a start that failed: Docker would
                # answer 304, and there is nothing to report.
                return
            _stopping = True
            await _docker(
                "POST",
                f"/containers/{config.VOXTRAL_CONTAINER}/stop",
                params={"t": STOP_GRACE_SECONDS},
            )
            logger.info(
                "Stopped the Voxtral container after %ss idle, freeing its GPU memory. "
                "The next Voxtral job starts it again.",
                config.VOXTRAL_IDLE_STOP_SECONDS,
            )
        except RuntimeError as exc:
            logger.warning("Could not stop the Voxtral container: %s", exc)
        finally:
            _stopping = False


async def adopt() -> None:
    """At startup: a server left running by an earlier run gets an idle stop too."""
    if not managed() or config.VOXTRAL_IDLE_STOP_SECONDS <= 0:
        return
    try:
        running = (await _inspect()).get("Running")
    except RuntimeError as exc:
        logger.warning("Cannot check the Voxtral container: %s", exc)
        return
    if running and _users == 0 and _stop_timer is None:
        _arm_stop(config.VOXTRAL_IDLE_STOP_SECONDS)


async def shutdown() -> None:
    """Cancel the idle stop. The container is left as it is."""
    _cancel_stop()
    if _stop_task is not None and not _stop_task.done():
        _stop_task.cancel()


# ── Status ─────────────────────────────────────────────────────────────────────


async def status() -> dict:
    """What the Settings page shows about the server."""
    base = {
        "managed": managed(),
        "state": STATE_UNMANAGED,
        "detail": None,
        "started_at": None,
        "start_triggered_at": _start_triggered_at,
        "in_use": _users > 0,
        "stop_in_seconds": None,
        "idle_stop_seconds": config.VOXTRAL_IDLE_STOP_SECONDS,
    }
    if not managed():
        return base
    try:
        state = await _inspect()
    except RuntimeError as exc:
        return {**base, "state": STATE_ERROR, "detail": str(exc)}
    if _stopping:
        current = STATE_STOPPING
    elif not state.get("Running"):
        current = STATE_STARTING if _starting else STATE_STOPPED
    else:
        current = STATE_RUNNING if await _answering() else STATE_STARTING
    if _stop_deadline is not None and _users == 0:
        base["stop_in_seconds"] = max(0, round(_stop_deadline - time.monotonic()))
    return {
        **base,
        "state": current,
        "started_at": _parse_time(state.get("StartedAt")) if state.get("Running") else None,
    }
