"""محفظتي — the positions actually held, and how to leave each one.

Every other page in this project answers "what should I buy?". This one
answers the question that decides whether any of that mattered: what to do with
what is already owned.

Three things are on screen before anything else, in this order:

1. **What to do now**, sorted by urgency, with the reason and what it is worth
   after costs. Not a table of symbols to scan -- if nothing needs doing, the
   panel says so in one line.
2. **Breakeven beside the average price.** These are different numbers and the
   difference is a real loss. A broker screen shows only the first.
3. **Where the price came from.** A live Rubix quote and a close from a session
   that ended yesterday are both prices; acting on the second as if it were the
   first is how a plan gets executed against a market that has moved.

The page computes nothing itself. Levels come from ``holdings.plan``,
recommendations from ``holdings.rules``, and arithmetic from ``holdings.book``.
Read-only with respect to the market: no order is placed anywhere, ever.
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import date

import pandas as pd
import streamlit as st

from holdings import imports, invoices

from core.level_status import BASIS_COMPLETED_CLOSE, BASIS_LIVE
from core.symbols import ApprovedSymbol, load_approved_symbol_options, resolve_approved_symbol
from dashboard.formatting import NAME_COLUMN, company_name, with_company_name_column
from dashboard.ui import (
    ACT,
    NO_PLAN,
    UNVALIDATED,
    WITHHELD_KIND,
    action_card_html,
    badge_html,
    empty_state,
    metric_card,
    page_header,
    section_header,
)
from holdings.assistant import (
    build_portfolio_view,
    default_history_loader,
    default_quote_overlays,
    default_sector_intraday,
    default_sector_map,
    default_sector_strengths,
    expected_completed_session,
    log_recommendations,
)
from holdings.book import BookError, load_fee_model
from holdings.rules import NOW, SOON, TODAY, WITHHELD
from holdings.store import HoldingsStore, StoreError


#: Tone per action, used for every badge on the page.
ACTION_TONE = {
    "EXIT": "red",
    "TRIM": "amber",
    "RAISE_STOP": "amber",
    "WATCH": "blue",
    "HOLD": "green",
    "WITHHELD": "gray",
}

ACTION_ARABIC = {
    "EXIT": "بيع كلي",
    "TRIM": "جني جزئي",
    "RAISE_STOP": "ترفيع الوقف",
    "WATCH": "مراقبة",
    "HOLD": "استمرار",
    "WITHHELD": "محجوب",
}

URGENCY_ARABIC = {NOW: "الآن", TODAY: "اليوم", SOON: "قريبًا"}

#: Transaction sides, which are a different vocabulary from recommendations:
#: a recorded SELL is something that happened, an EXIT is something proposed.
SIDE_ARABIC = {"BUY": "شراء", "SELL": "بيع",
               "DEPOSIT": "إيداع", "WITHDRAW": "سحب"}

BASIS_ARABIC = {
    BASIS_LIVE: "سعر حي",
    BASIS_COMPLETED_CLOSE: "إغلاق آخر جلسة",
    "NO_PRICE": "لا يوجد سعر",
}


# --------------------------------------------------------------------------- #
# Cached context
# --------------------------------------------------------------------------- #

@st.cache_data(ttl=300, show_spinner=False)
def _sector_map():
    return default_sector_map()


@st.cache_data(ttl=300, show_spinner=False)
def _sector_strengths():
    return default_sector_strengths()


@st.cache_data(ttl=120, show_spinner=False)
def _sector_intraday():
    """Empty since the Rubix feed was retired; see ``default_sector_intraday``."""

    return default_sector_intraday()


@st.cache_data(ttl=3600, show_spinner=False)
def _history(symbol, session):
    """Completed daily bars for one symbol, keyed by the session they end on.

    Two things keep this from being loaded on every visit. The cache key
    carries the completed session, so a new one invalidates every entry at
    once and nothing else does; and the assistant does not ask for daily data
    at all when the stored plan already belongs to that session.
    """

    return default_history_loader(symbol)


@st.cache_data(ttl=45, show_spinner=False)
def _quotes(symbols):
    """Live quotes for the held symbols: none since the Rubix feed was retired."""

    return default_quote_overlays(symbols)


def _store():
    return HoldingsStore()


def _money(value, digits=2):
    return "—" if value is None else f"{float(value):,.{digits}f}"


def _price(value):
    return "—" if value is None else f"{float(value):,.3f}"


def _percent(value, digits=2):
    return "—" if value is None else f"{float(value):+.{digits}f}%"


# --------------------------------------------------------------------------- #
# Panels
# --------------------------------------------------------------------------- #

def _summary(view):
    section_header("الملخص", "What the account is worth, after the cost of leaving it")
    # Whole pounds in the summary: five cards on one row wrap a decimal onto a
    # second line, and piastres carry no information at portfolio scale. The
    # positions table below keeps three decimals, where they decide things.
    # Risk to the stop is promoted to a tile of its own. It was a caption under
    # the row, in the same grey as the note about unpriced positions -- and it
    # is the only number here that makes two positions of different sizes and
    # different stop distances comparable, which is what the reader is doing.
    # Market value, unrealised profit and money-still-at-risk are three
    # different quantities and they read identically until one of them is
    # weighted differently.
    risk = sum(value for value in (_risk_egp(item) for item in view.positions)
               if value is not None)
    equity = view.total_equity_egp
    limit = float(getattr(view.policy, "max_portfolio_risk_percent", 0) or 0)
    risk_percent = (risk / equity * 100.0) if equity > 0 else None

    columns = st.columns(5)
    with columns[0]:
        exposure = view.exposure_percent
        metric_card("القيمة السوقية", _money(view.market_value_egp, 0), "Market Value",
                    sub=("" if exposure is None
                         else f"نسبة التعرض للأسهم: {exposure:.1f}%"))
    with columns[1]:
        net = view.unrealized_net_egp
        metric_card("ربح غير محقق (صافي)", _money(net, 0), "Unrealized, after costs",
                    tone="green" if net >= 0 else "red",
                    sub="بعد العمولة والدمغة وتكلفة الخروج")
    with columns[2]:
        # Against the same limit the backtest is run under, so a reader
        # comparing the two is comparing like with like.
        metric_card(
            "المخاطرة حتى الوقف", _money(risk, 0), "Capital at Risk",
            tone="amber" if (risk_percent is not None and limit
                             and risk_percent > limit) else None,
            sub=("—" if risk_percent is None else
                 f"{risk_percent:.1f}% من رأس المال"
                 + (f" · الحد {limit:.0f}%" if limit else "")),
        )
    with columns[3]:
        realized = view.book.realized_pnl_egp
        metric_card("ربح محقق", _money(realized, 0), "Realized",
                    tone="green" if realized >= 0 else "red")
    with columns[4]:
        cash = view.book.cash_egp
        metric_card("الكاش", _money(cash, 0), "Cash",
                    tone="red" if cash < 0 else None,
                    sub="مكشوف" if cash < 0 else "")

    if view.unpriced_count:
        st.caption(
            f"⚠️ {view.unpriced_count} مركز بدون سعر حالي — محسوب بسعر التكلفة، "
            "وليس بسعر السوق."
        )
    for warning in view.warnings:
        st.warning(warning)


def _actions(view):
    """What to do now. First panel on the page, because it is the reason for it."""

    section_header("ماذا أفعل الآن", "Ordered by urgency, priced after costs")
    actionable = view.actionable()
    withheld = [
        item for item in view.positions
        if item.recommendation is not None and item.recommendation.action == WITHHELD
    ]

    # A position with no plan is not a bug to hide: it is a holding this page
    # cannot advise on, and the reason belongs next to it.
    planless = [item for item in view.positions
                if item.plan is not None and not item.plan.available]

    if not (actionable or withheld or planless):
        st.success("لا يوجد إجراء مطلوب الآن — كل المراكز ضمن خططها.")

    # One component, four kinds, in one stack. They used to be a card, a joined
    # sentence in an info banner, and a warning each -- three shapes for three
    # facts that a reader has to weigh against each other.
    for item in actionable:
        _action_card(item)
    for item in withheld:
        _withheld_card(item)
    for item in planless:
        _no_plan_card(item)


def _name(symbol):
    """The company name beside the ticker, or nothing if it is not known."""
    try:
        name = company_name(symbol)
    except Exception:                       # a missing name must not lose the card
        return ""
    return "" if not name or name == symbol else name


def _action_card(item):
    recommendation = item.recommendation
    tone = ACTION_TONE.get(recommendation.action, "gray")
    badges = [
        badge_html(ACTION_ARABIC.get(recommendation.action, recommendation.action), tone),
        badge_html(URGENCY_ARABIC.get(recommendation.urgency, recommendation.urgency), tone),
        badge_html(BASIS_ARABIC.get(recommendation.price_basis, recommendation.price_basis),
                   "green" if recommendation.price_basis == BASIS_LIVE else "gray"),
    ]
    # Stated on the card itself, not buried in documentation: this rule's exit
    # evidence has not been validated on this market yet. It also changes what
    # kind of card this is, so the whole card reads as provisional rather than
    # one badge in a row of four carrying the entire caveat.
    kind = ACT
    if not recommendation.measured:
        kind = UNVALIDATED
        badges.append(badge_html("قاعدة غير مُقاسة بعد", "unknown",
                                 title="Unmeasured rule; logged for evaluation"))

    net = recommendation.net_egp
    st.markdown(
        action_card_html(
            item.symbol, kind=kind, tone=tone, name=_name(item.symbol), badges=badges,
            why=html.escape(str(recommendation.reason_ar or "")),
            figures=(
                ("السعر", _price(recommendation.price), None),
                ("الكمية", _money(recommendation.quantity, 0), None),
                ("الصافي بعد التكاليف",
                 f"{_money(net)} ({_percent(recommendation.net_percent)})",
                 None if net is None else ("pos" if net >= 0 else "neg")),
            ),
        ),
        unsafe_allow_html=True,
    )


def _withheld_card(item):
    """No price this page trusts, so it issues no advice at all.

    Separate from "no plan": the plan exists and the data is stale, which is a
    thing the evening import fixes. These were joined into one sentence naming
    every withheld ticker, which said what was blocked but not why per name.
    """
    st.markdown(
        action_card_html(
            item.symbol, kind=WITHHELD_KIND, name=_name(item.symbol),
            badges=[badge_html("توصية محجوبة", "amber"),
                    badge_html(BASIS_ARABIC.get(item.price.basis, item.price.basis), "amber")],
            why="لا توصية تُبنى على سعر قديم. المركز محسوب في الأرقام، "
                "والتوصية محجوبة حتى يصل سعر موثوق.",
            figures=(("آخر سعر معروف", _price(item.price.value), None),),
        ),
        unsafe_allow_html=True,
    )


def _no_plan_card(item):
    """Nothing was ever measured for this holding -- not stale, absent."""
    reason = item.error or {
        "NO_HISTORY": "لا توجد شموع يومية كافية لهذا السهم",
        "NO_ATR": "لا يمكن حساب مدى التذبذب (ATR) لهذا السهم",
    }.get(item.plan.reason, item.plan.reason or "سبب غير معروف")
    st.markdown(
        action_card_html(
            item.symbol, kind=NO_PLAN, name=_name(item.symbol),
            badges=[badge_html("بدون خطة خروج", "unknown",
                               title="No exit plan could be built for this holding")],
            why=html.escape(str(reason))
                + ". المركز محسوب في الأرقام، لكن بدون وقف ولا أهداف.",
            figures=(("الكمية", _money(item.position.quantity, 0), None),
                     ("متوسط الشراء", _price(item.position.average_price), None)),
        ),
        unsafe_allow_html=True,
    )


def _risk_egp(item):
    """What is still on the table between here and the stop, in pounds.

    Not the whole position and not the unrealized profit: the amount that would
    actually be given back if the stop were hit from the current price. It is
    the only number that makes two positions of different sizes and different
    stop distances comparable.
    """

    plan, price = item.plan, item.price.value
    if plan is None or plan.stop is None or price is None:
        return None
    return max(0.0, (float(price) - float(plan.stop)) * item.position.quantity)


def _positions_table(view):
    section_header("المراكز", "Average, breakeven, and the plan for each")
    rows = []
    for item in view.positions:
        position, plan, value = item.position, item.plan, item.value
        recommendation = item.recommendation
        rows.append({
            "Ticker": item.symbol,
            "الكمية": position.quantity,
            "متوسط الشراء": position.average_price,
            "سعر التعادل": value.breakeven_price if value else None,
            "السعر الحالي": item.price.value,
            "المصدر": BASIS_ARABIC.get(item.price.basis, item.price.basis),
            "الربح الصافي": value.unrealized_net_egp if value else None,
            "%": value.unrealized_net_percent if value else None,
            "الوقف": plan.stop if plan else None,
            "المخاطرة للوقف": _risk_egp(item),
            "هدف جزئي": plan.target_partial if plan else None,
            "هدف نهائي": plan.target_final if plan else None,
            "التوصية": ACTION_ARABIC.get(
                recommendation.action if recommendation else "", "—"),
            "القطاع": item.sector or "—",
            "جلسات": item.holding_sessions,
        })

    frame = pd.DataFrame(rows)
    st.dataframe(
        with_company_name_column(frame, "Ticker"),
        hide_index=True,
        use_container_width=True,
        column_config={
            NAME_COLUMN: st.column_config.TextColumn("اسم السهم", width="medium"),
            "الكمية": st.column_config.NumberColumn(format="%.0f"),
            "متوسط الشراء": st.column_config.NumberColumn(format="%.3f"),
            "سعر التعادل": st.column_config.NumberColumn(format="%.3f"),
            "السعر الحالي": st.column_config.NumberColumn(format="%.3f"),
            "الربح الصافي": st.column_config.NumberColumn(format="%.2f"),
            "%": st.column_config.NumberColumn(format="%.2f%%"),
            "الوقف": st.column_config.NumberColumn(format="%.3f"),
            "المخاطرة للوقف": st.column_config.NumberColumn(format="%.0f"),
            "هدف جزئي": st.column_config.NumberColumn(format="%.3f"),
            "هدف نهائي": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    st.caption(
        "سعر التعادل يشمل عمولة الدخول والخروج ورسم الأمر — البيع تحته خسارة "
        "حتى لو كان السعر أعلى من متوسط الشراء. الأهداف من نفس محرك الإشارات "
        "اليومية (المقاومة، ثم المقاومة + 2×ATR) والوقف من الدعم − 0.3×ATR."
    )


def _concentration(view):
    if not view.sector_exposure:
        return
    section_header("التركيز القطاعي", "Four names in one sector is one big position")
    total = sum(view.sector_exposure.values()) or 1.0
    frame = pd.DataFrame(
        [
            {"القطاع": sector, "القيمة": amount, "النسبة": amount / total * 100.0}
            for sector, amount in sorted(
                view.sector_exposure.items(), key=lambda item: -item[1])
        ]
    )
    st.dataframe(
        frame, hide_index=True, use_container_width=True,
        column_config={
            "القيمة": st.column_config.NumberColumn(format="%.0f"),
            "النسبة": st.column_config.ProgressColumn(
                "النسبة", min_value=0, max_value=100, format="%.1f%%"),
        },
    )


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #

def _symbol_picker(key, label="السهم · Symbol"):
    options = load_approved_symbol_options()
    picked = st.selectbox(
        label, options, index=None,
        format_func=lambda option: (
            option.display_label if isinstance(option, ApprovedSymbol) else str(option)),
        placeholder="اكتب رمز السهم أو اسم الشركة",
        key=key, accept_new_options=True, filter_mode="fuzzy",
    )
    selected = resolve_approved_symbol(picked, options)
    if picked not in (None, "") and selected is None:
        st.warning("لم يتم العثور على سهم مطابق")
    return selected.ticker if selected else None


def _trade_form(store, fee_model):
    with st.form("holdings_trade", clear_on_submit=True):
        symbol = _symbol_picker("holdings_trade_symbol")
        columns = st.columns(4)
        side = columns[0].selectbox("العملية", ["BUY", "SELL"],
                                    format_func=lambda v: "شراء" if v == "BUY" else "بيع")
        quantity = columns[1].number_input("الكمية", min_value=0.0, step=1.0, value=0.0)
        price = columns[2].number_input("السعر المنفذ", min_value=0.0, step=0.01,
                                        value=0.0, format="%.3f")
        trade_date = columns[3].date_input("التاريخ", value=date.today())
        fees = st.number_input(
            "العمولة الفعلية من إشعار التنفيذ (اختياري — اتركها صفرًا لتُحسب تلقائيًا)",
            min_value=0.0, step=0.5, value=0.0,
        )
        note = st.text_input("ملاحظة", value="")
        submitted = st.form_submit_button("تسجيل الصفقة", type="primary")

    if not submitted:
        return
    if not symbol or quantity <= 0 or price <= 0:
        st.error("لازم تختار سهمًا وتدخل كمية وسعرًا أكبر من صفر.")
        return
    try:
        store.record_trade(
            symbol, trade_date, side, quantity, price,
            fees_egp=fees if fees > 0 else None, note=note, fee_model=fee_model,
        )
    except StoreError as error:
        st.error(f"الصفقة مرفوضة: {error}")
        return
    st.success(f"تم تسجيل {SIDE_ARABIC.get(side, side)} {quantity:g} من {symbol}.")
    st.rerun()


def _cash_form(store):
    with st.form("holdings_cash", clear_on_submit=True):
        columns = st.columns(3)
        kind = columns[0].selectbox(
            "الحركة", ["DEPOSIT", "WITHDRAW"],
            format_func=lambda v: "إيداع" if v == "DEPOSIT" else "سحب")
        amount = columns[1].number_input("المبلغ", min_value=0.0, step=100.0, value=0.0)
        movement_date = columns[2].date_input("التاريخ", value=date.today(),
                                              key="holdings_cash_date")
        note = st.text_input("ملاحظة", value="", key="holdings_cash_note")
        submitted = st.form_submit_button("تسجيل")

    if submitted:
        if amount <= 0:
            st.error("المبلغ لازم يكون أكبر من صفر.")
            return
        store.record_cash(movement_date, kind, amount, note)
        st.rerun()


def _corporate_action_form(store, fee_model):
    st.caption(
        "السهم المجاني والتجزئة بيغيّروا عدد الأسهم من غير ما تدفع جنيهًا — "
        "ومن غير تسجيلهم هنا، متوسط الشراء بتاعك بيبقى غلط وأنت فاكر نفسك خسران."
    )
    with st.form("holdings_action", clear_on_submit=True):
        symbol = _symbol_picker("holdings_action_symbol")
        columns = st.columns(3)
        kind = columns[0].selectbox(
            "النوع", ["STOCK_DIVIDEND", "SPLIT", "CASH_DIVIDEND"],
            format_func=lambda v: {
                "STOCK_DIVIDEND": "أسهم مجانية",
                "SPLIT": "تجزئة",
                "CASH_DIVIDEND": "توزيع نقدي",
            }[v],
        )
        factor = columns[1].number_input(
            "عدد الأسهم بعد كل سهم (1.1 لمجانية 10%، 2.0 للتجزئة النصفية)",
            min_value=0.0, step=0.1, value=0.0, format="%.4f",
        )
        amount = columns[2].number_input("التوزيع لكل سهم", min_value=0.0,
                                         step=0.05, value=0.0, format="%.3f")
        effective = st.date_input("تاريخ الاستحقاق", value=date.today(),
                                  key="holdings_action_date")
        submitted = st.form_submit_button("تسجيل الإجراء")

    if not submitted:
        return
    if not symbol:
        st.error("اختر السهم أولًا.")
        return
    try:
        store.record_corporate_action(
            symbol, effective, kind,
            factor=factor if factor > 0 else None,
            amount_per_share=amount if amount > 0 else None,
            fee_model=fee_model,
        )
    except StoreError as error:
        st.error(f"الإجراء مرفوض: {error}")
        return
    st.rerun()


def _records(store, fee_model):
    trades = store.trades()
    if not trades:
        st.caption("لا توجد صفقات مسجلة بعد.")
        return
    frame = pd.DataFrame(trades)[
        ["id", "trade_date", "symbol", "side", "quantity", "price", "fees_egp", "note"]
    ]
    st.dataframe(frame, hide_index=True, use_container_width=True)

    columns = st.columns([2, 1])
    trade_id = columns[0].number_input("رقم الصفقة للحذف", min_value=0, step=1, value=0)
    if columns[1].button("حذف الصفقة", use_container_width=True):
        if trade_id <= 0:
            st.error("اكتب رقم صفقة صحيحًا.")
            return
        try:
            deleted = store.delete_trade(int(trade_id), fee_model=fee_model)
        except StoreError as error:
            st.error(f"لا يمكن الحذف: {error}")
            return
        if not deleted:
            st.error("لا توجد صفقة بهذا الرقم.")
            return
        st.rerun()


# --------------------------------------------------------------------------- #
# Bulk entry: invoices and the opening file
# --------------------------------------------------------------------------- #

STATUS_LABEL = {
    imports.NEW: "جديدة",
    imports.DUPLICATE: "مسجّلة من قبل",
    imports.CONFLICT: "تعارض",
    imports.UNRESOLVED: "رمز غير معروف",
    imports.NOT_EGX: "ليس سهمًا",
    imports.INVALID: "غير مقروءة",
}

STATUS_TONE = {
    imports.NEW: "green",
    imports.DUPLICATE: "gray",
    imports.CONFLICT: "red",
    imports.UNRESOLVED: "amber",
    imports.NOT_EGX: "blue",
    imports.INVALID: "red",
}


def _rows_frame(rows):
    return pd.DataFrame([
        {
            "الحالة": STATUS_LABEL.get(row.status, row.status),
            "المصدر": row.source_label,
            "التاريخ": row.trade_date,
            "السهم": row.symbol,
            "العملية": SIDE_ARABIC.get(row.side, row.side),
            "الكمية": row.quantity or None,
            "السعر": row.price or None,
            "العمولة": row.fees_egp,
            "القيمة": row.value_egp or None,
            "التفصيل": row.detail,
        }
        for row in rows
    ])


def _preview(rows):
    """Show every prospective row and return the ones that will be written."""

    if not rows:
        return []
    st.dataframe(
        _rows_frame(rows), hide_index=True, use_container_width=True,
        column_config={
            "الكمية": st.column_config.NumberColumn(format="%.0f"),
            "السعر": st.column_config.NumberColumn(format="%.4f"),
            "العمولة": st.column_config.NumberColumn(format="%.2f"),
            "القيمة": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    counts = Counter(row.status for row in rows)
    st.markdown(
        " ".join(
            badge_html(f"{STATUS_LABEL.get(status, status)}: {count}",
                       STATUS_TONE.get(status, "gray"))
            for status, count in counts.most_common()
        ),
        unsafe_allow_html=True,
    )
    for row in rows:
        if row.status in (imports.CONFLICT, imports.INVALID):
            st.warning(f"{row.source_label}: {row.detail}")
    return [row for row in rows if row.importable]


def _invoice_tab(store, fee_model):
    st.caption(
        "ارفع فواتير التنفيذ (PDF). الكمية والسعر والعمولة تُقرأ من الفاتورة "
        "نفسها — لا تخمين ولا قراءة صور — ويُرفض أي صفحة لا تتطابق حسابيًا. "
        "الملف نفسه لو اترفع مرتين مش هيتسجل مرتين."
    )
    uploaded = st.file_uploader(
        "فواتير PDF", type=["pdf"], accept_multiple_files=True,
        key="holdings_invoice_files",
    )
    funds_as_cash = st.checkbox(
        "سجّل صناديق النقد (مثل Thndr Savings) كحركة كاش", value=True,
        help="بيع الصندوق = دخول كاش، شراؤه = خروج كاش. من غير كده رصيد الكاش "
             "في الصفحة هيبقى ناقص.",
    )
    if not uploaded:
        return

    parsed = []
    for handle in uploaded:
        try:
            parsed.extend(invoices.parse_invoice_pdf(handle))
        except Exception as error:                               # noqa: BLE001
            st.error(f"{handle.name}: تعذّرت قراءة الملف — {error}")

    unresolved = [
        item for item in parsed
        if item.ok and item.instrument == invoices.EQUITY and not item.ticker
    ]
    manual = {}
    if unresolved:
        st.warning(
            "فيه أوراق مالية رمزها الدولي مش موجود في ملف الأسهم — اختار السهم "
            "بنفسك عشان تتسجل."
        )
        for item in unresolved:
            manual[item.reference] = _symbol_picker(
                f"holdings_map_{item.reference[:24]}",
                label=f"{item.security_name} ({item.symbol_code})",
            )

    rows = imports.validate_rows(
        imports.plan_invoice_import(
            parsed, store, manual_symbols=manual, funds_as_cash=funds_as_cash),
        store, fee_model=fee_model,
    )
    writable = _preview(rows)
    if not writable:
        st.info("لا توجد صفقات جديدة للتسجيل من هذه الملفات.")
        return
    if st.button(f"تسجيل {len(writable)} صفقة/حركة", type="primary",
                 key="holdings_invoice_commit"):
        result = imports.apply_import(store, rows, fee_model=fee_model)
        for label, reason in result.failures:
            st.error(f"{label}: {reason}")
        if result.written:
            st.success(f"تم تسجيل {result.written}.")
            st.rerun()


def _file_tab(store, fee_model):
    st.caption(
        "للمرة الأولى: ارفع ملفًا فيه مراكزك الحالية. لو السهم اشتريته على "
        "مرات كتير ومش عارف تفصل العمولة، استخدم قالب «المراكز الافتتاحية» — "
        "اكتب الكمية ومتوسط الشراء اللي عند الوسيط، وهيتسجل كما هو بدون إضافة "
        "أي عمولة فوقه، لأن المتوسط ده أصلاً شاملها."
    )
    columns = st.columns(2)
    columns[0].download_button(
        "⬇ قالب المراكز الافتتاحية", imports.OPENING_TEMPLATE,
        file_name="opening_positions.csv", mime="text/csv",
        use_container_width=True,
    )
    columns[1].download_button(
        "⬇ قالب الصفقات التفصيلية", imports.TRANSACTIONS_TEMPLATE,
        file_name="transactions.csv", mime="text/csv",
        use_container_width=True,
    )

    uploaded = st.file_uploader(
        "ملف CSV أو Excel", type=["csv", "xlsx"], key="holdings_import_file")
    if not uploaded:
        return

    try:
        if uploaded.name.lower().endswith(".xlsx"):
            frame = pd.read_excel(uploaded)
        else:
            frame = pd.read_csv(uploaded)
    except Exception as error:                                   # noqa: BLE001
        st.error(f"تعذّرت قراءة الملف: {error}")
        return

    shape = imports.detect_shape(frame.columns)
    if not shape:
        st.error(
            "أعمدة الملف غير مفهومة. لازم يكون فيه إما عمود العملية والسعر "
            "(صفقات تفصيلية) أو عمود متوسط الشراء (مراكز افتتاحية). نزّل قالبًا "
            "من فوق."
        )
        st.dataframe(frame.head(10), hide_index=True, use_container_width=True)
        return

    st.info(
        "الملف مقروء كـ "
        + ("مراكز افتتاحية — كل صف هيتسجل كعملية شراء واحدة بمتوسطه، بدون "
           "عمولة إضافية." if shape == imports.OPENING
           else "صفقات تفصيلية — العمولة الفارغة هتُحسب من جدول الرسوم.")
    )
    rows = imports.validate_rows(
        imports.plan_file_import(frame), store, fee_model=fee_model)
    writable = _preview(rows)
    if not writable:
        st.info("لا توجد صفوف صالحة للتسجيل.")
        return
    if st.button(f"تسجيل {len(writable)} صف", type="primary",
                 key="holdings_file_commit"):
        result = imports.apply_import(store, rows, fee_model=fee_model)
        for label, reason in result.failures:
            st.error(f"{label}: {reason}")
        if result.written:
            st.success(f"تم تسجيل {result.written} صف.")
            st.rerun()


def _entry(store, fee_model):
    section_header("تسجيل وتعديل", "Invoices, opening positions, and corrections")
    invoice_tab, file_tab, trades, cash, actions, records = st.tabs(
        ["فواتير اليوم", "أول مرة (ملف)", "صفقة يدوية", "كاش",
         "مجانية / تجزئة / توزيع", "السجل"])
    with invoice_tab:
        _invoice_tab(store, fee_model)
    with file_tab:
        _file_tab(store, fee_model)
    with trades:
        _trade_form(store, fee_model)
    with cash:
        _cash_form(store)
    with actions:
        _corporate_action_form(store, fee_model)
    with records:
        _records(store, fee_model)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

def _provenance(view):
    with st.expander("من أين تأتي هذه الأرقام؟ · Provenance"):
        fee = view.fee_model
        # Zero and unset are different facts and must not print the same line:
        # a configured zero is a decision about this account, while an unset
        # rate means nobody has said, and every net figure on the page would be
        # overstated by an unknown amount.
        if fee.tax_percent is None:
            tax_text = (
                " · ⚠️ لا توجد ضريبة أرباح مضبوطة في الإعدادات — "
                "الأرقام الصافية أعلاه لا تخصم أي ضريبة"
            )
        elif fee.tax_percent:
            tax_text = f" + ضريبة أرباح {fee.tax_percent}%"
        else:
            tax_text = " · لا ضريبة أرباح (مضبوطة صفرًا في الإعدادات)"
        st.markdown(
            f"**التكاليف** — {fee.percent_per_side * 100:.4f}% لكل جهة "
            f"+ {fee.order_fee_egp:.2f} جنيه رسم أمر" + tax_text
        )
        if fee.fee_lines:
            st.dataframe(
                pd.DataFrame(fee.fee_lines, columns=["البند", "النسبة %"]),
                hide_index=True, use_container_width=True,
            )
        policy = view.policy
        st.markdown(
            f"**سياسة الخروج** — جني {policy.partial_fraction * 100:.0f}% عند الهدف "
            f"الأول، ثم الوقف لسعر التعادل، وإغلاق بعد "
            f"{policy.max_holding_sessions} جلسة بلا هدف. مصدرها "
            f"`{policy.source}` — نفس السياسة التي اختُبر عليها الباك-تست."
        )
        sessions = {item.plan.session_date for item in view.positions
                    if item.plan and item.plan.session_date}
        reused = sum(1 for item in view.positions if item.plan_reused)
        st.markdown(
            f"**الخطط** — محسوبة من جلسة {' / '.join(sorted(sessions)) or '—'}، "
            f"و{reused} من {len(view.positions)} قُرئت من المخزن بدون إعادة "
            "قراءة الشموع. الخطة بتتحسب مرة واحدة لكل جلسة لكل سهم، وبتتعاد "
            "لو اتغيرت كمية المركز أو متوسطه."
        )
        st.markdown(
            "**الأهداف والوقف** — المقاومة (أعلى 20 جلسة بدون اليوم) ثم "
            "المقاومة + 2×ATR للأهداف، والدعم − 0.3×ATR للوقف: نفس تعريفات "
            "`strategy/entry.py`. الوقف يضيق ولا يتسع أبدًا."
        )
        st.markdown(
            "**قواعد السيولة** غير مُقاسة بعد على هذا السوق. لذلك لا تبيع "
            "بخسارة أبدًا — تحفظ ربحًا قائمًا أو تضيّق الوقف فقط — وكل توصية "
            "منها تُسجَّل بأدلتها ليُحكم عليها لاحقًا بما فعلت."
        )
        st.caption(f"آخر تحديث: {view.generated_at}")


def _log(store, view):
    with st.expander("سجل التوصيات · Recommendation log"):
        rows = store.recommendations(limit=100)
        if not rows:
            st.caption("لم تُسجَّل توصية بعد.")
        else:
            frame = pd.DataFrame(rows)[
                ["generated_at", "symbol", "action", "rule", "price", "price_basis",
                 "net_percent", "reason"]
            ]
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.caption(
                "كل توصية تطلب إجراءً تُسجَّل مرة واحدة لكل جلسة — هذا السجل هو ما "
                "سيُحكم به على القواعد بعد عدد كافٍ من الجلسات."
            )

    with st.expander("تاريخ خطط الخروج · Plan versions"):
        # The plan is versioned precisely so "why did it want 12.00 last week?"
        # stays answerable. That is only true if the versions are reachable.
        versions = []
        for item in view.positions:
            versions.extend(store.plan_history(item.symbol))
        if not versions:
            st.caption("لا توجد خطط محفوظة بعد.")
            return
        frame = pd.DataFrame(versions)[
            ["symbol", "version", "created_at", "session_date", "stop",
             "target_partial", "target_final", "source", "reason", "superseded_at"]
        ]
        st.dataframe(
            frame.sort_values(["symbol", "version"], ascending=[True, False]),
            hide_index=True, use_container_width=True,
            column_config={
                "stop": st.column_config.NumberColumn(format="%.3f"),
                "target_partial": st.column_config.NumberColumn(format="%.3f"),
                "target_final": st.column_config.NumberColumn(format="%.3f"),
            },
        )
        st.caption(
            "الصفوف التي لها تاريخ في superseded_at هي خطط قديمة — محفوظة كما "
            "كانت، لأن تعديل الخطة لا يُلغي سبب الخطة السابقة."
        )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def show_portfolio():
    page_header(
        "محفظتي",
        "Your real positions, and how to leave each one well",
        icon="💼",
        badge="PORTFOLIO",
    )
    store = _store()
    fee_model = load_fee_model()

    try:
        book = store.book(fee_model)
    except BookError as error:
        st.error(
            f"السجل المخزَّن غير متسق ولا يمكن حساب المتوسطات منه: {error}. "
            "صحّح الصف المسؤول من تبويب السجل بالأسفل."
        )
        _entry(store, fee_model)
        return

    if not book.open_positions:
        empty_state(
            "لا توجد مراكز مفتوحة",
            "سجّل أول عملية شراء من الأسفل، وستظهر هنا بمتوسطها وسعر تعادلها "
            "وخطة خروجها.",
            icon="💼",
        )
        _entry(store, fee_model)
        return

    session = expected_completed_session()
    controls = st.columns([1, 1, 2])
    if controls[0].button("🔄 تحديث الأسعار", type="primary",
                          use_container_width=True):
        _quotes.clear()
        _sector_intraday.clear()
        st.rerun()
    rebuild = controls[1].button(
        "♻ إعادة حساب الخطط", use_container_width=True,
        help="يعيد قراءة الشموع اليومية لكل سهم ويبني الخطط من جديد. "
             "بيحصل تلقائيًا مع كل جلسة جديدة، فمش محتاجه إلا لو عايز تجبره.",
    )
    if rebuild:
        _history.clear()

    symbols = tuple(position.symbol for position in book.open_positions)
    spinner = ("إعادة حساب الخطط من الشموع اليومية..." if rebuild
               else "تحديث الأسعار...")
    with st.spinner(spinner):
        quotes = _quotes(symbols)
        view = build_portfolio_view(
            store,
            fee_model=fee_model,
            history_loader=lambda symbol: _history(symbol, session),
            quote_loader=quotes.get,
            sector_map=_sector_map(),
            sector_strengths=_sector_strengths(),
            sector_intraday=_sector_intraday(),
            session=session,
            rebuild=rebuild,
        )
        log_recommendations(store, view)

    _actions(view)
    _summary(view)
    _positions_table(view)
    _concentration(view)
    _entry(store, fee_model)
    _log(store, view)
    _provenance(view)
