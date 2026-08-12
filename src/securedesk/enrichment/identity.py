from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_STATE_CODES = set(_STATE_NAMES.values())


def canonical_linkedin_url(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("www."):
        text = "https://" + text
    elif text.casefold().startswith("linkedin.com/"):
        text = "https://www." + text
    parsed = urlsplit(text)
    host = (parsed.hostname or "").casefold()
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    if host not in {"linkedin.com", "www.linkedin.com"} and not host.endswith(
        ".linkedin.com"
    ):
        return None
    if not path.casefold().startswith("/in/"):
        return None
    return urlunsplit(("https", "www.linkedin.com", path, "", ""))


def split_location(
    location: str | None,
    explicit_city: str | None = None,
    explicit_state: str | None = None,
) -> tuple[str | None, str | None]:
    city = " ".join((explicit_city or "").split()).strip() or None
    state = normalize_state(explicit_state)
    if not location:
        return city, state
    cleaned = " ".join(location.split()).strip()
    parts = [part.strip() for part in re.split(r"[,|]", cleaned) if part.strip()]
    if not state:
        for part in reversed(parts):
            candidate = normalize_state(part)
            if candidate:
                state = candidate
                break
    if not city and parts:
        candidate = parts[0]
        if normalize_state(candidate) is None and not re.search(r"\d", candidate):
            city = candidate
    return city, state


def normalize_state(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z ]", "", value).strip()
    if cleaned.upper() in _STATE_CODES:
        return cleaned.upper()
    return _STATE_NAMES.get(cleaned.casefold())
