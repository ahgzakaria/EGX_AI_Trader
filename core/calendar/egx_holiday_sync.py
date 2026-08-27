"""Official EGX holiday sync — conservative discovery, never silent confirmation.

Fetches OFFICIAL EGX sources only, scans for explicit closure language (Arabic +
English), extracts candidate closures, and ingests them as **NEEDS_REVIEW** unless
they satisfy a strict auto-confirm policy. It never confirms from government/bank
lists, search snippets, third-party sites, or feed silence. Runs dry-run by default;
`commit=True` writes discoveries (still unconfirmed) and updates sync state.

Safety: this module only ever writes DISCOVERED / NEEDS_REVIEW records and sync
state. It never edits Python, never writes a CONFIRMED holiday automatically unless
the strict policy in `classify_confirmation` holds (official domain + explicit
wording + explicit EGX-trading closure + resolved date + no conflict).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import re
from urllib import error as urlerror
from urllib import request as urlrequest
from zoneinfo import ZoneInfo

from core.calendar.egx_calendar_service import CalendarService, service

CAIRO = ZoneInfo("Africa/Cairo")

# Official EGX sources only (auto-confirm is gated on these domains). URLs are
# best-effort entry points; parsing is resilient to layout and never depends on a
# single fragile selector.
OFFICIAL_SOURCES = (
    {"name": "EGX Trading Calendar", "source_type": "EGX_TRADING_CALENDAR",
     "url": "https://www.egx.com.eg/en/homepage.aspx"},
    {"name": "EGX News / Disclosures", "source_type": "EGX_NEWS",
     "url": "https://www.egx.com.eg/en/News.aspx"},
)
OFFICIAL_DOMAINS = ("egx.com.eg",)

# Discovery-only sources. These can raise a candidate for an operator to look at
# and can never confirm one: `classify_confirmation` requires an OFFICIAL_DOMAINS
# url AND an official source_type, and a row from here satisfies neither. The
# separation is structural rather than a rule to remember.
#
# They exist because EGX's own hosts refuse an identified client. Measured on
# 2026-08-27: www.egx.com.eg and egx.com.eg reset the connection for
# `EGX-Trader-CalendarSync/1.0`, and beta.egx.com.eg answers 200 with a WAF
# "Request Rejected" body. All three serve normally to a browser user-agent.
# Presenting a browser string to defeat that is not something this project does,
# so the official tier stays wired to EGX and simply reports itself unreachable,
# while discovery runs against a source that accepts an honest client.
#
# This matters more in Egypt than the split might suggest: a public holiday
# falling mid-week is moved by decree to a Thursday or a Sunday, so the date the
# exchange actually closes is not derivable from a published list in advance --
# it has to be picked up from the announcement.
DISCOVERY_SOURCES = (
    {"name": "Mubasher — EGX news", "source_type": "THIRD_PARTY_DISCOVERY",
     "url": "https://www.mubasher.info/news/eg/now/latest"},
)

# Explicit closure language — Arabic + English. Presence is necessary but NOT
# sufficient for auto-confirmation (date + EGX-trading scope must also be explicit).
CLOSURE_PATTERNS_AR = (
    "تعطيل التداول", "توقف التداول", "إجازة البورصة", "إجازة رسمية",
    "تستأنف البورصة التداول", "تعطل التداول", "لا يوجد تداول",
)
CLOSURE_PATTERNS_EN = (
    "trading will be suspended", "market will be closed", "exchange holiday",
    "official holiday", "trading will resume", "no trading", "public holiday",
    "trading calendar",
)
# Language that signals ambiguity → force NEEDS_REVIEW even if a date is present.
AMBIGUOUS_PATTERNS = (
    "بمناسبة", "next thursday", "on the occasion of", "government holiday",
    "banks will be closed", "بنوك", "ترحيل الإجازة", "بدلاً من",
)

_AR_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5,
    "يونيو": 6, "يونية": 6, "يوليو": 7, "يولية": 7, "أغسطس": 8, "اغسطس": 8,
    "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}
_EN_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}


@dataclass
class Candidate:
    date: str | None
    name_en: str
    name_ar: str
    closure_type: str
    source_type: str
    source_url: str
    source_title: str
    evidence: str
    confidence: float
    ambiguous: bool

    def as_record(self):
        return {
            "date": self.date, "name_en": self.name_en, "name_ar": self.name_ar,
            "exchange": "EGX", "closure_type": self.closure_type,
            "status": "NEEDS_REVIEW", "source_type": self.source_type,
            "source_url": self.source_url, "source_title": self.source_title,
            "announcement_date": "", "confidence": round(self.confidence, 2),
            "notes": self.evidence[:180], "source_hash": _hash_text(self.evidence + (self.date or "")),
        }


def _hash_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def fetch(url, timeout=12):
    """GET a URL (stdlib only, no login). Returns text or raises for the caller."""
    req = urlrequest.Request(url, headers={"User-Agent": "EGX-Trader-CalendarSync/1.0"})
    with urlrequest.urlopen(req, timeout=timeout) as resp:      # noqa: S310 (official https)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read(2_000_000).decode(charset, errors="replace")


def _strip_html(html):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _resolve_date(snippet, year_hint=None):
    """Best-effort EGX/Cairo date from a snippet; None when not explicit."""
    # ISO / numeric d/m/y
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", snippet)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe_date(y, mo, d)
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](20\d{2})", snippet)
    if m:
        d, mo, y = map(int, m.groups())
        return _safe_date(y, mo, d)
    # "23 July 2026" / Arabic "23 يوليو 2026"
    m = re.search(r"(\d{1,2})\s+([A-Za-z؀-ۿ]+)\s+(20\d{2})", snippet)
    if m:
        d = int(m.group(1)); mon = m.group(2).strip().lower(); y = int(m.group(3))
        mo = _EN_MONTHS.get(mon) or _AR_MONTHS.get(m.group(2).strip())
        if mo:
            return _safe_date(y, mo, d)
    # "23 July" without year → ambiguous unless a hint is supplied
    m = re.search(r"(\d{1,2})\s+([A-Za-z؀-ۿ]+)", snippet)
    if m and year_hint:
        d = int(m.group(1)); mon = m.group(2).strip().lower()
        mo = _EN_MONTHS.get(mon) or _AR_MONTHS.get(m.group(2).strip())
        if mo:
            return _safe_date(year_hint, mo, d)
    return None


def _safe_date(y, mo, d):
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def extract_candidates(text, source, year_hint=None):
    """Scan already-fetched text for explicit EGX closure candidates."""
    plain = _strip_html(text)
    low = plain.lower()
    candidates = []
    matched = [p for p in CLOSURE_PATTERNS_AR if p in plain] + \
              [p for p in CLOSURE_PATTERNS_EN if p in low]
    if not matched:
        return candidates
    # window around each closure keyword for date + name extraction
    for kw in matched:
        idx = plain.find(kw) if kw in plain else low.find(kw)
        window = plain[max(0, idx - 120): idx + 160]
        resolved = _resolve_date(window, year_hint)
        ambiguous = (resolved is None
                     or any(a in window.lower() or a in window for a in AMBIGUOUS_PATTERNS))
        candidates.append(Candidate(
            date=resolved, name_en=_guess_name(window), name_ar=_guess_name_ar(window),
            closure_type="FULL_DAY", source_type=source["source_type"],
            source_url=source["url"], source_title=source["name"],
            evidence=window.strip(), confidence=0.5 if not ambiguous else 0.2,
            ambiguous=ambiguous))
    return candidates


def _guess_name(window):
    m = re.search(r"(?:occasion of|holiday of|for the)\s+([A-Za-z0-9 '\-]{3,40})", window, re.I)
    return (m.group(1).strip() if m else "EGX announced closure")


def _guess_name_ar(window):
    m = re.search(r"بمناسبة\s+([؀-ۿ0-9 ]{3,40})", window)
    return (m.group(1).strip() if m else "")


def classify_confirmation(candidate: Candidate, svc: CalendarService):
    """Strict auto-confirm policy. Returns 'CONFIRMED' only when ALL hold; else
    'NEEDS_REVIEW'. Conservative by construction."""
    if candidate.ambiguous or not candidate.date:
        return "NEEDS_REVIEW"
    domain_ok = any(dom in (candidate.source_url or "") for dom in OFFICIAL_DOMAINS)
    official = candidate.source_type in ("EGX_OFFICIAL", "EGX_TRADING_CALENDAR")
    closure_known = candidate.closure_type in ("FULL_DAY", "EARLY_CLOSE", "LATE_OPEN")
    if not (domain_ok and official and closure_known):
        return "NEEDS_REVIEW"
    if svc.conflicts(candidate.date):
        return "NEEDS_REVIEW"
    return "CONFIRMED"


def run_sync(*, commit=False, svc=None, html_by_source=None, offline=False,
             now=None, year_hint=None):
    """Run one sync pass. Dry-run by default (commit=False writes nothing).

    ``html_by_source``: optional {url: html} to bypass network (tests / offline).
    Returns a run summary; on unreachable sources records CALENDAR_SOURCE_UNAVAILABLE.
    """
    svc = svc or service()
    now_dt = (now or datetime.now(timezone.utc)).astimezone(CAIRO)
    year_hint = year_hint or now_dt.year
    checked, failures, discovered, confirmed = [], [], 0, 0
    ingested = []

    for source in OFFICIAL_SOURCES + DISCOVERY_SOURCES:
        url = source["url"]
        checked.append(source["name"])
        try:
            if html_by_source is not None:
                if url not in html_by_source:
                    raise urlerror.URLError("no fixture")
                text = html_by_source[url]
            elif offline:
                raise urlerror.URLError("offline")
            else:
                text = fetch(url)
        except Exception as error:                         # network / parse safety
            failures.append(f"{source['name']}: {type(error).__name__}")
            continue
        for cand in extract_candidates(text, source, year_hint=year_hint):
            verdict = classify_confirmation(cand, svc)
            record = cand.as_record()
            if verdict == "CONFIRMED":
                record["status"] = "NEEDS_REVIEW"          # still routed via review by default
            if commit:
                res = svc.ingest_discovery(record)
                if res["action"] in ("new", "updated"):
                    discovered += 1
            ingested.append({**record, "auto_verdict": verdict})

    official_names = {source["name"] for source in OFFICIAL_SOURCES}
    official_reachable = sorted(
        name for name in official_names
        if not any(f.startswith(f"{name}:") for f in failures))
    verdict = _overall_verdict(checked, failures, svc,
                               official_unreachable=not official_reachable)
    summary = {
        "at": now_dt.isoformat(timespec="seconds"), "verdict": verdict,
        "sources_checked": checked, "failures": failures,
        # Which tier reached its sources, so "nothing found" can be told apart
        # from "the official tier could not be read at all".
        "official_sources_reachable": official_reachable,
        "discovery_sources_reachable": sorted(
            source["name"] for source in DISCOVERY_SOURCES
            if not any(f.startswith(f"{source['name']}:") for f in failures)),
        "discovered": discovered, "confirmed": confirmed,
        "candidates": ingested, "commit": commit}

    if commit:
        state = svc.read_sync_state()
        runs = state.get("runs", []) or []
        runs.append({k: summary[k] for k in
                     ("at", "verdict", "sources_checked", "failures", "discovered", "confirmed")})
        svc.write_sync_state({
            **state, "last_sync_at": summary["at"], "last_status": "OK" if not failures else "PARTIAL",
            "last_verdict": verdict, "sources_checked": checked, "failures": failures,
            "discovered_count": discovered, "confirmed_count": confirmed,
            "runs": runs[-50:]})
    return summary


def _overall_verdict(checked, failures, svc, official_unreachable=False):
    """The verdict is about the OFFICIAL tier, not the count of sources.

    A discovery source succeeding must never make the official tier's failure
    disappear: only EGX can confirm a closure, so an unreachable EGX means the
    calendar cannot be confirmed from anywhere, however much was discovered.
    """

    if failures and len(failures) >= len(checked):
        return "CALENDAR_SOURCE_UNAVAILABLE"
    if official_unreachable:
        return "OFFICIAL_SOURCE_UNAVAILABLE"
    if svc.conflicts():
        return "CONFLICTING_OFFICIAL_RECORDS"
    return "OK"
