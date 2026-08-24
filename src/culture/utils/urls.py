from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Parameters that never change page content. Kept conservative on purpose:
# dropping a meaningful parameter would wrongly merge different URLs.
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "igsh"}
TRACKING_PREFIXES = ("utm_",)


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    Strips tracking parameters and fragments, lowercases scheme/host, removes
    default ports and trailing slashes. Content-affecting query parameters are
    preserved in their original order.
    """
    url = url.strip()
    scheme, netloc, path, query, _fragment = urlsplit(url)

    scheme = scheme.lower()
    netloc = netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    if path != "/":
        path = path.rstrip("/")

    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key not in TRACKING_PARAMS and not key.startswith(TRACKING_PREFIXES)
    ]
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))
