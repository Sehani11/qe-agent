"""Is a fine-tuned model actually being served, and removing the one that is.

The behaviour under test is a distinction that cost a wipe its meaning: the
weights outlive every file in this repository, because `ollama create` copies
them into the runtime's own store. So `status` has to ask the shim rather than
read configuration, and `purge_served_model` has to delete the exact name it
reports — never the shared base model sitting beside it.

And a name is not a model. `/health` echoes a configured string back whether or
not anything stands behind it, so `status` confirms with the runtime; trusting
the name alone left the toggle enabled with nothing to serve.
"""

import httpx
import pytest

from app.services import fine_tuned_serving

#: Captured before any patching. `fine_tuned_serving.httpx` IS the httpx module,
#: so replacing `AsyncClient` on it replaces it everywhere — including inside
#: the factory below, which would then build itself forever.
_RealAsyncClient = httpx.AsyncClient


def _client(handler) -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def shim(monkeypatch):
    """Point the module at a fake shim and record what it is asked to do."""
    monkeypatch.setattr(
        fine_tuned_serving.settings,
        "fine_tuned_model_endpoint",
        "http://shim.test:9000/",
    )
    calls: list[httpx.Request] = []

    def install(handler):
        def recording(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return handler(request)

        monkeypatch.setattr(
            fine_tuned_serving.httpx,
            "AsyncClient",
            lambda **_: _client(recording),
        )
        return calls

    return install


def _health_body(**overrides) -> dict:
    return {
        "status": "ok",
        "model": "bdd-lora-1.5b",
        "ollama": "http://runtime.test:11434",
        **overrides,
    }


def _serving(request: httpx.Request) -> httpx.Response:
    """A shim naming a model, and a runtime that really has it."""
    return httpx.Response(200, json=_health_body())


class TestStatus:
    async def test_reports_the_model_the_shim_says_it_serves(self, shim):
        """Configuration names a URL, not a model. Only the shim knows which."""
        shim(_serving)

        assert await fine_tuned_serving.status() == {
            "available": True,
            "model": "bdd-lora-1.5b",
            "detail": "Serving bdd-lora-1.5b.",
        }

    async def test_a_name_the_runtime_does_not_have_is_not_available(self, shim):
        """The state that broke the gate: the model deleted from the runtime,
        the shim still echoing its configured name."""
        shim(
            lambda request: httpx.Response(200, json=_health_body())
            if request.url.path == "/health"
            else httpx.Response(404, json={"error": "model not found"})
        )

        result = await fine_tuned_serving.status()

        assert result["available"] is False
        assert "no longer loaded" in str(result["detail"])

    async def test_an_unreachable_runtime_is_not_available(self, shim):
        """Generation would fail against it too — this is not a working model."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json=_health_body())
            raise httpx.ConnectError("no listener")

        shim(handler)

        result = await fine_tuned_serving.status()

        assert result["available"] is False
        assert "runtime is not responding" in str(result["detail"])

    async def test_a_shim_naming_no_runtime_is_taken_at_its_word(self, shim):
        """Nothing to cross-check against: a shim on some other backend gives
        the only evidence there is, and disabling a working setup is worse."""
        shim(lambda _: httpx.Response(200, json=_health_body(ollama="")))

        assert (await fine_tuned_serving.status())["available"] is True

    async def test_an_unreachable_shim_is_unavailable_not_an_error(self, shim):
        """The caller renders a toggle; it should not have to catch anything."""

        def refuse(_):
            raise httpx.ConnectError("no listener")

        shim(refuse)

        result = await fine_tuned_serving.status()
        assert result["available"] is False
        assert result["model"] is None
        assert "not responding" in str(result["detail"])

    async def test_an_unset_endpoint_does_not_reach_the_network(
        self, shim, monkeypatch
    ):
        monkeypatch.setattr(
            fine_tuned_serving.settings, "fine_tuned_model_endpoint", ""
        )
        calls = shim(_serving)

        result = await fine_tuned_serving.status()

        assert result["available"] is False
        assert calls == []

    async def test_a_shim_serving_nothing_is_unavailable(self, shim):
        """Answering /health is not the same as having a model behind it."""
        shim(lambda _: httpx.Response(200, json=_health_body(model="")))

        assert (await fine_tuned_serving.status())["available"] is False

    async def test_health_is_taken_from_the_origin_not_the_generate_path(
        self, shim, monkeypatch
    ):
        """/health sits beside the generate route, not under it."""
        monkeypatch.setattr(
            fine_tuned_serving.settings,
            "fine_tuned_model_endpoint",
            "http://shim.test:9000/generate",
        )
        calls = shim(_serving)

        await fine_tuned_serving.status()

        assert str(calls[0].url) == "http://shim.test:9000/health"


class TestPurgingTheServedModel:
    async def test_deletes_exactly_the_name_the_shim_reports(self, shim):
        """Named, not guessed: the base model it was built from lives in the
        same store, is shared with everything else on the machine, and is not
        ours to remove."""
        calls = shim(
            lambda request: httpx.Response(200, json=_health_body())
            if request.url.path == "/health"
            else httpx.Response(200, json={})
        )

        assert await fine_tuned_serving.purge_served_model() == "bdd-lora-1.5b"

        delete = calls[-1]
        assert delete.method == "DELETE"
        assert str(delete.url) == "http://runtime.test:11434/api/delete"
        assert delete.read() == b'{"model":"bdd-lora-1.5b"}'

    async def test_a_shim_that_is_down_leaves_the_runtime_alone(self, shim):
        """Without a name there is nothing safe to delete — deleting a guess
        would take the shared base model with it."""

        def refuse(_):
            raise httpx.ConnectError("no listener")

        calls = shim(refuse)

        assert await fine_tuned_serving.purge_served_model() is None
        assert all(c.method == "GET" for c in calls)

    async def test_a_failing_delete_does_not_raise(self, shim):
        """The caller is a wipe that has already committed."""
        shim(
            lambda request: httpx.Response(200, json=_health_body())
            if request.url.path == "/health"
            else httpx.Response(500)
        )

        assert await fine_tuned_serving.purge_served_model() is None
