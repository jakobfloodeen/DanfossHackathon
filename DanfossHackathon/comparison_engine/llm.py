"""
LLM - the ONLY place that talks to the Claude API.

    ask_claude(system, prompt, schema) -> str (the JSON text the model wrote)

- The key is read from .env / the environment (ANTHROPIC_API_KEY) and never logged or returned.
- Model from env LLM_MODEL (default claude-sonnet-5-5), effort from env LLM_EFFORT (default low:
  the facts are already sorted and checked by code, so long thinking mostly costs output tokens).
- The system prompt (LOGIC + hard rules, identical for every comparison in a family) is marked for prompt
  caching: a repeat within 5 minutes reads it at 5 % of the input price.
- Every call's token counts and estimated cost are recorded (usage.py -> data/usage.csv).
- No 'temperature': the installed SDK uses an API version that rejects it, and Sonnet 5.5 only accepts
  the default anyway. Consistency comes from: structured JSON output (schema), fixed prompts and our cache.
- If the model's safety check declines the request, the API retries on a fallback model by itself
  (fallbacks="default"). If that option is rejected, we retry once without it.
- Any problem (no key, timeout, API error, refusal) -> LLMUnavailable. interpret() then falls back to
  the code-only result, so the website keeps working.

In tests we never call this: interpret(..., ask=fake_function) is used instead.
"""
import json
import os
import re

from . import config, usage

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(Exception):
    """The AI could not be used (no key, timeout, API error, refusal). The message is safe to show."""


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(config.PROJECT_ROOT / ".env")
    except ImportError:
        pass


def has_api_key() -> bool:
    _load_env()
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def ask_claude(system: str, prompt: str, schema: dict | None = None, model: str | None = None,
               purpose: str = "compare") -> str:
    """One call to Claude. Returns the text of the answer (JSON when a schema is given)."""
    if not has_api_key():
        raise LLMUnavailable("no ANTHROPIC_API_KEY in .env")
    try:
        import anthropic
    except ImportError as e:
        raise LLMUnavailable("the 'anthropic' package is not installed") from e

    client = anthropic.Anthropic(timeout=config.llm_timeout(), max_retries=1)
    output_config = {"effort": config.llm_effort()}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    model = model or config.llm_model()
    request = dict(model=model, max_tokens=16000,
                   system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                   messages=[{"role": "user", "content": prompt}], output_config=output_config)
    try:
        try:
            response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
        except anthropic.BadRequestError:
            try:
                response = client.messages.create(**request)    # fallback option not accepted -> plain call
            except anthropic.BadRequestError as e:
                if "effort" not in str(e).lower():
                    raise
                request["output_config"].pop("effort")          # this model has no effort setting
                response = client.messages.create(**request)
    except anthropic.APITimeoutError as e:
        raise LLMUnavailable(f"the AI did not answer within {config.llm_timeout():.0f} s") from e
    except anthropic.AuthenticationError as e:
        raise LLMUnavailable("the API key was rejected") from e
    except anthropic.RateLimitError as e:
        raise LLMUnavailable("rate limit reached - try again in a minute") from e
    except anthropic.APIStatusError as e:
        raise LLMUnavailable(f"API error {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        raise LLMUnavailable("no connection to the API") from e

    if getattr(response, "usage", None) is not None:
        usage.record(purpose, getattr(response, "model", None) or model, response.usage)
    if response.stop_reason == "refusal":
        raise LLMUnavailable("the model declined this request")
    if response.stop_reason == "max_tokens":
        raise LLMUnavailable("the answer was too long and got cut off")
    text = "".join(b.text for b in response.content if b.type == "text")
    if not text.strip():
        raise LLMUnavailable("the model returned no text")
    return text


def extract_json(text: str) -> dict:
    """Parse the model's JSON. Tolerates ```json fences and text around the object. Raises ValueError."""
    if not isinstance(text, str):
        raise ValueError("answer is not text")
    s = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("answer contains no JSON object")
        try:
            data = json.loads(s[start:end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"answer is not valid JSON: {e.msg}") from e
    if not isinstance(data, dict):
        raise ValueError("answer must be one JSON object")
    return data
