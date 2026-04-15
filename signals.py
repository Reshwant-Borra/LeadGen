"""
Six revenue-focused signals from homepage HTML + visible text
(no_booking_system, call_only_intake, no_instant_response, weak_or_unclear_cta,
clunky_or_long_form, weak_lead_capture).

Accuracy: run ``python -m unittest discover -s tests -p "test_*.py"`` for
regression checks; for new sites, spot-check in a REPL with ``compute_signals``
against what you see in the browser, then add a focused test if a marker mis-fires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup


# Prefer widget/host markers in HTML; avoid generic phrases like "schedule an appointment"
# that appear on phone-only pages.
BOOKING_HTML_MARKERS = (
    "calendly.com",
    "cal.com",
    "booksy.com",
    "fresha.com",
    "mindbodyonline",
    "mindbody.io",
    "square.site",
    "squareup.com/appointments",
    "setmore.com",
    "youcanbook.me",
    "appointlet.com",
    "acuityscheduling.com",
    "simplybook.me",
    "genbook.com",
    "vagaro.com",
    "hubspot.com/meetings",
)

# Narrow text cues (unlikely on pure "call us" pages)
TEXT_ONLINE_BOOKING = re.compile(
    r"\b(online\s+booking|book\s+online|schedule\s+online|"
    r"schedule\s+your\s+appointment\s+online|reserve\s+online)\b",
    re.I,
)

CHAT_MARKERS = (
    "intercom.io",
    "intercomcdn",
    "drift.com",
    "crisp.chat",
    "tawk.to",
    "zendesk.com/embeddable",
    "hubspot.com/conversations-visitor",
    "usemessages.com",
    "facebook.com/plugins/customerchat",
    "fb-customerchat",
)

INSTANT_RESPONSE_PHRASES = (
    "text us",
    "text us at",
    "sms",
    "we respond within",
    "we reply within",
    "response within",
    "minutes",
    "live chat",
    "chat with us",
)

PHONE_INTAKE_PATTERNS = re.compile(
    r"(call\s+(us|today|now)|phone\s*:|call\s+to\s+schedule|call\s+for\s+"
    r"appointments?|leave\s+(a\s+)?message|voicemail|during\s+business\s+hours)",
    re.I,
)

REVENUE_CTA_VERBS = re.compile(
    r"\b(book|schedule|request\s+a\s+quote|get\s+a\s+quote|free\s+quote|"
    r"get\s+quote|call\s+now|contact\s+us|reserve|appointment)\b",
    re.I,
)

NEWSLETTER_MARKERS = re.compile(
    r"\b(subscribe|newsletter|sign\s+up\s+for\s+updates|follow\s+us\s+on)\b",
    re.I,
)

LEAD_PATH_MARKERS = re.compile(
    r"\b(book|schedule|quote|contact\s+us|request|appointment|get\s+in\s+touch)\b",
    re.I,
)


@dataclass
class SignalResult:
    flags: dict[str, bool] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {"flags": self.flags, "evidence": self.evidence}


def extract_visible_text(html: str, max_chars: int = 15_000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    parts: list[str] = []
    if soup.title and soup.title.string:
        parts.append(soup.title.string.strip())
    desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if desc and desc.get("content"):
        parts.append(str(desc["content"]).strip())
    body_text = soup.get_text(separator=" ", strip=True)
    parts.append(body_text)
    text = " \n ".join(p for p in parts if p)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _count_form_inputs(soup: BeautifulSoup) -> int:
    max_inputs = 0
    for form in soup.find_all("form"):
        n = len(form.find_all(["input", "select", "textarea"]))
        max_inputs = max(max_inputs, n)
    return max_inputs


def _has_captcha(html: str) -> bool:
    h = html.lower()
    return "recaptcha" in h or "hcaptcha" in h or "captcha" in h


def compute_signals(html: str, visible_text: str) -> SignalResult:
    low_html = html.lower()
    low_text = visible_text.lower()
    soup = BeautifulSoup(html, "html.parser")
    flags: dict[str, bool] = {}
    evidence: dict[str, str] = {}

    # 1 no_booking_system
    has_booking = any(m in low_html for m in BOOKING_HTML_MARKERS) or bool(
        TEXT_ONLINE_BOOKING.search(low_text[:8000])
    )
    flags["no_booking_system"] = not has_booking
    if flags["no_booking_system"]:
        evidence["no_booking_system"] = "No common online booking/embed patterns found."

    # 5 clunky_or_long_form (needs soup)
    form_inputs = _count_form_inputs(soup)
    captcha = _has_captcha(html)
    flags["clunky_or_long_form"] = form_inputs > 6 or captcha
    if flags["clunky_or_long_form"]:
        evidence["clunky_or_long_form"] = (
            f"Large form ({form_inputs} fields) and/or captcha present."
            if form_inputs > 6
            else "Captcha detected on form."
        )

    # 2 call_only_intake
    phone_lang = bool(PHONE_INTAKE_PATTERNS.search(visible_text[:8000]))
    has_small_form = form_inputs > 0 and form_inputs <= 6
    flags["call_only_intake"] = (
        phone_lang and flags["no_booking_system"] and not has_small_form
    )
    if flags["call_only_intake"]:
        m = PHONE_INTAKE_PATTERNS.search(visible_text[:8000])
        snippet = (m.group(0)[:80] + "…") if m else "Phone/voicemail scheduling language."
        evidence["call_only_intake"] = f"Phone-first language: “{snippet}”; no short contact form."

    # 3 no_instant_response
    has_chat = any(m in low_html for m in CHAT_MARKERS)
    has_instant_phrase = any(p in low_text[:6000] for p in INSTANT_RESPONSE_PHRASES)
    flags["no_instant_response"] = not has_chat and not has_instant_phrase
    if flags["no_instant_response"]:
        evidence["no_instant_response"] = "No chat widget markers; no quick-response/SMS promise in hero area text."

    # 4 weak_or_unclear_cta — first ~2500 chars of visible text
    head = visible_text[:2500]
    verb_matches = list(REVENUE_CTA_VERBS.finditer(head))
    # Many competing links/buttons heuristic: count distinct CTA-like words
    if not verb_matches:
        flags["weak_or_unclear_cta"] = True
        evidence["weak_or_unclear_cta"] = "No clear book/quote/schedule/contact CTA in opening text."
    elif len(verb_matches) >= 5:
        flags["weak_or_unclear_cta"] = True
        evidence["weak_or_unclear_cta"] = "Many competing action phrases near top; no single obvious next step."
    else:
        flags["weak_or_unclear_cta"] = False

    # 6 weak_lead_capture — newsletter/social without lead path in first chunk
    chunk = visible_text[:3500]
    if NEWSLETTER_MARKERS.search(chunk) and not LEAD_PATH_MARKERS.search(chunk):
        flags["weak_lead_capture"] = True
        evidence["weak_lead_capture"] = "Subscribe/social language without clear book/quote/contact path in opening chunk."
    else:
        flags["weak_lead_capture"] = False

    return SignalResult(flags=flags, evidence=evidence)


def signal_priority_hint(flags: dict[str, bool]) -> str:
    """Deterministic ordering hint for the LLM (plan section 3)."""
    if flags.get("call_only_intake") and flags.get("no_booking_system"):
        return "strongest: call_only_intake + no_booking_system (direct booking/revenue leak)"
    if flags.get("clunky_or_long_form") and flags.get("weak_or_unclear_cta"):
        return "strong: clunky_or_long_form + weak_or_unclear_cta (conversion leak)"
    if flags.get("no_instant_response") and (
        flags.get("no_booking_system") or flags.get("call_only_intake")
    ):
        return "strong: no_instant_response paired with booking friction"
    if flags.get("weak_lead_capture"):
        return "medium: weak_lead_capture (funnel leak)"
    fired = [k for k, v in flags.items() if v]
    if not fired:
        return "no signals fired; rely only on excerpt."
    return "priority: " + ", ".join(fired)


def text_sufficient_for_llm(visible_text: str, min_chars: int = 200) -> bool:
    return len(visible_text.strip()) >= min_chars
