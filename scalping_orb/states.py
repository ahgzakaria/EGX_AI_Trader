"""Typed Phase 2B Core research states, labels and rule codes.

Research only. The maximum state this module admits is
``ENTRY_READY_RESEARCH`` — a deterministic research candidate, never a buy
instruction. Trade-management states (`TRADE_ACTIVE`, `PARTIAL_EXIT`,
`TRAILING`, `EXITED`) belong to a later phase and are deliberately absent, so
no code path can reach them.
"""

from __future__ import annotations

from enum import Enum


class OrbResearchState(str, Enum):
    """Every state the Phase 2B Core engine may occupy."""

    WAIT_OPENING_RANGE = "WAIT_OPENING_RANGE"
    WAIT_BREAKOUT = "WAIT_BREAKOUT"
    BREAKOUT_CANDIDATE = "BREAKOUT_CANDIDATE"
    BREAKOUT_REJECTED_WICK_ONLY = "BREAKOUT_REJECTED_WICK_ONLY"
    BREAKOUT_REJECTED_STALE = "BREAKOUT_REJECTED_STALE"
    BREAKOUT_REJECTED_POOR_QUALITY = "BREAKOUT_REJECTED_POOR_QUALITY"
    BREAKOUT_TOO_EXTENDED = "BREAKOUT_TOO_EXTENDED"
    MOMENTUM_QUALIFIED_RESEARCH = "MOMENTUM_QUALIFIED_RESEARCH"
    WAIT_FIRST_PULLBACK = "WAIT_FIRST_PULLBACK"
    FIRST_PULLBACK_IN_PROGRESS = "FIRST_PULLBACK_IN_PROGRESS"
    PULLBACK_HEALTHY = "PULLBACK_HEALTHY"
    PULLBACK_PRICE_ONLY = "PULLBACK_PRICE_ONLY"
    PULLBACK_TOO_DEEP = "PULLBACK_TOO_DEEP"
    PULLBACK_VOLUME_EXPANSION = "PULLBACK_VOLUME_EXPANSION"
    PULLBACK_STRUCTURE_FAILED = "PULLBACK_STRUCTURE_FAILED"
    WAIT_RECLAIM = "WAIT_RECLAIM"
    RECLAIM_CANDIDATE = "RECLAIM_CANDIDATE"
    RECLAIM_FAILED = "RECLAIM_FAILED"
    ENTRY_READY_RESEARCH = "ENTRY_READY_RESEARCH"
    ENTRY_EXPIRED = "ENTRY_EXPIRED"
    LATE_SESSION = "LATE_SESSION"
    AUCTION_PHASE = "AUCTION_PHASE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    FAILED = "FAILED"


#: Arabic labels. ``ENTRY_READY_RESEARCH`` always carries the research
#: qualifier — it must never be presented as a plain buy or entry instruction.
STATE_LABELS_AR: dict[OrbResearchState, str] = {
    OrbResearchState.WAIT_OPENING_RANGE: "انتظار اكتمال النطاق الافتتاحي",
    OrbResearchState.WAIT_BREAKOUT: "انتظار اختراق مؤكد",
    OrbResearchState.BREAKOUT_CANDIDATE: "مرشح اختراق قيد التحقق",
    OrbResearchState.BREAKOUT_REJECTED_WICK_ONLY: "اختراق بالظل فقط — مرفوض",
    OrbResearchState.BREAKOUT_REJECTED_STALE: "بيانات متأخرة — الاختراق مرفوض",
    OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY: "جودة الاختراق غير كافية",
    OrbResearchState.BREAKOUT_TOO_EXTENDED: "الاختراق ممتد — ممنوع المطاردة",
    OrbResearchState.MOMENTUM_QUALIFIED_RESEARCH: "مؤهل للزخم بحثيًا — لا دخول بعد",
    OrbResearchState.WAIT_FIRST_PULLBACK: "انتظار أول إعادة اختبار",
    OrbResearchState.FIRST_PULLBACK_IN_PROGRESS: "أول إعادة اختبار قيد التكوين",
    OrbResearchState.PULLBACK_HEALTHY: "إعادة اختبار سليمة",
    OrbResearchState.PULLBACK_PRICE_ONLY: "إعادة اختبار سليمة بالسعر فقط",
    OrbResearchState.PULLBACK_TOO_DEEP: "إعادة الاختبار عميقة أكثر من المسموح",
    OrbResearchState.PULLBACK_VOLUME_EXPANSION: "تمدد حجم البيع أثناء التصحيح",
    OrbResearchState.PULLBACK_STRUCTURE_FAILED: "فشل هيكل إعادة الاختبار",
    OrbResearchState.WAIT_RECLAIM: "انتظار تأكيد استعادة المستوى",
    OrbResearchState.RECLAIM_CANDIDATE: "مرشح استعادة قيد التأكيد",
    OrbResearchState.RECLAIM_FAILED: "فشل تأكيد الاستعادة",
    OrbResearchState.ENTRY_READY_RESEARCH: "جاهز بحثيًا بعد تأكيد إعادة الاختبار",
    OrbResearchState.ENTRY_EXPIRED: "انتهت صلاحية الفرصة",
    OrbResearchState.LATE_SESSION: "الوقت متأخر لدخول جديد",
    OrbResearchState.AUCTION_PHASE: "مرحلة مزاد الإغلاق — لا دخول جديد",
    OrbResearchState.DATA_UNAVAILABLE: "البيانات غير متاحة",
    OrbResearchState.FAILED: "فشل النموذج",
}

