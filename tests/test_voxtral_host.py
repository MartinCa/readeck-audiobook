"""Tests for starting and stopping the Voxtral container; Docker and the server are faked."""

import asyncio

import httpx
import pytest

from app import voxtral_host as host

REAL_ASYNC_CLIENT = httpx.AsyncClient
PROXY = "http://proxy.test:2375"
SERVER = "http://voxtral.test"


class FakeDocker:
    """A container that starts (and becomes ready) after `ready_after` polls."""

    def __init__(self):
        self.running = False
        self.ready = False
        self.calls = []
        self.refuse = None  # an HTTP status the proxy answers every call with
        self.exit_code = None  # the container died while starting
        self.ready_after = 1
        self._polls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(SERVER):
            if self.ready:
                return httpx.Response(200, json={"voices": []})
            raise httpx.ConnectError("not up", request=request)
        self.calls.append((request.method, request.url.path))
        if self.refuse:
            return httpx.Response(self.refuse)
        if request.url.path.endswith("/json"):
            if self.running and self.exit_code is None:
                self._polls += 1
                if self._polls >= self.ready_after:
                    self.ready = True
            state = {
                "Running": self.running,
                "Status": "running" if self.running else "exited",
                "StartedAt": "2026-10-16T21:05:27.123456789Z"
                if self.running
                else "0001-01-01T00:00:00Z",
            }
            if self.exit_code is not None:
                state.update(Running=False, Status="exited", ExitCode=self.exit_code)
            return httpx.Response(200, json={"State": state})
        if request.url.path.endswith("/start"):
            self.running = True
            return httpx.Response(204)
        if request.url.path.endswith("/stop"):
            self.running = self.ready = False
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(host.config, "VOXTRAL_URL", SERVER)
    monkeypatch.setattr(host.config, "DOCKER_PROXY_URL", PROXY)
    monkeypatch.setattr(host.config, "VOXTRAL_CONTAINER", "voxtral")
    monkeypatch.setattr(host.config, "VOXTRAL_IDLE_STOP_SECONDS", 300)
    monkeypatch.setattr(host.config, "VOXTRAL_START_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(host, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(host, "_users", 0)
    for name in ("_stop_timer", "_stop_deadline", "_stop_task", "_start_triggered_at", "_lock"):
        monkeypatch.setattr(host, name, None)
    monkeypatch.setattr(host, "_starting", False)
    monkeypatch.setattr(host, "_stopping", False)
    monkeypatch.setattr(
        host.httpx,
        "AsyncClient",
        lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(fake.handler), **kw),
    )
    yield fake
    host._cancel_stop()


def posts(docker, verb):
    return [c for c in docker.calls if c == ("POST", f"/containers/voxtral/{verb}")]


class TestAcquire:
    async def test_does_nothing_without_a_proxy(self, docker, monkeypatch):
        monkeypatch.setattr(host.config, "DOCKER_PROXY_URL", "")
        await host.acquire()
        assert docker.calls == []
        assert (await host.status())["state"] == "unmanaged"

    async def test_starts_a_stopped_container_and_waits_for_it(self, docker):
        docker.ready_after = 3
        await host.acquire()
        assert len(posts(docker, "start")) == 1
        assert docker.ready
        assert host._start_triggered_at is None  # cleared once it answers

    async def test_leaves_a_ready_server_alone(self, docker):
        docker.running = docker.ready = True
        await host.acquire()
        assert docker.calls == []

    async def test_waits_for_a_container_started_by_someone_else(self, docker):
        docker.running, docker.ready_after = True, 2
        await host.acquire()
        assert posts(docker, "start") == []
        assert docker.ready

    async def test_a_refusing_proxy_names_the_settings_to_fix(self, docker):
        docker.refuse = 403
        with pytest.raises(RuntimeError, match="ALLOW_START=1"):
            await host.acquire()

    async def test_a_container_that_dies_while_starting_is_an_error(self, docker):
        docker.running, docker.exit_code = True, 137
        with pytest.raises(RuntimeError, match="exit code 137"):
            await host.acquire()
        assert host._starting is False

    async def test_gives_up_after_the_start_timeout(self, docker, monkeypatch):
        monkeypatch.setattr(host.config, "VOXTRAL_START_TIMEOUT_SECONDS", 0)
        docker.ready_after = 10**6
        with pytest.raises(RuntimeError, match="did not answer"):
            await host.acquire()

    async def test_concurrent_jobs_start_it_once(self, docker):
        docker.ready_after = 5
        await asyncio.gather(host.acquire(), host.acquire(), host.acquire())
        assert len(posts(docker, "start")) == 1


class TestIdleStop:
    async def test_stops_after_the_idle_period_once_unused(self, docker, monkeypatch):
        monkeypatch.setattr(host.config, "VOXTRAL_IDLE_STOP_SECONDS", 0.05)
        await host.acquire()
        host.release()
        await asyncio.sleep(0.3)
        assert len(posts(docker, "stop")) == 1
        assert not docker.running

    async def test_a_new_job_cancels_the_pending_stop(self, docker, monkeypatch):
        monkeypatch.setattr(host.config, "VOXTRAL_IDLE_STOP_SECONDS", 0.2)
        await host.acquire()
        host.release()
        await host.acquire()
        await asyncio.sleep(0.4)
        assert posts(docker, "stop") == []
        host.release()

    async def test_a_server_in_use_is_not_stopped(self, docker, monkeypatch):
        monkeypatch.setattr(host.config, "VOXTRAL_IDLE_STOP_SECONDS", 0.05)
        await host.acquire()
        await host.acquire()
        host.release()  # one job still holds it
        await asyncio.sleep(0.2)
        assert posts(docker, "stop") == []
        host.release()

    async def test_zero_never_stops(self, docker, monkeypatch):
        monkeypatch.setattr(host.config, "VOXTRAL_IDLE_STOP_SECONDS", 0)
        await host.acquire()
        host.release()
        assert host._stop_timer is None
        assert posts(docker, "stop") == []

    async def test_a_failing_stop_is_logged_not_raised(self, docker, monkeypatch):
        monkeypatch.setattr(host.config, "VOXTRAL_IDLE_STOP_SECONDS", 0.05)
        await host.acquire()
        docker.refuse = 403
        host.release()
        await asyncio.sleep(0.2)
        assert host._stopping is False

    async def test_adopts_a_server_left_running(self, docker):
        docker.running = docker.ready = True
        await host.adopt()
        assert host._stop_timer is not None

    async def test_does_not_adopt_a_stopped_server(self, docker):
        await host.adopt()
        assert host._stop_timer is None


class TestStatus:
    async def test_stopped(self, docker):
        info = await host.status()
        assert info["state"] == "stopped"
        assert info["stop_in_seconds"] is None

    async def test_running_with_the_time_to_shutdown(self, docker):
        docker.running = docker.ready = True
        await host.adopt()
        info = await host.status()
        assert info["state"] == "running"
        assert 0 < info["stop_in_seconds"] <= 300
        assert info["started_at"].year == 2026
        assert info["in_use"] is False

    async def test_started_but_not_answering_is_starting(self, docker):
        docker.running, docker.ready_after = True, 10**6
        assert (await host.status())["state"] == "starting"

    async def test_in_use_hides_the_countdown(self, docker):
        await host.acquire()
        info = await host.status()
        assert info["in_use"] is True
        assert info["stop_in_seconds"] is None
        host.release()

    async def test_an_unreachable_proxy_is_an_error_state(self, docker):
        docker.refuse = 403
        info = await host.status()
        assert info["state"] == "error"
        assert "ALLOW_START" in info["detail"]
