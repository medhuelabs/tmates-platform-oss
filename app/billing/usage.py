from __future__ import annotations

from typing import Any, Dict, List, Optional


class UsageTracker:
    """Accumulate token/cost metrics for a run."""

    NUMERIC_FIELDS = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "duration_ms",
    )

    def __init__(self) -> None:
        self._totals: Dict[str, float] = {field: 0.0 for field in self.NUMERIC_FIELDS}
        self._events: List[Dict[str, Any]] = []

    def add(
        self,
        metrics: Optional[Dict[str, Any]],
        *,
        label: Optional[str] = None,
        category: Optional[str] = None,
    ) -> None:
        if not metrics:
            return
        entry: Dict[str, Any] = {}
        if label:
            entry["label"] = label
        if category:
            entry["category"] = category
        model = metrics.get("model")
        if model:
            entry["model"] = model

        has_value = False
        for field in self.NUMERIC_FIELDS:
            if field not in metrics or metrics[field] is None:
                continue
            value = metrics[field]
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            self._totals[field] += numeric
            entry[field] = value
            has_value = True

        if has_value:
            self._events.append(entry)

    def to_metrics(self) -> Optional[Dict[str, Any]]:
        result: Dict[str, Any] = {}
        for field, total in self._totals.items():
            if not total:
                continue
            if field == "estimated_cost_usd":
                result[field] = round(total, 6)
            elif field in {"prompt_tokens", "completion_tokens", "total_tokens"}:
                result[field] = int(total)
            elif field == "duration_ms":
                result[field] = int(total)
            else:
                result[field] = total
        return result or None

    def summary_lines(self) -> List[str]:
        lines: List[str] = []
        for entry in self._events:
            label = entry.get("label") or entry.get("model") or "usage"
            parts: List[str] = []
            tokens = (
                entry.get("total_tokens")
                or (
                    (entry.get("prompt_tokens") or 0)
                    + (entry.get("completion_tokens") or 0)
                )
            )
            if tokens:
                parts.append(f"{int(tokens)} tokens")
            cost = entry.get("estimated_cost_usd")
            if cost:
                parts.append(f"${float(cost):.6f}")
            duration = entry.get("duration_ms")
            if duration:
                parts.append(f"{int(duration)} ms")
            if parts:
                lines.append(f"{label}: {', '.join(parts)}")
        return lines

    def has_usage(self) -> bool:
        return any(value for value in self._totals.values())