STATE_LABELS_EN: dict[OrbResearchState, str] = {
    OrbResearchState.WAIT_OPENING_RANGE: "Waiting for the opening range",
    OrbResearchState.WAIT_BREAKOUT: "Waiting for a confirmed breakout",
    OrbResearchState.BREAKOUT_CANDIDATE: "Breakout candidate under validation",
    OrbResearchState.BREAKOUT_REJECTED_WICK_ONLY: "Wick-only breakout rejected",
    OrbResearchState.BREAKOUT_REJECTED_STALE: "Breakout rejected on stale data",
    OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY: "Breakout quality insufficient",
    OrbResearchState.BREAKOUT_TOO_EXTENDED: "Breakout too extended — do not chase",
    OrbResearchState.MOMENTUM_QUALIFIED_RESEARCH: "Momentum qualified (research) — no entry yet",
    OrbResearchState.WAIT_FIRST_PULLBACK: "Waiting for the first pullback",
    OrbResearchState.FIRST_PULLBACK_IN_PROGRESS: "First pullback forming",
    OrbResearchState.PULLBACK_HEALTHY: "Pullback healthy",
    OrbResearchState.PULLBACK_PRICE_ONLY: "Pullback healthy on price evidence only",
    OrbResearchState.PULLBACK_TOO_DEEP: "Pullback deeper than permitted",
    OrbResearchState.PULLBACK_VOLUME_EXPANSION: "Selling volume expanded during the pullback",
    OrbResearchState.PULLBACK_STRUCTURE_FAILED: "Pullback structure failed",
    OrbResearchState.WAIT_RECLAIM: "Waiting for reclaim confirmation",
    OrbResearchState.RECLAIM_CANDIDATE: "Reclaim candidate under confirmation",
    OrbResearchState.RECLAIM_FAILED: "Reclaim confirmation failed",
    OrbResearchState.ENTRY_READY_RESEARCH: "Research ready after retest confirmation",
    OrbResearchState.ENTRY_EXPIRED: "Opportunity expired",
    OrbResearchState.LATE_SESSION: "Too late in the session for a new candidate",
    OrbResearchState.AUCTION_PHASE: "Closing auction — no new candidate",
    OrbResearchState.DATA_UNAVAILABLE: "Data unavailable",
    OrbResearchState.FAILED: "Model failed",
}


#: No transition may leave these.
TERMINAL_STATES = frozenset(
    {
        OrbResearchState.BREAKOUT_REJECTED_WICK_ONLY,
        OrbResearchState.BREAKOUT_REJECTED_STALE,
        OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY,
        OrbResearchState.PULLBACK_TOO_DEEP,
        OrbResearchState.PULLBACK_VOLUME_EXPANSION,
        OrbResearchState.PULLBACK_STRUCTURE_FAILED,
        OrbResearchState.RECLAIM_FAILED,
        OrbResearchState.ENTRY_READY_RESEARCH,
        OrbResearchState.ENTRY_EXPIRED,
        OrbResearchState.AUCTION_PHASE,
        OrbResearchState.DATA_UNAVAILABLE,
        OrbResearchState.FAILED,
    }
)

