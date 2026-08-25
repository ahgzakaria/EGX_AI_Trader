"""Read-only Streamlit views for operations and deterministic replay."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from core.data_provider import provider_health
from dashboard.ui import page_header, section_header
from services.experiment_tracking import RunRepository
from services.run_replay import replay_run
from services.system_health import collect_system_health


def _lossless_replay_ready(run):
    directory = Path("reports") / str(run.get("run_id"))
    try:
        manifest = json.loads((directory / "dataset" / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    try:
        notes = json.loads((directory / "notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        notes = {}
    return manifest.get("format") == "npz" and notes.get("metric_match") is not False


def show_system_health():
    page_header(
        "صحة النظام",
        "حالة مصادر البيانات والتشخيصات الفنية",
        icon="🩺",
        badge="SYSTEM HEALTH",
    )
    health = collect_system_health(streamlit_running=True)
    state = health["state"]
    if state == "HEALTHY":
        st.success("الخدمات المطلوبة متاحة للبحث.")
    elif state in {"DEGRADED", "RESEARCH_ONLY"}:
        st.warning(f"{health['safety']['message']} ({state})")
    else:
        st.error(f"متطلبات التشغيل غير مكتملة. ({state})")
    provider, database, disk, replay = st.columns(4)
    provider.metric("مصدر البيانات اليومية", health["provider"].get("actual", "Unknown").upper())
    database.metric("قاعدة Rubix", health["rubix_database"]["status"])
    disk.metric("المساحة المتاحة", f"{health['disk']['free_percent']:.1f}%")
    replay.metric("جلسات البحث القابلة للإعادة", health["replay_readiness"]["ready_runs"])
    if st.button("تحديث حالة النظام", use_container_width=True):
        st.rerun()

    section_header("التشخيصات الفنية", "تفاصيل للمراجعة عند وجود مشكلة")
    st.caption(
        "توجد هنا تفاصيل التغطية، معرّفات الفحص، نتائج الحالات، فقدان الذاكرة "
        "المؤقتة، المهام المجدولة، اللقطات غير الصالحة، الجسر، إخفاقات المصدر، "
        "بصمات قواعد البيانات، والعمليات."
    )
    with st.expander("تفاصيل النظام الكاملة (Advanced Diagnostics)"):
        st.json(health, expanded=False)
    with st.expander("تشخيصات مصادر البيانات (Provider Diagnostics)"):
        _provider_domains_panel()
        st.json(provider_health("dashboard"), expanded=False)

    _legacy_research_tools()


def _legacy_research_tools():
    """Expose removed pages only from System Health, never primary navigation."""

    section_header("أدوات البحث القديمة", "Legacy / Research Only")
    st.warning(
        "هذه الأدوات مخصصة للبحث والتشخيص فقط، وليست جزءاً من مسار التداول اليومي."
    )
    tools = {
        # Retired from navigation 2026-08-25 on measurement, not preference:
        # the round trip is 1,030% of the average intraday move on EGX, and
        # across 48 live signals only 46% ever saw a price covering their own
        # cost even with a perfect exit. Kept reachable because the engine,
        # the shadow sessions and the evidence all remain.
        "إشارات ORB (متقاعدة)": (
            "dashboard.orb_signals",
            "show_orb_signals",
        ),
        "الفرص القديمة (Opportunities)": (
            "dashboard.opportunities",
            "show_opportunities",
        ),
        "النطاق المتوقع القديم (Expected Range Scalper)": (
            "dashboard.expected_range_scalper",
            "show_expected_range_scalper",
        ),
        "سجل التجارب (Run History)": (
            "dashboard.run_history",
            "show_run_history",
        ),
        "مقارنة التجارب (Compare Runs)": (
            "dashboard.compare_runs",
            "show_compare_runs",
        ),
        "إعادة التشغيل (Replay)": (__name__, "show_replay_run"),
        "تحليلات الأداء (Performance Analytics)": (
            "dashboard.decision_support",
            "show_decision_analytics",
        ),
        "الاختبار الأمامي (Forward Testing)": (
            "dashboard.forward_testing",
            "show_forward_testing",
        ),
        "تقويم التداول (Trading Calendar)": (
            "dashboard.trading_calendar",
            "show_trading_calendar",
        ),
        "مراجعة EODHD (Migration Review)": (
            "dashboard.eodhd_migration_review",
            "show_eodhd_migration_review",
        ),
        "إعدادات السكالبنج القديمة (Scalping Settings)": (
            "dashboard.scalping",
            "show_scalping_settings",
        ),
        "إعادة بناء القائمة التاريخية (Research Rebuild)": (
            "dashboard.scalping",
            "_historical_watchlist_panel",
        ),
    }
    with st.expander("Legacy Research Tools · أدوات البحث القديمة"):
        selected = st.selectbox(
            "اختر أداة بحثية",
            tuple(tools),
            key="legacy_research_tool",
        )
        st.caption("Legacy / Research Only")
        if st.button(
            "فتح الأداة البحثية",
            key="open_legacy_research_tool",
        ):
            st.session_state["_open_legacy_research_tool"] = selected
        active = st.session_state.get("_open_legacy_research_tool")
        if active in tools:
            module_name, function_name = tools[active]
            if module_name == __name__:
                renderer = globals()[function_name]
            else:
                from importlib import import_module

                renderer = getattr(import_module(module_name), function_name)
            st.divider()
            renderer()


def show_provider_diagnostics():
    """Keep verbose provider evidence out of the trading dashboard."""

    page_header(
        "تشخيصات مصادر البيانات",
        "أدلة تشغيلية للقراءة فقط",
        icon="🗄️",
        badge="LEGACY / RESEARCH ONLY",
    )
    st.caption(
        "Read-only operational evidence. These values never enter strategy "
        "or indicator calculations."
    )
    _provider_domains_panel()
    st.divider()
    st.json(provider_health("dashboard"), expanded=False)
    if st.button("Refresh provider status", use_container_width=True):
        st.rerun()


def _provider_domains_panel():
    """CURRENT RESEARCH / LIVE / UNSUPPORTED / LEGACY — explicit data domains."""
    import json as _json
    from pathlib import Path as _Path

    def _load(p):
        try:
            return _json.loads(_Path(p).read_text(encoding="utf-8"))
        except Exception:
            return {}

    summary = _load("reports/eodhd/current_research_migration_summary.json")

    st.subheader("CURRENT RESEARCH · CURRENT_RESEARCH_V2")
    c = st.columns(5)
    c[0].metric("Current Research Provider", "EODHD")
    c[1].metric("Activated symbols", summary.get("activated_current_research", "—"))
    try:
        from core.egx_calendar import effective_holidays
        from core.egx_session import expected_latest_completed_session
        expected = expected_latest_completed_session(holidays=effective_holidays())
    except Exception:
        expected = None
    c[2].metric("Expected EGX session", str(expected or "—"))
    latest_eodhd = "—"
    try:
        from core.research_router import get_current_research_history
        f = get_current_research_history("COMI", min_bars=1)
        latest_eodhd = f.attrs["market_data"].get("latest_completed_session", "—")
    except Exception:
        pass
    c[3].metric("Latest EODHD session", latest_eodhd)
    fresh = "FRESH" if (expected and str(latest_eodhd) >= str(expected)) else "CHECK"
    c[4].metric("Freshness", fresh)
    st.caption("Current research never uses Yahoo; Yahoo's date is not consulted for freshness.")

    st.subheader("LIVE · Rubix")
    l = st.columns(3)
    try:
        from dashboard.scalping import _rubix_latest_event, _rubix_path
        l[0].metric("Live provider", "Rubix")
        l[1].metric("Collector DB", "present" if _rubix_path().is_file() else "missing")
        l[2].metric("Last event", str(_rubix_latest_event() or "—")[:19])
    except Exception:
        l[0].metric("Live provider", "Rubix")

    st.subheader("UNSUPPORTED SYMBOLS · Local History + Rubix Daily Bridge")
    u = st.columns(3)
    u[0].metric("Evaluated", summary.get("unsupported_evaluated", "—"))
    u[1].metric("Ready", summary.get("unsupported_ready", "—"))
    blocked = summary.get("blocked", "—")
    u[2].metric("Blocked (all tiers)", blocked)
    st.caption("EODHD-unsupported symbols use validated local history plus completed "
               "Rubix Daily Bridge sessions — never a Yahoo update.")

    st.subheader("LEGACY · Frozen Yahoo Snapshot")
    g = st.columns(3)
    g[0].metric("Provider", "FROZEN_YAHOO_SNAPSHOT")
    g[1].metric("Used for", "Backtest reproduction only")
    g[2].metric("Live network calls", "none")
    st.caption("Legacy snapshots are immutable, are NOT used for current research, and do "
               "NOT affect current freshness.")


def show_replay_run():
    page_header("Replay Run", "Re-runs a recorded session against the current engine. Reads only; writes nothing back.", icon="↻")
    runs = [
        run for run in RunRepository.list_runs()
        if run.get("status") == "COMPLETED" and run.get("replay_ready")
        and _lossless_replay_ready(run)
        and (
            run.get("run_type") in {"BACKTEST", "REPLAY"}
            or run.get("baseline_name") == "PHASE5_CURRENT_DATA_V2"
        )
    ]
    if not runs:
        st.info("No completed backtest has a Phase 8 dataset archive yet.")
        return
    selected = st.selectbox("Archived Run", [run["run_id"] for run in runs])
    metadata = RunRepository.get(selected)
    directory = Path("reports") / selected
    manifest = json.loads(
        (directory / "dataset" / "MANIFEST.json").read_text(encoding="utf-8")
    )
    st.caption(
        f"Dataset hash: {metadata.get('dataset_hash')} · Mode: {metadata.get('mode')}"
    )
    left, middle, right = st.columns(3)
    left.metric("Dataset", str(metadata.get("dataset_hash", ""))[:12])
    middle.metric("Settings", str(manifest.get("settings_sha256", ""))[:12])
    right.metric("Code", str(metadata.get("git_hash") or "unavailable")[:12])
    with st.expander("Original metrics", expanded=True):
        st.json(metadata.get("metrics", {}))
    st.warning("Replay is offline. Missing candles or predictions cause a hard failure; Yahoo/Rubix fallback is disabled.")
    if st.button("Replay archived run", type="primary", use_container_width=True):
        try:
            with st.spinner("Replaying exact archived inputs..."):
                result = replay_run(selected)
            st.success(f"Exact replay completed: {result['replay_run_id']}")
            st.json(result)
        except Exception as error:
            st.error(f"Replay failed: {error}")
