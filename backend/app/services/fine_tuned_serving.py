"""What the fine-tuned model is, as opposed to how it generates.

`bdd_model/fine_tuned_provider` calls the shim to produce scenarios. This module
answers the two questions that are about the shim itself rather than about a
generation: is a fine-tuned model actually being served right now, and remove
the one that is.

Both start at the shim's `/health` rather than at configuration, because
configuration does not know the answer. `FINE_TUNED_MODEL_ENDPOINT` names a URL,
not a model — the shim resolves `FINE_TUNED_OLLAMA_MODEL` and `OLLAMA_BASE_URL`
in its own process, and reports what it landed on.

Neither stops there. `/health` reports a configured NAME, echoed back whether or
not anything stands behind it, so the runtime it points at is asked to confirm
the model is really loaded. A name is not a model — treating it as one is what
let the toggle stay enabled with nothing to serve.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Short on purpose. Both callers are interactive — a toggle deciding whether to
#: render disabled, and the tail of a wipe — so a shim that is down should read
#: as "not serving" in a moment rather than hold the request for a timeout.
_TIMEOUT_SECONDS = 5.0


def _root(url: str) -> str | None:
    """Reduce the configured endpoint to the shim's origin, or None.

    The endpoint points at the generate route (`POST /`), while `/health` sits
    beside it. Rebuilding from scheme and host rather than string-trimming keeps
    this correct whether the value is `http://host:9000`, `.../`, or a path the
    generate route was moved to.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    netloc = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, "", "", ""))


async def health() -> dict[str, object] | None:
    """Fetch the shim's `/health`, or None if there is nothing to fetch it from."""
    root = _root(settings.fine_tuned_model_endpoint or "")
    if root is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{root}/health")
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Fine-tuned shim unreachable at %s: %s", root, type(exc).__name__)
        return None
    return body if isinstance(body, dict) else None


async def model_exists(runtime: str, model: str) -> bool | None:
    """Is `model` actually loaded in the runtime? None if that cannot be told.

    The question `/health` does NOT answer. `MODEL_NAME` there is the shim's
    configured env value, echoed back whether or not anything stands behind it —
    so a model deleted from the runtime leaves the shim happily reporting the
    name of something that is gone. Reading that name as existence is what let
    the toggle stay enabled with nothing to serve.

    Ollama answers `/api/show` with 404 for a name it does not have, which is
    the distinction this needs and the only place it is available.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{runtime.rstrip('/')}/api/show", json={"model": model}
            )
    except httpx.HTTPError as exc:
        logger.info("Could not verify model %s: %s", model, type(exc).__name__)
        return None
    if response.status_code == 404:
        return False
    return response.is_success or None


async def status() -> dict[str, object]:
    """Report whether a fine-tuned model is being served, and which.

    The toggle is a stored preference, so without this it stays flippable after
    the model behind it is gone — generation then falls back to the general LLM
    and silently produces something other than what the switch says. Answering
    "no model" lets the control say so instead.

    Never raises. Every failure — unset endpoint, shim down, malformed body —
    is the same answer for the caller: there is nothing to switch on.
    """
    if not (settings.fine_tuned_model_endpoint or "").strip():
        return {
            "available": False,
            "model": None,
            "detail": "No fine-tuned endpoint is configured on this server.",
        }

    body = await health()
    if body is None:
        return {
            "available": False,
            "model": None,
            "detail": "The fine-tuned model service is not responding.",
        }

    model = body.get("model")
    if not isinstance(model, str) or not model:
        return {
            "available": False,
            "model": None,
            "detail": "The fine-tuned model service is not serving a model.",
        }

    # The shim names a model; the runtime is what decides whether it is there.
    # Only checkable when the shim says which runtime it uses — with none
    # reported there is no second opinion to get, and the name it gave is the
    # only evidence available.
    runtime = body.get("ollama")
    if isinstance(runtime, str) and runtime:
        exists = await model_exists(runtime, model)
        if exists is False:
            return {
                "available": False,
                "model": None,
                "detail": f"{model} is no longer loaded in the model runtime.",
            }
        if exists is None:
            return {
                "available": False,
                "model": None,
                "detail": "The model runtime is not responding.",
            }

    return {"available": True, "model": model, "detail": f"Serving {model}."}


async def purge_served_model() -> str | None:
    """Delete the served model from the runtime holding it. Returns its name.

    The last copy of a fine-tune does not live in this repository. `ollama
    create` imports the GGUF into Ollama's own blob store, so deleting
    `training/outputs` removes the source and leaves the model itself running —
    which is how a wiped account can still generate from a fine-tune.

    Deletes only the exact name `/health` reports. That matters: the base model
    the fine-tune was built from (`qwen2.5:1.5b-instruct` here) sits in the same
    store, is shared with everything else on the machine, and is not ours to
    remove. Asking the shim what it serves is what keeps this from guessing.

    Best-effort and never raises — the caller is a wipe that has already
    committed.
    """
    body = await health()
    if body is None:
        return None

    model = body.get("model")
    runtime = body.get("ollama")
    if not isinstance(model, str) or not model:
        return None
    if not isinstance(runtime, str) or not runtime:
        logger.info("Shim reports model %s but no runtime to delete it from", model)
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            # DELETE with a body: Ollama's delete API takes the name in JSON
            # rather than in the path, which httpx only allows via `request`.
            response = await client.request(
                "DELETE",
                f"{runtime.rstrip('/')}/api/delete",
                json={"model": model},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "Could not delete served model %s: %s", model, type(exc).__name__
        )
        return None

    logger.info("Deleted served fine-tuned model %s", model)
    return model
