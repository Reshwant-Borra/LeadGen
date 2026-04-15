"""Auto-generate Google Places textQuery: random lists or one LLM call."""

from __future__ import annotations

import json
import random
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

# Local SMB-style niches + US metros (tweak anytime)
NICHES = [
    "HVAC companies",
    "plumbing services",
    "electrical contractors",
    "roofing contractors",
    "landscaping companies",
    "auto repair shops",
    "dental offices",
    "veterinary clinics",
    "med spas",
    "chiropractors",
    "physical therapy clinics",
    "law firms",
    "accounting firms",
    "insurance agencies",
    "real estate agencies",
    "property management companies",
    "cleaning services",
    "pest control companies",
    "moving companies",
    "storage facilities",
    "gyms",
    "yoga studios",
    "hair salons",
    "barber shops",
    "nail salons",
    "florists",
    "catering companies",
    "restaurants",
    "bakeries",
    "pet grooming",
    "pool service companies",
    "fence installers",
    "window cleaning",
    "pressure washing",
    "home remodeling contractors",
    "auto body shops",
    "daycare centers",
    "tire shops",
]

CITIES = [
    "Dallas TX",
    "Houston TX",
    "Austin TX",
    "San Antonio TX",
    "Phoenix AZ",
    "Tucson AZ",
    "Denver CO",
    "Atlanta GA",
    "Miami FL",
    "Tampa FL",
    "Orlando FL",
    "Jacksonville FL",
    "Charlotte NC",
    "Raleigh NC",
    "Nashville TN",
    "Memphis TN",
    "Indianapolis IN",
    "Columbus OH",
    "Cleveland OH",
    "Cincinnati OH",
    "Detroit MI",
    "Kansas City MO",
    "St. Louis MO",
    "Minneapolis MN",
    "Milwaukee WI",
    "Chicago IL",
    "Portland OR",
    "Seattle WA",
    "Las Vegas NV",
    "Salt Lake City UT",
    "Oklahoma City OK",
    "Tulsa OK",
    "Albuquerque NM",
    "El Paso TX",
    "San Diego CA",
    "Sacramento CA",
    "San Jose CA",
    "Oakland CA",
    "Louisville KY",
    "Birmingham AL",
    "New Orleans LA",
    "Richmond VA",
    "Virginia Beach VA",
    "Boise ID",
    "Spokane WA",
]

# Random / fallback discovery: skip food & drink defaults (user asked to avoid “coffee shops only” bias).
_NICHES_EXCLUDED_FROM_RANDOM = frozenset(
    {
        "restaurants",
        "bakeries",
        "catering companies",
    }
)


def niches_for_random_draw() -> list[str]:
    """Niches used by random_places_query() (subset of NICHES)."""
    return [n for n in NICHES if n not in _NICHES_EXCLUDED_FROM_RANDOM]


def is_coffee_cafe_food_default_query(text: str) -> bool:
    """
    True if the query reads like a coffee / cafe / restaurant Maps search.
    Used to reject LLM outputs that over-index on cafes.
    """
    low = text.lower()
    # Hyphenated / compound spellings models use to slip past simple "coffee shop" checks
    if re.search(
        r"\b("
        r"coffee[\s\-]*shops?|coffeeshops?|"
        r"coffee[\s\-]*houses?|coffeehouses?|"
        r"coffeehouse|coffee\s+house|"
        r"espresso|starbucks|bubble\s+tea|boba|"
        r"café|cafe"
        r")\b",
        low,
    ):
        return True
    if re.search(
        r"\b(restaurants?|bakeries|brewpubs?|breweries|pizza\s+places?|"
        r"burger\s+joints?|bars?\s+in)\b",
        low,
    ):
        return True
    return False


def random_places_query() -> str:
    """Pick a random niche + city. No API cost. Skips cafe/restaurant-heavy categories."""
    pool = niches_for_random_draw()
    return f"{random.choice(pool)} in {random.choice(CITIES)}"


# How many separate Maps-style strings Auto mode invents per job (each gets its own discover()).
AUTO_DISCOVER_NUM_QUERIES = 5

DISCOVER_AUTO_MULTI_SYSTEM = """You output valid JSON only. No markdown."""

DISCOVER_AUTO_MULTI_USER = """Generate EXACTLY {n} DISTINCT Google Maps style search queries for LOCAL small businesses in the United States.

Rules:
- Return a single JSON object with key "queries" whose value is an array of exactly {n} strings.
- Each string: one line, under 90 characters, e.g. "HVAC companies in Dallas TX".
- Every string must differ in both industry/trade AND city or metro from the others (no duplicate cities).
- No coffee shops, cafés, espresso bars, restaurants, bakeries, breweries, bars, juice bars, or bubble tea.
- Prefer skilled trades and professional SMBs (HVAC, plumbing, dental, legal, insurance, auto repair, salons, gyms, etc.).
- No politics, adult, scams, hate. No quotes inside individual query strings.

Return exactly this shape (with {n} strings in the array):
{{"queries": ["...", "...", "..."]}}
"""


