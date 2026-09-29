"""Runtime configuration, read from the environment (never hardcoded secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.hindsight.vectorize.io"
DEFAULT_BANK = "signaldesk-churn"

# The org this agent works for. Shaping these values is what lets one bank hold
# many customer accounts without them bleeding into each other.
DEFAULT_ORG = "Northwind Cloud (vendor)"


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return default


@dataclass(frozen=True)
class Settings:
    """Resolved settings for a single SignalDesk process."""

    base_url: str
    api_key: str
    bank_id: str
    org_name: str
    reflect_budget: str

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = _env("HINDSIGHT_API_KEY", "HINDSIGHT_CLOUD_API_KEY", "HS_KEY")
        if not api_key:
            raise SystemExit(
                "Missing Hindsight credentials.\n"
                "  export HINDSIGHT_API_KEY=hsk_...\n"
                "(HINDSIGHT_CLOUD_API_KEY is also accepted.)"
            )
        return cls(
            base_url=_env("HINDSIGHT_BASE_URL", default=DEFAULT_BASE_URL),
            api_key=api_key,
            bank_id=_env("HINDSIGHT_BANK_ID", default=DEFAULT_BANK),
            org_name=_env("SIGNALDESK_ORG", default=DEFAULT_ORG),
            reflect_budget=_env("SIGNALDESK_BUDGET", default="mid"),
        )
