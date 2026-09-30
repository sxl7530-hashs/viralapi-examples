"""Small-team multi-model gateway admission example.

This no-network example models route selection and budget admission for
customer support, content drafts, and data analysis.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    models: tuple[str, ...]
    cost_group: str
    deadline_s: int
    customer_visible: bool
    allow_fallback: bool


ROUTES = {
    "support_reply": Route(("YOUR_CLAUDE_MODEL_ID", "YOUR_GPT_MODEL_ID"), "stable_official", 35, True, False),
    "content_draft": Route(("YOUR_GPT_MODEL_ID", "YOUR_GEMINI_MODEL_ID"), "welfare", 60, False, True),
    "data_analysis": Route(("YOUR_CLAUDE_MODEL_ID", "YOUR_GPT_MODEL_ID"), "official_transfer", 90, False, True),
}


def admit(route_name: str, remaining_cents: int, reserve_cents: int) -> Route:
    route = ROUTES[route_name]
    if remaining_cents < reserve_cents:
        kind = "customer-visible request requires handoff" if route.customer_visible else "enqueue or reject batch work"
        raise RuntimeError(f"budget_guard: {kind}")
    return route


if __name__ == "__main__":
    route = admit("content_draft", remaining_cents=100, reserve_cents=20)
    print({"route": "content_draft", "primary_model": route.models[0], "cost_group": route.cost_group})