DISCOVER_AUTO_SYSTEM = """You output valid JSON only. No markdown."""

DISCOVER_AUTO_USER = """Generate ONE short Google Maps style search query to find LOCAL small businesses in the United States.

Rules:
- Single line, under 90 characters.
- Must sound like something a human would type in Google Maps (business type + city/region).
- Vary the industry and location each time you answer (do not repeat the same combo as a default).
- Do NOT default to coffee shops, cafes, espresso bars, restaurants, bakeries, breweries, or bars.
  Prefer skilled trades and professional SMBs (examples: HVAC, dental, legal, insurance, auto repair, salons, gyms).
- Legitimate business categories only. No politics, no adult, no scams, no hate.
- No quotes inside the query string.

Return exactly this JSON shape:
{"textQuery":"your query here"}
"""


def llm_places_query(client: OpenAI, model: str, *, max_attempts: int = 4) -> str:
    """
    LLM → textQuery string, with retries when the model repeats café/restaurant defaults.
    Falls back to random_places_query() on repeated failure.
    """
    rejects: list[str] = []
    for attempt in range(max_attempts):
        hint_niche = random.choice(niches_for_random_draw())
        hint_city = random.choice(CITIES)
        extra = ""
        if rejects:
            extra = (
                "\n\nThese queries were rejected (same clichés or food/drink focus). "
                "Pick a completely different industry AND a different US city or metro:\n"
                + "\n".join(f"- {r}" for r in rejects[-4:])
            )
        diversity = (
            f"\n\nDiversity hint for this attempt ({attempt + 1}/{max_attempts}): "
            f'think along the lines of "{hint_niche} in {hint_city}" '
            f"(do not copy verbatim unless it is still a fresh combo)."
        )
        user = DISCOVER_AUTO_USER + extra + diversity
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=1.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": DISCOVER_AUTO_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)
            q = str(data.get("textQuery", "")).strip()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        q = re.sub(r"\s+", " ", q)
        if not q or len(q) > 200:
            continue
        if is_coffee_cafe_food_default_query(q):
            rejects.append(q[:160])
            continue
        if rejects and any(_similar_query(a, q) for a in rejects):
            rejects.append(q[:160])
            continue
        return q[:200]
    return random_places_query()


def _similar_query(a: str, b: str) -> bool:
    """True if b is basically the same query as a (model lightly rephrased)."""
    da = re.sub(r"[^\w\s]", "", a.lower())
    db = re.sub(r"[^\w\s]", "", b.lower())
    if not da or not db:
        return False
    wa, wb = set(da.split()), set(db.split())
    inter = len(wa & wb)
    union = len(wa | wb) or 1
    return inter / union >= 0.55


def _fill_random_distinct_queries(n: int, *, avoid: list[str] | None = None) -> list[str]:
    """Build up to n non-food, mutually dissimilar random niche+city queries."""
    avoid = list(avoid or [])
    out: list[str] = []
    for _ in range(n * 40):
        if len(out) >= n:
            break
        q = random_places_query()
        if is_coffee_cafe_food_default_query(q):
            continue
        if any(_similar_query(q, x) for x in avoid + out):
            continue
        out.append(q)
    while len(out) < n:
        out.append(random_places_query())
    return out[:n]


def invent_n_distinct_places_queries(
    client: OpenAI,
    model: str,
    n: int = AUTO_DISCOVER_NUM_QUERIES,
) -> list[str]:
    """
    Auto discover: one LLM call asking for n distinct Maps queries, validated and
    padded with random niche+city rows so the job always runs n different searches.
    """
    n = max(1, min(int(n), 12))
    picked: list[str] = []

    def _consume_candidates(candidates: list[str]) -> None:
        for raw in candidates:
            q = re.sub(r"\s+", " ", str(raw).strip())
            if not q or len(q) > 200:
                continue
            if is_coffee_cafe_food_default_query(q):
                continue
            if any(_similar_query(q, p) for p in picked):
                continue
            picked.append(q[:200])
            if len(picked) >= n:
                return

    for _attempt in range(2):
        user = DISCOVER_AUTO_MULTI_USER.format(n=n)
        if picked:
            user += (
                "\n\nYou already used these (do not repeat or paraphrase them); "
                "output a fresh set of " + str(n) + " queries:\n"
                + "\n".join(f"- {p}" for p in picked[: n + 2])
            )
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=1.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": DISCOVER_AUTO_MULTI_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)
            arr = data.get("queries")
            if isinstance(arr, list):
                _consume_candidates([str(x) for x in arr])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
            pass
        if len(picked) >= n:
            break

    if len(picked) < n:
        for q in _fill_random_distinct_queries(n - len(picked), avoid=picked):
            if len(picked) >= n:
                break
            if not any(_similar_query(q, p) for p in picked):
                picked.append(q)
    while len(picked) < n:
        picked.append(random_places_query())
    return picked[:n]