#: States from which no *new* research candidate may be produced, but which are
#: not themselves structural failures.
NON_ENTRY_STATES = frozenset(
    {OrbResearchState.LATE_SESSION, OrbResearchState.AUCTION_PHASE}
)


#: The legal transition graph. Any transition outside it is a programming error
#: and raises, so an illegal sequence can never reach persistence.
LEGAL_TRANSITIONS: dict[OrbResearchState, frozenset[OrbResearchState]] = {
    OrbResearchState.WAIT_OPENING_RANGE: frozenset(
        {
            OrbResearchState.WAIT_BREAKOUT,
            OrbResearchState.DATA_UNAVAILABLE,
            OrbResearchState.FAILED,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.LATE_SESSION,
        }
    ),
    OrbResearchState.WAIT_BREAKOUT: frozenset(
        {
            OrbResearchState.BREAKOUT_CANDIDATE,
            OrbResearchState.BREAKOUT_REJECTED_WICK_ONLY,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.ENTRY_EXPIRED,
            OrbResearchState.DATA_UNAVAILABLE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.BREAKOUT_CANDIDATE: frozenset(
        {
            OrbResearchState.MOMENTUM_QUALIFIED_RESEARCH,
            OrbResearchState.BREAKOUT_TOO_EXTENDED,
            OrbResearchState.BREAKOUT_REJECTED_WICK_ONLY,
            OrbResearchState.BREAKOUT_REJECTED_STALE,
            OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.MOMENTUM_QUALIFIED_RESEARCH: frozenset(
        {
            OrbResearchState.WAIT_FIRST_PULLBACK,
            OrbResearchState.BREAKOUT_TOO_EXTENDED,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.BREAKOUT_TOO_EXTENDED: frozenset(
        {
            OrbResearchState.WAIT_FIRST_PULLBACK,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.ENTRY_EXPIRED,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.WAIT_FIRST_PULLBACK: frozenset(
        {
            OrbResearchState.FIRST_PULLBACK_IN_PROGRESS,
            OrbResearchState.ENTRY_EXPIRED,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.FIRST_PULLBACK_IN_PROGRESS: frozenset(
        {
            OrbResearchState.PULLBACK_HEALTHY,
            OrbResearchState.PULLBACK_PRICE_ONLY,
            OrbResearchState.PULLBACK_TOO_DEEP,
            OrbResearchState.PULLBACK_VOLUME_EXPANSION,
            OrbResearchState.PULLBACK_STRUCTURE_FAILED,
            OrbResearchState.ENTRY_EXPIRED,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.PULLBACK_HEALTHY: frozenset(
        {
            OrbResearchState.WAIT_RECLAIM,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.PULLBACK_PRICE_ONLY: frozenset(
        {
            OrbResearchState.WAIT_RECLAIM,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.WAIT_RECLAIM: frozenset(
        {
            OrbResearchState.RECLAIM_CANDIDATE,
            OrbResearchState.RECLAIM_FAILED,
            OrbResearchState.PULLBACK_STRUCTURE_FAILED,
            OrbResearchState.ENTRY_EXPIRED,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.RECLAIM_CANDIDATE: frozenset(
        {
            OrbResearchState.ENTRY_READY_RESEARCH,
            OrbResearchState.RECLAIM_FAILED,
            OrbResearchState.ENTRY_EXPIRED,
            OrbResearchState.LATE_SESSION,
            OrbResearchState.AUCTION_PHASE,
            OrbResearchState.FAILED,
        }
    ),
    OrbResearchState.LATE_SESSION: frozenset(
        {OrbResearchState.AUCTION_PHASE, OrbResearchState.ENTRY_EXPIRED}
    ),
}


class RuleCode(str, Enum):
    """Why a transition fired. Every transition records exactly one."""

    OPENING_RANGE_READY = "OPENING_RANGE_READY"
    OPENING_RANGE_NOT_READY = "OPENING_RANGE_NOT_READY"
    BREAKOUT_CLOSE_ABOVE_OR_HIGH = "BREAKOUT_CLOSE_ABOVE_OR_HIGH"
    BREAKOUT_QUALITY_ACCEPTED = "BREAKOUT_QUALITY_ACCEPTED"
    BREAKOUT_QUALITY_REJECTED = "BREAKOUT_QUALITY_REJECTED"
    ANTI_CHASE_EXTENSION_EXCEEDED = "ANTI_CHASE_EXTENSION_EXCEEDED"
    ANTI_CHASE_ACCEPTABLE = "ANTI_CHASE_ACCEPTABLE"
    BREAKOUT_FROZEN_AWAIT_PULLBACK = "BREAKOUT_FROZEN_AWAIT_PULLBACK"
    FIRST_PULLBACK_STARTED = "FIRST_PULLBACK_STARTED"
    PULLBACK_QUALITY_ASSESSED = "PULLBACK_QUALITY_ASSESSED"
    PULLBACK_DEPTH_EXCEEDED = "PULLBACK_DEPTH_EXCEEDED"
    PULLBACK_VOLUME_EXPANDED = "PULLBACK_VOLUME_EXPANDED"
    PULLBACK_STRUCTURE_BROKEN = "PULLBACK_STRUCTURE_BROKEN"
    AWAIT_RECLAIM_CONFIRMATION = "AWAIT_RECLAIM_CONFIRMATION"
    RECLAIM_CONFIRMED = "RECLAIM_CONFIRMED"
    RECLAIM_NOT_CONFIRMED = "RECLAIM_NOT_CONFIRMED"
    RESEARCH_READINESS_ACCEPTED = "RESEARCH_READINESS_ACCEPTED"
    RESEARCH_READINESS_REJECTED = "RESEARCH_READINESS_REJECTED"
    ENTRY_WINDOW_EXPIRED = "ENTRY_WINDOW_EXPIRED"
    LATE_SESSION_CUTOFF = "LATE_SESSION_CUTOFF"
    AUCTION_REACHED = "AUCTION_REACHED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class RejectionReason(str, Enum):
    """Explicit, non-collapsible rejection reasons."""

    # Breakout validity
    WICK_ONLY_BREAKOUT = "WICK_ONLY_BREAKOUT"
    CLOSE_NOT_ABOVE_OR_HIGH = "CLOSE_NOT_ABOVE_OR_HIGH"
    INCOMPLETE_CONFIRMATION_BAR = "INCOMPLETE_CONFIRMATION_BAR"
    STALE_LIVE_DATA = "STALE_LIVE_DATA"
    UNRELIABLE_MARKET_TIMESTAMP = "UNRELIABLE_MARKET_TIMESTAMP"
    AUCTION_CONTAMINATION = "AUCTION_CONTAMINATION"
    ARCHIVED_SYMBOL_INELIGIBLE = "ARCHIVED_SYMBOL_INELIGIBLE"
    UNVERIFIED_RUBIX_MAPPING = "UNVERIFIED_RUBIX_MAPPING"
    DAILY_RESISTANCE_TOO_CLOSE = "DAILY_RESISTANCE_TOO_CLOSE"
    POOR_INITIAL_REWARD_RISK = "POOR_INITIAL_REWARD_RISK"
    INSUFFICIENT_PRICE_UPDATES = "INSUFFICIENT_PRICE_UPDATES"
    INVALID_PRICE_BAR = "INVALID_PRICE_BAR"
    BREAKOUT_BEFORE_EARLIEST_TIME = "BREAKOUT_BEFORE_EARLIEST_TIME"
    OPENING_RANGE_NOT_READY = "OPENING_RANGE_NOT_READY"

    # Anti-chase. Each gate has its own reason so the recorded evidence names
    # the measurement that actually fired, never a generic "too extended".
    EXTENSION_ABOVE_OR_HIGH_EXCEEDED = "EXTENSION_ABOVE_OR_HIGH_EXCEEDED"
    EXTENSION_ABOVE_OR_HIGH_ATR_EXCEEDED = "EXTENSION_ABOVE_OR_HIGH_ATR_EXCEEDED"
    BREAKOUT_BAR_RANGE_EXCEEDED = "BREAKOUT_BAR_RANGE_EXCEEDED"
    BREAKOUT_BAR_RANGE_ATR_EXCEEDED = "BREAKOUT_BAR_RANGE_ATR_EXCEEDED"

    # Pullback
    PULLBACK_NOT_STARTED = "PULLBACK_NOT_STARTED"
    PULLBACK_TOO_SHALLOW = "PULLBACK_TOO_SHALLOW"
    PULLBACK_DEPTH_PERCENT_EXCEEDED = "PULLBACK_DEPTH_PERCENT_EXCEEDED"
    PULLBACK_DEPTH_ATR_EXCEEDED = "PULLBACK_DEPTH_ATR_EXCEEDED"
    PULLBACK_DURATION_EXCEEDED = "PULLBACK_DURATION_EXCEEDED"
    EXCESSIVE_CLOSES_BELOW_OR_HIGH = "EXCESSIVE_CLOSES_BELOW_OR_HIGH"
    STRUCTURAL_LOWER_LOW_FAILURE = "STRUCTURAL_LOWER_LOW_FAILURE"
    SELLING_VOLUME_EXPANSION = "SELLING_VOLUME_EXPANSION"
    VOLUME_UNAVAILABLE_AND_REQUIRED = "VOLUME_UNAVAILABLE_AND_REQUIRED"
    SECOND_PULLBACK_NOT_ELIGIBLE = "SECOND_PULLBACK_NOT_ELIGIBLE"

    # Reclaim
    RECLAIM_TRIGGER_NOT_REACHED = "RECLAIM_TRIGGER_NOT_REACHED"
    RECLAIM_CONFIRMATION_EXPIRED = "RECLAIM_CONFIRMATION_EXPIRED"
    RECLAIM_AFTER_STRUCTURE_FAILURE = "RECLAIM_AFTER_STRUCTURE_FAILURE"

    # Risk / targets
    STOP_REFERENCE_MISSING = "STOP_REFERENCE_MISSING"
    STOP_NOT_BELOW_TRIGGER = "STOP_NOT_BELOW_TRIGGER"
    STOP_DISTANCE_EXCEEDED = "STOP_DISTANCE_EXCEEDED"
    REWARD_RISK_BELOW_MINIMUM = "REWARD_RISK_BELOW_MINIMUM"
    RISK_NOT_CALCULABLE = "RISK_NOT_CALCULABLE"

    # Time / capability
    LATE_SESSION_CUTOFF = "LATE_SESSION_CUTOFF"
    AUCTION_PHASE_REACHED = "AUCTION_PHASE_REACHED"
    ENTRY_WINDOW_EXPIRED = "ENTRY_WINDOW_EXPIRED"
    LIVE_DECISION_DISABLED_FRESHNESS = "LIVE_DECISION_DISABLED_FRESHNESS"


REJECTION_LABELS_AR: dict[RejectionReason, str] = {
    RejectionReason.WICK_ONLY_BREAKOUT: "اختراق بالظل فقط",
    RejectionReason.CLOSE_NOT_ABOVE_OR_HIGH: "الإغلاق ليس فوق قمة النطاق",
    RejectionReason.INCOMPLETE_CONFIRMATION_BAR: "شمعة التأكيد غير مكتملة",
    RejectionReason.STALE_LIVE_DATA: "بيانات حية متأخرة",
    RejectionReason.UNRELIABLE_MARKET_TIMESTAMP: "توقيت السوق غير موثوق",
    RejectionReason.AUCTION_CONTAMINATION: "تلوث بيانات المزاد",
    RejectionReason.ARCHIVED_SYMBOL_INELIGIBLE: "رمز مؤرشف غير مؤهل",
    RejectionReason.UNVERIFIED_RUBIX_MAPPING: "ربط Rubix غير مُتحقق",
    RejectionReason.DAILY_RESISTANCE_TOO_CLOSE: "مقاومة يومية قريبة جدًا",
    RejectionReason.POOR_INITIAL_REWARD_RISK: "العائد إلى المخاطرة ضعيف",
    RejectionReason.INSUFFICIENT_PRICE_UPDATES: "تحديثات السعر غير كافية",
    RejectionReason.INVALID_PRICE_BAR: "شمعة سعرية غير صالحة",
    RejectionReason.BREAKOUT_BEFORE_EARLIEST_TIME: "الاختراق قبل الوقت المسموح",
    RejectionReason.OPENING_RANGE_NOT_READY: "النطاق الافتتاحي غير مكتمل",
    RejectionReason.EXTENSION_ABOVE_OR_HIGH_EXCEEDED: "الامتداد فوق القمة تجاوز الحد",
    RejectionReason.EXTENSION_ABOVE_OR_HIGH_ATR_EXCEEDED: "الامتداد فوق القمة تجاوز حد ATR",
    RejectionReason.BREAKOUT_BAR_RANGE_EXCEEDED: "مدى شمعة الاختراق تجاوز الحد",
    RejectionReason.BREAKOUT_BAR_RANGE_ATR_EXCEEDED: "مدى الشمعة تجاوز حد ATR",
    RejectionReason.PULLBACK_NOT_STARTED: "لم تبدأ إعادة الاختبار",
    RejectionReason.PULLBACK_TOO_SHALLOW: "إعادة الاختبار ضحلة جدًا",
    RejectionReason.PULLBACK_DEPTH_PERCENT_EXCEEDED: "عمق التصحيح تجاوز النسبة",
    RejectionReason.PULLBACK_DEPTH_ATR_EXCEEDED: "عمق التصحيح تجاوز حد ATR",
    RejectionReason.PULLBACK_DURATION_EXCEEDED: "مدة التصحيح تجاوزت الحد",
    RejectionReason.EXCESSIVE_CLOSES_BELOW_OR_HIGH: "إغلاقات متكررة تحت القمة",
    RejectionReason.STRUCTURAL_LOWER_LOW_FAILURE: "فشل هيكلي بقاع أدنى",
    RejectionReason.SELLING_VOLUME_EXPANSION: "تمدد حجم البيع",
    RejectionReason.VOLUME_UNAVAILABLE_AND_REQUIRED: "الحجم غير متاح وهو مطلوب",
    RejectionReason.SECOND_PULLBACK_NOT_ELIGIBLE: "إعادة اختبار ثانية غير مؤهلة",
    RejectionReason.RECLAIM_TRIGGER_NOT_REACHED: "لم يتحقق مستوى الاستعادة",
    RejectionReason.RECLAIM_CONFIRMATION_EXPIRED: "انتهت مهلة تأكيد الاستعادة",
    RejectionReason.RECLAIM_AFTER_STRUCTURE_FAILURE: "استعادة بعد فشل هيكلي",
    RejectionReason.STOP_REFERENCE_MISSING: "مرجع وقف الخسارة مفقود",
    RejectionReason.STOP_NOT_BELOW_TRIGGER: "الوقف ليس أسفل نقطة التفعيل",
    RejectionReason.STOP_DISTANCE_EXCEEDED: "مسافة الوقف تجاوزت الحد",
    RejectionReason.REWARD_RISK_BELOW_MINIMUM: "العائد إلى المخاطرة أقل من الحد",
    RejectionReason.RISK_NOT_CALCULABLE: "تعذر حساب المخاطرة",
    RejectionReason.LATE_SESSION_CUTOFF: "تجاوز وقت الدخول",
    RejectionReason.AUCTION_PHASE_REACHED: "بدأ مزاد الإغلاق",
    RejectionReason.ENTRY_WINDOW_EXPIRED: "انتهت نافذة الدخول",
    RejectionReason.LIVE_DECISION_DISABLED_FRESHNESS: "القرار الحي معطل لضعف الحداثة",
}


def state_label(state: OrbResearchState, language: str = "AR") -> str:
    """Localized label. Never returns a raw enum name."""

    table = STATE_LABELS_EN if str(language).upper() == "EN" else STATE_LABELS_AR
    return table[state]


def rejection_label(reason: RejectionReason, language: str = "AR") -> str:
    if str(language).upper() == "EN":
        return reason.value.replace("_", " ").title()
    return REJECTION_LABELS_AR[reason]


def assert_legal_transition(
    prior: OrbResearchState, new: OrbResearchState
) -> None:
    """Fail loudly on an illegal transition rather than persisting it."""

    if prior in TERMINAL_STATES:
        raise ValueError(f"{prior.value} is terminal; cannot move to {new.value}")
    allowed = LEGAL_TRANSITIONS.get(prior)
    if allowed is None or new not in allowed:
        raise ValueError(f"illegal ORB transition {prior.value} -> {new.value}")


__all__ = [
    "LEGAL_TRANSITIONS",
    "NON_ENTRY_STATES",
    "OrbResearchState",
    "REJECTION_LABELS_AR",
    "RejectionReason",
    "RuleCode",
    "STATE_LABELS_AR",
    "STATE_LABELS_EN",
    "TERMINAL_STATES",
    "assert_legal_transition",
    "rejection_label",
    "state_label",
]
