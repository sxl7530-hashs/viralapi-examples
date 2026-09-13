"""Safe pre-release configuration checks for a ViralAPI integration."""

import os
from urllib.parse import urlparse


def check_config() -> dict[str, object]:
    api_key = os.getenv("VIRALAPI_API_KEY")
    base_url = os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1")
    parsed = urlparse(base_url)
    if not api_key:
        raise RuntimeError("missing VIRALAPI_API_KEY")
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("VIRALAPI_BASE_URL must be HTTPS")
    return {"endpoint_host": parsed.netloc, "api_key_present": True}


if __name__ == "__main__":
    print(check_config())
    print("config-ok; set VIRALAPI_PROBE_LIVE=1 for a staging smoke test")
