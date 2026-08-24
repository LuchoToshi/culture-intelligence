import time

import httpx

from culture.logging import get_logger

log = get_logger("culture.http")

# Several fashion publications block default library user agents.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def create_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
    )


def get_with_retries(client: httpx.Client, url: str, max_attempts: int = 3) -> httpx.Response:
    """GET with conservative exponential backoff on transient failures."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.get(url)
            if response.status_code in RETRYABLE_STATUS and attempt < max_attempts:
                log.debug(
                    "retryable status %s from %s (attempt %d)", response.status_code, url, attempt
                )
            else:
                response.raise_for_status()
                return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status not in RETRYABLE_STATUS:
                raise  # 403/404 etc. will not improve on retry
            if attempt == max_attempts:
                raise
            log.debug("fetch failed for %s (attempt %d): %s", url, attempt, exc)
        time.sleep(2**attempt)  # 2s, 4s
    raise AssertionError(f"retry loop exited without result for {url}") from last_error
