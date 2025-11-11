import os, time
from typing import Tuple, Dict, Any, Optional, List
from openai import OpenAI, AzureOpenAI

# Approximate pricing per 1K tokens (as of 2025)
MODEL_PRICING = {
    "gpt-5-mini": {"input": 0.00025, "cached_input": 0.000025, "output": 0.002},  # $0.25 / $0.025 cached / $2.00 per 1M tokens
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
}

_client = None
_client_is_azure = False
_azure_deployment = None


def _supports_temperature(model: str) -> bool:
    """Determine if the target model accepts the temperature parameter."""
    lowered = (model or "").strip().lower()
    if not lowered:
        return True
    if lowered.startswith("gpt-5"):
        return False
    return True


def openai_client() -> OpenAI:
    global _client, _client_is_azure, _azure_deployment
    if _client is None:
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        preference = (os.getenv("OPENAI_CLIENT") or "").strip().lower()

        def _init_azure(*, explicit: bool) -> OpenAI:
            if not (azure_endpoint and azure_key):
                if explicit:
                    raise RuntimeError(
                        "OPENAI_CLIENT=azure requested but AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are not set"
                    )
                raise RuntimeError(
                    "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set to use Azure OpenAI"
                )
            client = AzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                api_version=azure_version,
            )
            _log(
                "[openai] initialized Azure client",
                "| endpoint:",
                azure_endpoint,
                "| deployment:",
                (azure_deployment or "(model name)"),
            )
            return client

        def _init_openai(*, explicit: bool) -> OpenAI:
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                if explicit:
                    raise RuntimeError("OPENAI_CLIENT=openai requested but OPENAI_API_KEY is not set")
                raise RuntimeError("OPENAI_API_KEY must be set to use the standard OpenAI client")
            client = OpenAI(api_key=key)
            _log("[openai] initialized standard OpenAI client")
            return client

        if preference == "azure":
            _client = _init_azure(explicit=True)
            _client_is_azure = True
            _azure_deployment = azure_deployment
        elif preference == "openai":
            _client = _init_openai(explicit=True)
            _client_is_azure = False
            _azure_deployment = None
        else:
            if azure_endpoint and azure_key:
                _client = _init_azure(explicit=False)
                _client_is_azure = True
                _azure_deployment = azure_deployment
            else:
                _client = _init_openai(explicit=False)
                _client_is_azure = False
                _azure_deployment = None
    return _client


def _log(*parts: Any) -> None:
    from .utils import log as _base_log

    _base_log(*parts)

def call_response_with_metrics(
    *,
    model: str,
    system_prompt: str | None,
    user_prompt: str,
    temperature: float = 0.0,
    response_format: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    start_time = time.time()

    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append(
            {
                "role": "system",
                "content": system_prompt,
            }
        )
    messages.append(
        {
            "role": "user",
            "content": user_prompt,
        }
    )

    client = openai_client()
    client = openai_client()
    target_model = model
    if _client_is_azure and _azure_deployment:
        target_model = _azure_deployment
    provider = "azure" if _client_is_azure else "openai"
    _log(
        f"[openai:{provider}] response model:",
        model,
        "| deployed_as:",
        target_model,
        "| prompt_len:",
        len(user_prompt),
    )

    kwargs: Dict[str, Any] = {
        "model": target_model,
        "input": messages,
    }
    if temperature is not None and _supports_temperature(model):
        kwargs["temperature"] = temperature
    elif temperature is not None and not _supports_temperature(model):
        _log("[openai] temperature parameter omitted; model does not support it")
    kwargs.setdefault("reasoning", {"effort": "low"})
    kwargs.setdefault("text", {"verbosity": "low"})
    if response_format is not None:
        kwargs["response_format"] = response_format

    try:
        resp = client.responses.create(**kwargs)
    except TypeError as exc:
        if "response_format" in str(exc) and "response_format" in kwargs:
            _log(
                "[openai] responses.create does not accept response_format; retrying without it"
            )
            kwargs.pop("response_format", None)
            resp = client.responses.create(**kwargs)
        else:
            raise

    end_time = time.time()
    duration_ms = int((end_time - start_time) * 1000)

    text = getattr(resp, "output_text", "") or ""
    text = text.strip()

    usage = getattr(resp, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", 0) or 0
    cache_tokens = (
        getattr(usage, "cache_read_tokens", None)
        or getattr(usage, "cached_input_tokens", None)
        or getattr(usage, "cache_tokens", None)
        or 0
    )
    total_tokens = getattr(usage, "total_tokens", None) or (input_tokens + output_tokens)

    pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
    input_cost = (input_tokens / 1000) * pricing.get("input", 0)
    cached_cost = (cache_tokens / 1000) * pricing.get("cached_input", pricing.get("input", 0))
    output_cost = (output_tokens / 1000) * pricing.get("output", 0)
    total_cost = input_cost + output_cost + cached_cost

    metrics = {
        "duration_ms": duration_ms,
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_tokens": cache_tokens,
        "cached_cost_usd": round(cached_cost, 6) if cached_cost else 0.0,
        "estimated_cost_usd": round(total_cost, 6),
        "model": model,
    }

    _log(
        f"[openai:{provider}] response output_len:",
        len(text),
        "| duration:",
        f"{duration_ms}ms",
        "| tokens:",
        total_tokens,
        "| cache:",
        cache_tokens,
        "| cost:",
        f"${total_cost:.6f}",
    )

    return text, metrics
