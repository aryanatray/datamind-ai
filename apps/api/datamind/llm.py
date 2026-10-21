import asyncio
import json
import random
import re
import time
from contextlib import asynccontextmanager

from fastapi import HTTPException
from google import genai
from sqlalchemy.orm import Session

from datamind.config import settings
from datamind.db import Usage
from datamind.limits import reserve_tokens, settle_tokens
from datamind.schemas import Plan
from datamind.security import Principal


@asynccontextmanager
async def client():
    if not settings().gemini_api_key:
        raise HTTPException(503, "The platform operator must configure GEMINI_API_KEY in the backend .env")
    instance = genai.Client(
        api_key=settings().gemini_api_key, http_options={"timeout": 60000, "retry_options": {"attempts": 1}}
    )
    try:
        yield instance.aio
    finally:
        await instance.aio.aclose()


def record(db: Session, actor: Principal, usage, started: float, keys: list[str], reserved: int) -> dict:
    incoming = int(getattr(usage, "total_input_tokens", 0) or 0)
    outgoing = int(getattr(usage, "total_output_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", incoming + outgoing) or incoming + outgoing)
    db.add(
        Usage(
            user_id=actor.user_id,
            workspace_id=actor.workspace_id,
            model=settings().gemini_model,
            input_tokens=incoming,
            output_tokens=total - incoming,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    )
    if usage is not None:
        settle_tokens(db, keys, reserved, total)
    else:
        db.commit()
    return {
        "input_tokens": incoming,
        "output_tokens": outgoing,
        "total_tokens": total,
        "usage_reported": usage is not None,
        "cached_tokens": int(getattr(usage, "total_cached_tokens", 0) or 0),
    }


def provider_error(exc: Exception) -> dict:
    # Retain only the error envelope, never request headers or the complete response body.
    try:
        body = json.loads(getattr(exc, "body", "") or "{}")
    except (ValueError, TypeError):
        body = {}
    if isinstance(body, list):
        body = body[0] if body else {}
    error = body.get("error", {}) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        error = {}

    def clean(value):
        text = str(value)
        key = settings().gemini_api_key
        if key:
            text = text.replace(key, "[redacted]")
        text = re.sub(r"AIza[\w-]+", "[redacted]", text)
        text = re.sub(r"(?i)bearer\s+[^\s,;\"']+", "Bearer [redacted]", text)
        text = re.sub(
            r"(?i)(authorization|password|api[_-]?key)([\s\"']*[:=][\s\"']*)([^\s,;\"']+)",
            r"\1\2[redacted]",
            text,
        )
        return " ".join(text.split())[:1000]

    return {
        "code": clean(error.get("status") or error.get("code") or type(exc).__name__),
        "message": clean(
            error.get("message") or "Provider rejected the request; no structured reason available."
        ),
        "invalid_key": "API_KEY_INVALID" in json.dumps(error) or "API_KEY_INVALID" in str(exc),
    }


async def create_with_retry(api, trace: list, **kwargs):
    for attempt in range(3):
        try:
            return await api.interactions.create(**kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            diagnostic = provider_error(exc)
            if status == 400 and diagnostic["invalid_key"]:
                raise HTTPException(
                    503,
                    "Google rejected GEMINI_API_KEY as invalid. The platform operator must replace it "
                    "in the backend .env and restart the API and worker.",
                ) from exc
            if status not in {429, 500, 502, 503, 504} or attempt == 2:
                trace.append(
                    {
                        "stage": "gemini_error",
                        "status": status,
                        "attempt": attempt + 1,
                        "code": diagnostic["code"],
                        "message": diagnostic["message"],
                    }
                )
                advice = {
                    400: "Google rejected the request format or parameters. Narrow the selected sources; the operator can inspect the sanitized provider reason.",
                    401: "Google rejected authentication. Ask the operator to check the backend API key.",
                    403: "Google denied access. Ask the operator to check model access, project permissions and regional availability.",
                    429: "Google's quota or rate limit was reached. Wait before retrying; check the provider quota if it persists.",
                }.get(
                    status,
                    "The provider is unavailable or timed out. Retry later; no database write was performed.",
                )
                raise HTTPException(
                    502,
                    f"Gemini request failed ({status or type(exc).__name__}). "
                    + advice
                    + " Open Sources & execution for details.",
                ) from exc
            trace.append({"stage": "gemini_retry", "attempt": attempt + 1, "category": status})
            await asyncio.sleep(min(4, 2**attempt) + random.random() * 0.2)


def plan_schema(source_ids: list[str]) -> dict:
    schema = Plan.model_json_schema()
    step = schema["$defs"]["ToolCall"]
    identifier = step["properties"]["source_id"]
    identifier.pop("default", None)
    identifier["enum"] = sorted(set(source_ids))
    identifier["description"] = (
        "Copy an exact selected source id for every step; never use its name or omit it."
    )
    step["required"] = [*step["required"], "source_id"]
    return inline_schema(schema)


def inline_schema(schema: dict) -> dict:
    """Export self-contained provider schemas; retain server-side Pydantic validation."""
    definitions = schema.get("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand(
                {
                    **definitions[value["$ref"].split("/")[-1]],
                    **{k: v for k, v in value.items() if k != "$ref"},
                }
            )
        return {k: expand(v) for k, v in value.items() if k not in {"$defs", "title", "default"}}

    return expand(schema)


async def plan(db: Session, actor: Principal, prompt: str, trace: list, source_ids: list[str]) -> Plan:
    if len(prompt.encode()) > 100_000:
        raise HTTPException(
            400, "Selected source metadata exceeds the planning budget. Choose fewer sources."
        )
    output_limit = settings().gemini_max_output_tokens
    reserved = len(prompt.encode()) + output_limit
    started = time.perf_counter()
    async with client() as api:
        keys = reserve_tokens(db, actor, reserved * 3)
        response = await create_with_retry(
            api,
            trace,
            model=settings().gemini_model,
            store=False,
            input=prompt,
            tools=[
                {
                    "type": "function",
                    "name": "propose_plan",
                    "description": "Submit a bounded data investigation plan or clarification",
                    "parameters": plan_schema(source_ids),
                }
            ],
            generation_config={"max_output_tokens": output_limit, "tool_choice": "any"},
        )
    usage = record(db, actor, response.usage, started, keys, reserved * 3)
    trace.append(
        {"stage": "gemini_plan", "usage": usage, "latency_ms": round((time.perf_counter() - started) * 1000)}
    )
    calls = [step for step in response.steps if step.type == "function_call" and step.name == "propose_plan"]
    if len(calls) != 1:
        raise HTTPException(502, "Gemini returned an invalid plan")
    arguments = calls[0].arguments
    return Plan.model_validate(json.loads(arguments) if isinstance(arguments, str) else arguments)


async def structured(db: Session, actor: Principal, prompt: str, schema, trace: list):
    output_limit = min(4096, settings().gemini_max_output_tokens)
    reserved = (len(prompt.encode()) + output_limit) * 3
    started = time.perf_counter()
    async with client() as api:
        keys = reserve_tokens(db, actor, reserved)
        response = await create_with_retry(
            api,
            trace,
            model=settings().gemini_model,
            store=False,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": inline_schema(schema.model_json_schema()),
            },
            generation_config={"max_output_tokens": output_limit},
        )
    usage = record(db, actor, response.usage, started, keys, reserved)
    trace.append(
        {
            "stage": "gemini_structured",
            "schema": schema.__name__,
            "usage": usage,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    )
    return schema.model_validate_json(response.output_text)


async def stream(db: Session, actor: Principal, prompt: str, trace: list):
    output_limit = settings().gemini_max_output_tokens
    reserved = (len(prompt.encode()) + output_limit) * 3
    started = time.perf_counter()
    usage = None
    async with client() as api:
        keys = reserve_tokens(db, actor, reserved)
        response = await create_with_retry(
            api,
            trace,
            model=settings().gemini_model,
            store=False,
            input=prompt,
            stream=True,
            generation_config={"max_output_tokens": output_limit},
        )
        try:
            async for event in response:
                if event.event_type == "step.delta" and getattr(event.delta, "type", None) == "text":
                    yield {"text": event.delta.text}
                elif event.event_type == "interaction.completed":
                    usage = event.interaction.usage
                elif event.event_type in {"interaction.failed", "error"}:
                    raise HTTPException(502, "Gemini generation failed")
        finally:
            await response.close()
    trace.append({"stage": "gemini_synthesis", "latency_ms": round((time.perf_counter() - started) * 1000)})
    yield {"usage": record(db, actor, usage, started, keys, reserved)}
