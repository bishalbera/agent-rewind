
from __future__ import annotations

from typing import Final

#: (input_per_mtok, output_per_mtok) in USD.
PRICING: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one LLM call. Returns 0.0 for an unpriced model (a visible signal)."""
    rate = PRICING.get(model)
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
