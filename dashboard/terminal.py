"""الطرفية · Terminal — the whole post-close picture on one screen.

Every other page in this application answers one question and is read by
scrolling. This one answers the question that is actually asked after the close
— *what do I hold, what did the scan find, and what is the market doing* — and
it answers it without scrolling: a fixed 900px frame, three columns, and panes
that scroll inside themselves while the page does not.

**It decides nothing and it runs nothing.** Every number is read from work that
has already happened: the portfolio book and its cached plans, the scan this
session adopted, the measured index store, the funnel a Breakout Watch scan
recorded. The rail's controls are links to the pages that own those jobs — a
second owner for the scan lifecycle is how two concurrent scans of the same
universe happened on 2026-08-03.

**Every pane fails alone.** A pane whose source is missing renders as
unavailable and says what would make it computable; it does not take the screen
down with it, and it never renders an absence as a zero.
"""

from __future__ import annotations

import html
import logging

import streamlit as st

from dashboard.ui import _scale_positions, apply_global_style

logger = logging.getLogger(__name__)

#: Never a zero, never an empty cell. A value nobody measured looks like
#: nothing else on the screen and is the one thing drawn in --unknown.
UNKNOWN = "—·—"

#: The frame the design was drawn to, and the reason the height below is a
#: calculation rather than this number: a literal 900px frame under Streamlit's
#: own header and padding ended 56px below a 900px viewport, which put the
#: status line -- the line that says where every number came from -- off the
#: bottom of the screen it was supposed to be pinned to.
TERMINAL_HEIGHT = 900

#: What is left above and below the frame once this page's own stylesheet has
#: collapsed the container's padding and hidden the Streamlit header.
TERMINAL_CHROME = 16


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #

def num(value, digits=2, *, plus=False):
    """A number, or the unknown mark. ``0`` is a number and prints as one."""
    try:
        if value is None:
            return UNKNOWN
        number = float(value)
    except (TypeError, ValueError):
        return UNKNOWN
    if number != number:                                  # NaN
        return UNKNOWN
    text = f"{number:,.{digits}f}"
    return f"+{text}" if plus and number > 0 else text


def pct(value, digits=2, *, plus=False):
    text = num(value, digits, plus=plus)
    return text if text == UNKNOWN else f"{text}%"


def unknown_cell():
    return f'<span class="q">{UNKNOWN}</span>'


def _tone_of(value):
    """green above zero, red below, nothing at zero or unknown."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number != number:
        return ""
    return "up" if number > 0 else ("dn" if number < 0 else "")


def esc(value):
    return html.escape("" if value is None else str(value))


# --------------------------------------------------------------------------- #
# chrome
# --------------------------------------------------------------------------- #

def rail_html(readings, actions):
    """The command rail: what session this is, and where to go to change it.

    ``readings`` are ``(label, value, tone)``; ``actions`` are ``(label, href,
    primary)``. The actions are links, not buttons: this page owns no job.
    """
    cells = []
    for label, value, tone in readings:
        dot = (f'<i style="background:var(--{tone})"></i>'
               if tone in ("green", "amber", "red", "unknown") else "")
        klass = {"green": "up", "red": "dn", "amber": "wn",
                 "unknown": "q"}.get(tone, "")
        cells.append(f'<span class="k">{dot}{esc(label)} '
                     f'<b class="{klass}">{esc(value)}</b></span>')
    links = "".join(
        f'<a class="{"go" if primary else ""}" href="{esc(href)}" '
        f'target="_self">{esc(label)}</a>'
        for label, href, primary in actions)
    return (f'<div class="rail"><span class="brand">EGX</span>'
            f'{"".join(cells)}<span class="sp"></span>{links}</div>')


def pane_html(title_en, title_ar, meta, body, *, fixed=""):
    """One pane: a header that stays, and a body that scrolls inside it."""
    head = (f'<div class="ph"><h2>{esc(title_en)}'
            f'<span class="ar">{esc(title_ar)}</span></h2>'
            f'<span class="meta">{esc(meta)}</span></div>')
    return (f'<div class="pane">{head}{fixed}'
            f'<div class="scroll">{body}</div></div>')


def unavailable_pane(title_en, title_ar, needs):
    """A pane that could not be computed, saying what would compute it.

    Not an empty table, and not a pane that quietly disappears: the reader has
    to be able to tell "nothing to show" from "this was never read".
    """
    return pane_html(
        title_en, title_ar, "unavailable",
        f'<div class="na"><b>{UNKNOWN}</b><p>{esc(needs)}</p></div>')


def status_html(cells):
    """The bottom line: where the numbers above came from."""
    rendered = []
    for label, value, tone in cells:
        dot = (f'<i style="background:var(--{tone})"></i>'
               if tone in ("green", "amber", "red", "unknown") else "")
        value_html = (f' <b>{esc(value)}</b>' if value else "")
        rendered.append(f'<span class="c">{dot}{esc(label)}{value_html}</span>')
    rendered.append('<span class="c sp"></span>')
    rendered.append(f'<span class="c">unknown is drawn {unknown_cell()}, '
                    f'never 0</span>')
    return f'<div class="status">{"".join(rendered)}</div>'


# --------------------------------------------------------------------------- #
# panes
# --------------------------------------------------------------------------- #

def figures_html(figures):
    """The four numbers that decide whether the pane below is worth reading."""
    cells = []
    for label, value, sub, tone in figures:
        cells.append(f'<div><div class="k">{esc(label)}</div>'
                     f'<div class="v {tone}">{esc(value)}</div>'
                     f'<div class="s">{esc(sub)}</div></div>')
    return f'<div class="figs">{"".join(cells)}</div>'


#: Two vocabularies meet on this screen and they are not the same one: the scan
#: decides BUY / WATCH / AVOID about a name it does not hold, and the portfolio
#: decides EXIT / TRIM / RAISE_STOP / WATCH / HOLD about one it does. Both are
#: listed here so neither is translated into the other.
#:
#: WITHHELD is deliberately absent. It means no recommendation could be made,
#: which is the unknown state, not a sixth action -- and anything else this map
#: has never seen is drawn the same way rather than being given a tone it has
#: not earned.
_RULE_CLASS = {"BUY": "buy", "WATCH": "watch", "AVOID": "avoid",
               "HOLD": "hold", "EXIT": "exit", "TRIM": "watch",
               "RAISE_STOP": "watch"}


def signal_chip(value):
    if not value:
        return unknown_cell()
    key = str(value).strip().upper()
    klass = _RULE_CLASS.get(key)
    if klass is None:
        return f'<span class="q">{esc(value)}</span>'
    return f'<span class="sig {klass}">{esc(key)}</span>'


def positions_body(rows):
    """One row per open position, worst first.

    Worst first because the money already at risk outranks the money that is
    not: a position 20% under water is the reason to open this screen, and
    sorting by symbol buries it in the middle of the alphabet.
    """
    body = []
    for row in rows:
        unpriced = row.get("last") is None
        tr = ' class="unk"' if unpriced else ""
        pnl, percent = row.get("pnl"), row.get("percent")
        body.append(
            f"<tr{tr}>"
            f'<td class="t">{esc(row.get("symbol"))}</td>'
            f'<td class="r">{num(row.get("quantity"), 0)}</td>'
            f'<td class="r">{num(row.get("average"), 3)}</td>'
            f'<td class="r">{unknown_cell() if unpriced else num(row.get("last"), 3)}</td>'
            f'<td class="r {_tone_of(pnl)}">'
            f'{unknown_cell() if pnl is None else num(pnl, 0, plus=True)}</td>'
            f'<td class="r {_tone_of(percent)}">'
            f'{unknown_cell() if percent is None else pct(percent, 2, plus=True)}</td>'
            f'<td>{signal_chip(row.get("rule"))}</td></tr>')
    header = ("<tr><th>Sym</th><th class='r'>Qty</th><th class='r'>Avg</th>"
              "<th class='r'>Last</th><th class='r'>P&amp;L</th>"
              "<th class='r'>%</th><th>Rule</th></tr>")
    return (f"<table><thead>{header}</thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def trade_rail(stop, low, high, price, target):
    """The trade drawn to scale, or nothing at all.

    ``_scale_positions`` is the dashboard card's geometry, reused rather than
    rewritten: it returns None unless every level exists and the span is
    positive, which is what stops a picture being drawn of a trade nobody
    computed.
    """
    places = _scale_positions(stop, low, high, price, target)
    if places is None:
        return unknown_cell()
    return (f'<span class="tr"></span>'
            f'<span class="bd" style="left:{places["band_left"]:.1f}%;'
            f'width:{places["band_width"]:.1f}%"></span>'
            f'<span class="nw" style="left:{places["now"]:.1f}%"></span>')


def candidates_body(rows):
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f'<td class="t">{esc(row.get("symbol"))}</td>'
            f'<td>{signal_chip(row.get("signal"))}</td>'
            f'<td class="rg">{esc(row.get("regime") or "")}</td>'
            f'<td class="r">{num(row.get("price"), 2)}</td>'
            f'<td class="r dn">{num(row.get("stop"), 2)}</td>'
            f'<td class="r">{num(row.get("target"), 2)}</td>'
            f'<td class="r up">{num(row.get("rr"), 2)}</td>'
            f'<td class="rail">'
            f'{trade_rail(row.get("stop"), row.get("buy_low"), row.get("buy_high"), row.get("price"), row.get("target"))}'
            f"</td></tr>")
    header = ("<tr><th>Sym</th><th>Rule</th><th>Regime</th><th class='r'>Last</th>"
              "<th class='r'>Stop</th><th class='r'>Target</th>"
              "<th class='r'>R:R</th><th>Stop &rarr; target</th></tr>")
    return (f"<table><thead>{header}</thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def kv_rows(rows):
    """``(label, value, tone)`` pairs, one per line."""
    out = []
    for label, value, tone in rows:
        klass = {"green": "up", "red": "dn", "amber": "wn",
                 "unknown": "q"}.get(tone, "")
        out.append(f'<div class="kv"><span class="k">{esc(label)}</span>'
                   f'<span class="{klass}">{esc(value)}</span></div>')
    return "".join(out)


def index_block(close, change, percent, previous):
    tone = _tone_of(change)
    detail = " · ".join(filter(None, [
        UNKNOWN if change is None else num(change, 2, plus=True),
        UNKNOWN if percent is None else pct(percent, 2, plus=True),
        f"prev {num(previous, 2)}",
    ]))
    return (f'<div class="idx"><div class="k">Close</div>'
            f'<div class="v">{esc(num(close, 2))}</div>'
            f'<div class="d {tone}">{esc(detail)}</div></div>')


def decision_bars(counts, total):
    """The three decisions, drawn to the same scale as one another.

    Widths are a share of the population the scan actually reached, so a bar
    is a picture of the same number the label carries. A decision nobody
    reached is drawn at zero width and still listed — a day with no buys is a
    fact about the day.
    """
    bars = []
    for label, count, klass in counts:
        width = (count / total * 100.0) if total else 0.0
        bars.append(
            f'<div class="bar"><span class="n">{esc(label)}</span>'
            f'<span class="t"><i class="{klass}" '
            f'style="width:{width:.1f}%"></i></span>'
            f'<span class="p">{count:,}</span></div>')
    return (f'<div class="bars"><div class="lbl">Decisions across '
            f'{total:,}</div>{"".join(bars)}</div>')


# --------------------------------------------------------------------------- #
# the frame
# --------------------------------------------------------------------------- #

def terminal_html(rail, left, middle, right_top, right_bottom, status):
    return (f'<div class="term">{rail}<div class="body">'
            f'<div class="col">{left}</div>'
            f'<div class="col">{middle}</div>'
            f'<div class="col split">{right_top}{right_bottom}</div>'
            f'</div>{status}</div>')


STYLE = """<style>
/* The page itself must not scroll: Streamlit's own top padding is what pushed
   a 900px frame past the fold on a 900px screen. Unscoped on purpose -- this
   sheet is injected by this page and lives only as long as it is rendered, and
   a class on a div INSIDE the container cannot reach the container. */
.block-container { padding:.4rem .5rem 0 !important; max-width:100% !important; }
/* The frame takes the screen it is on, whatever that screen is. */
header[data-testid="stHeader"] { display:none !important; }
/* The two stylesheets above the frame are invisible but not weightless: each
   is an element in the main vertical block, and the block's 1rem gap between
   them pushed the frame 48px down the page. */
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {
    gap:0 !important; }
.term { height:calc(100vh - __CHROME__px); display:grid;
    grid-template-rows:28px 1fr 22px; background:var(--surface);
    border:1px solid var(--border); overflow:hidden;
    font-family:var(--font-mono);
    font-size:11px; line-height:1.35; color:var(--text);
    font-variant-numeric:tabular-nums; font-feature-settings:"tnum" 1,"zero" 1; }
.term * { box-sizing:border-box; }

/* command rail */
.term .rail { display:flex; align-items:stretch; background:var(--bg-2);
    border-bottom:1px solid var(--border); }
.term .rail .brand { display:flex; align-items:center; padding:0 12px;
    font-weight:600; letter-spacing:.06em; border-right:1px solid var(--border); }
.term .rail .k { padding:0 10px; color:var(--text-low); font-size:9px;
    letter-spacing:.12em; text-transform:uppercase;
    border-right:1px solid var(--border); display:flex; align-items:center;
    gap:6px; white-space:nowrap; }
.term .rail .k b { color:var(--text); font-weight:500; font-size:11px;
    letter-spacing:0; text-transform:none; }
.term .rail .k i, .term .status .c i { width:5px; height:5px; border-radius:50%;
    display:block; flex-shrink:0; }
.term .rail .sp { flex:1; border-right:1px solid var(--border); }
.term .rail a { display:flex; align-items:center; padding:0 14px;
    color:var(--muted); font-size:9.5px; letter-spacing:.1em;
    text-transform:uppercase; text-decoration:none;
    border-right:1px solid var(--border); }
.term .rail a:last-child { border-right:0; }
.term .rail a:hover { background:rgba(255,255,255,.05); color:var(--text); }
.term .rail a.go { color:var(--green); }

/* body grid */
.term .body { display:grid; grid-template-columns:1fr 1.42fr .82fr;
    min-height:0; }
.term .col { display:grid; min-height:0; border-right:1px solid var(--border); }
.term .col:last-child { border-right:0; }
.term .col.split { grid-template-rows:1.15fr 1fr; }
.term .pane { display:flex; flex-direction:column; min-height:0;
    border-bottom:1px solid var(--border); }
.term .pane:last-child { border-bottom:0; }
.term .ph { display:flex; align-items:center; justify-content:space-between;
    gap:8px; padding:5px 10px; background:var(--bg-2);
    border-bottom:1px solid var(--border); flex-shrink:0; }
.term .ph h2 { font-size:9px; font-weight:600; letter-spacing:.14em;
    text-transform:uppercase; color:var(--muted); margin:0; }
.term .ph h2 .ar { font-family:var(--font-ar); font-size:10px;
    color:var(--text-low); font-weight:400; letter-spacing:0;
    text-transform:none; margin-right:6px; }
.term .ph .meta { font-size:9.5px; color:var(--text-low); letter-spacing:.04em;
    white-space:nowrap; }
.term .scroll { overflow:auto; min-height:0; flex:1; }
.term .scroll::-webkit-scrollbar { width:7px; height:7px; }
.term .scroll::-webkit-scrollbar-thumb { background:var(--border); }

/* the four figures */
.term .figs { display:grid; grid-template-columns:repeat(4,1fr);
    border-bottom:1px solid var(--border); flex-shrink:0; }
.term .figs > div { padding:6px 10px; border-right:1px solid var(--border); }
.term .figs > div:last-child { border-right:0; }
.term .figs .k, .term .idx .k, .term .bars .lbl { font-size:8.5px;
    letter-spacing:.1em; text-transform:uppercase; color:var(--text-low); }
.term .figs .v { font-size:15px; font-weight:600; margin-top:2px;
    letter-spacing:-.01em; }
.term .figs .s { font-size:9px; color:var(--text-low); margin-top:1px; }

/* tables */
.term table { width:100%; border-collapse:collapse; }
.term th { position:sticky; top:0; background:var(--bg-2); font-size:8.5px;
    font-weight:600; letter-spacing:.1em; text-transform:uppercase;
    color:var(--text-low); text-align:left; padding:4px 8px;
    border-bottom:1px solid var(--border); z-index:1; }
.term th.r, .term td.r { text-align:right; }
.term td { padding:3px 8px; border-bottom:1px solid rgba(255,255,255,.035);
    white-space:nowrap; }
.term tbody tr:hover td { background:rgba(255,255,255,.035); }
.term td.t { font-weight:600; color:var(--text); }
.term .up { color:var(--green); } .term .dn { color:var(--red); }
.term .wn { color:var(--amber); } .term .q { color:var(--unknown); }
/* A row the market never priced is hatched, the same hatch an unavailable
   gate wears elsewhere: it is not a row with a zero in it. */
.term tr.unk td { background:repeating-linear-gradient(45deg,
    rgba(192,132,252,.07) 0 3px, transparent 3px 6px); }
.term .sig { font-size:8.5px; font-weight:600; letter-spacing:.06em;
    padding:1px 4px; border:1px solid; }
.term .sig.buy { color:var(--green); border-color:rgba(61,220,151,.45); }
.term .sig.watch { color:var(--amber); border-color:rgba(255,176,32,.4); }
.term .sig.avoid, .term .sig.hold { color:var(--text-low);
    border-color:var(--border); }
.term .sig.exit { color:var(--red); border-color:rgba(255,92,108,.45); }
.term .rg { color:var(--text-low); font-size:9px; letter-spacing:.04em; }

/* one trade drawn to scale */
.term td.rail { width:118px; position:relative; padding:3px 8px; }
.term td.rail .tr { display:block; height:5px; background:var(--bg-2);
    border:1px solid var(--border); }
.term td.rail .bd { position:absolute; top:5px; bottom:5px;
    background:rgba(139,151,170,.55); }
.term td.rail .nw { position:absolute; top:3px; bottom:3px; width:1.5px;
    background:var(--text); }

/* market */
.term .idx { padding:8px 10px; border-bottom:1px solid var(--border);
    flex-shrink:0; }
.term .idx .v { font-size:20px; font-weight:600; letter-spacing:-.02em;
    margin-top:1px; }
.term .idx .d { font-size:10px; margin-top:1px; }
.term .kv { display:flex; justify-content:space-between; gap:8px;
    padding:3.5px 10px; border-bottom:1px solid rgba(255,255,255,.035);
    font-size:10.5px; }
.term .kv .k { color:var(--text-low); }
.term .bars { padding:9px 10px; }
.term .bars .lbl { margin-bottom:6px; }
.term .bar { display:grid; grid-template-columns:44px 1fr 30px; gap:7px;
    align-items:center; margin-bottom:4px; font-size:10px; }
.term .bar .n { color:var(--muted); }
.term .bar .t { height:6px; background:var(--bg-2);
    border:1px solid var(--border); position:relative; }
.term .bar .t i { position:absolute; top:0; bottom:0; left:0; display:block;
    background:var(--text-low); }
.term .bar .t i.w { background:var(--amber); opacity:.75; }
.term .bar .p { text-align:right; color:var(--muted); }

/* a pane that could not be computed */
.term .na { padding:14px 12px; }
.term .na b { color:var(--unknown); font-size:15px; }
.term .na p { margin:4px 0 0; color:var(--text-low); font-size:10px;
    line-height:1.6; max-width:34em; }

/* status line */
.term .status { display:flex; align-items:stretch; background:var(--bg-2);
    border-top:1px solid var(--border); font-size:9.5px;
    color:var(--text-low); }
.term .status .c { padding:0 10px; display:flex; align-items:center; gap:6px;
    border-right:1px solid var(--border); white-space:nowrap; }
.term .status .c b { color:var(--muted); font-weight:500; }
.term .status .sp { flex:1; }
.term .status .c:last-child { border-right:0; }
</style>
"""


def style_html(chrome=TERMINAL_CHROME):
    """The stylesheet, with what sits above and below the frame subtracted.

    A plain replace rather than %-formatting: the sheet is full of literal
    percent signs (``width:100%``, ``50%``, ``!important``) and every one of
    them would have to be doubled, which is a footgun sitting in the middle of
    a stylesheet nobody expects to be a format string.
    """
    return STYLE.replace("__CHROME__", str(int(chrome)))


# --------------------------------------------------------------------------- #
# reading what already happened
# --------------------------------------------------------------------------- #

def position_rows(view):
    """``view.positions`` as display rows, worst first.

    Nothing is computed here: quantity, average and the net figures are read
    from the book, and the rule is the recommendation the holdings engine
    already made. A position the market never priced keeps its quantity and
    average -- those are facts -- and carries ``None`` everywhere a price would
    be, so the row is drawn hatched rather than filled with zeros.
    """
    rows = []
    for item in view.positions:
        value, price = item.value, item.price
        recommendation = item.recommendation
        rows.append({
            "symbol": item.symbol,
            "quantity": item.position.quantity,
            "average": item.position.average_price,
            "last": price.value if price is not None else None,
            "pnl": value.unrealized_net_egp if value else None,
            "percent": value.unrealized_net_percent if value else None,
            "rule": (recommendation.action if recommendation is not None
                     else None),
        })
    # Worst first. `None` sorts last rather than as a zero: an unpriced
    # position is not a position that broke even.
    return sorted(rows, key=lambda row: (row["percent"] is None,
                                         row["percent"] or 0.0))


def candidate_rows(results, limit=60):
    """The scan's rows as the terminal draws them.

    ``limit`` is a drawing limit, not a filter: the pane scrolls, and the count
    in its header is the whole population, so a reader is never shown a short
    list that looks complete.
    """
    rows = []
    for row in results[:limit]:
        rows.append({
            "symbol": str(row.get("Ticker", "")).split(".")[0],
            "signal": row.get("Signal"),
            "regime": row.get("Regime"),
            "price": row.get("Price"),
            "stop": row.get("StopLoss"),
            "target": row.get("Target2") or row.get("Target1"),
            "rr": row.get("RR"),
            "buy_low": row.get("BuyLow"),
            "buy_high": row.get("BuyHigh"),
        })
    return rows


def index_reading():
    """EGX30 as the measured store holds it, with its own lag.

    Read through ``research_router`` so this page sees exactly the frame the
    strategy's market filter would see -- including how many sessions behind
    the equities beside it the index is.
    """
    from core.research_router import get_market_index_history

    frame = get_market_index_history("^CASE30", min_bars=250)
    closes = frame["Close"]
    close = float(closes.iloc[-1])
    previous = float(closes.iloc[-2]) if len(closes) > 1 else None
    meta = dict(frame.attrs.get("market_data", {}))
    change = None if previous is None else close - previous
    percent = (None if not previous else change / previous * 100.0)
    return {
        "close": close, "previous": previous, "change": change,
        "percent": percent,
        "session": meta.get("latest_completed_session"),
        "lag": meta.get("session_lag"),
        "provider": meta.get("provider"),
    }


def market_gates():
    """What the market filter says, and whether it is switched on.

    ``strategy.market_analyzer`` is the same function the decision engine
    calls; its answer is restated here, never recomputed. The filter being off
    is a decision that was recorded on 2026-09-11 after it was measured, so it
    is reported as a choice rather than as a fault.
    """
    from datetime import date as _date

    from strategy.config import load as load_strategy_config
    from strategy.market_analyzer import analyze

    cfg = load_strategy_config()
    verdict = analyze(_date.today().isoformat(), cfg)
    required = bool(getattr(cfg, "REQUIRE_MARKET_ANALYZER", False))
    return {
        "regime": verdict.get("Regime", "UNKNOWN"),
        "available": bool(verdict.get("Available")),
        "passed": bool(verdict.get("Passed")),
        "required": required,
        "reason": (verdict.get("Reasons") or [""])[0],
    }


def decision_counts(results):
    """How many names each decision reached, over the population it reached."""
    counts = {"BUY": 0, "WATCH": 0, "AVOID": 0}
    for row in results:
        signal = str(row.get("Signal") or "").upper()
        if signal in counts:
            counts[signal] += 1
    return counts


def funnel_reading():
    """The gate funnel, if a Breakout Watch scan recorded one this session.

    A saved watchlist carries its candidates and not its funnel, so there is
    nothing on disk to read: this returns ``None`` rather than reconstructing
    counts from the survivors, which would be a different measurement wearing
    the same labels.
    """
    return st.session_state.get("breakout_watch_funnel")


def run_status_reading(session=None):
    """Whether this morning's scheduled run happened, in four states.

    The same four the sidebar panel reports, because "the run failed" and "I
    could not find out whether the run happened" send a reader to different
    places and were one state until they were separated.
    """
    from datetime import date as _date

    from services.automation_status import NEVER_RAN, UNREADABLE, read_status

    status = read_status(session or _date.today().isoformat())
    if status.healthy:
        return "completed", "green"
    if status.outcome == UNREADABLE:
        return "unreadable", "unknown"
    if status.outcome == NEVER_RAN:
        return "not recorded", "amber"
    if status.outcome == "RUNNING":
        return "in flight", "amber"
    return str(status.outcome).lower(), "red"


def frozen_record_session():
    from core.frozen_mubasher_store import read_manifest

    manifest = read_manifest()
    return (manifest.get("history_db_last_session")
            or manifest.get("export_last_session"))


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #

def _positions_pane():
    """The book, or why it could not be read. Never a half-drawn book."""
    from dashboard.portfolio import (_history, _quotes, _risk_egp,
                                     _sector_intraday, _sector_map,
                                     _sector_strengths, _store)
    from holdings.assistant import (build_portfolio_view,
                                    expected_completed_session)
    from holdings.book import load_fee_model

    store, fee_model = _store(), load_fee_model()
    book = store.book(fee_model)
    if not book.open_positions:
        return pane_html(
            "Positions", "المراكز", "no open position",
            '<div class="na"><b>0</b><p>The book is empty. That is a measured '
            'answer, not a missing reading: no position is open.</p></div>'), 0

    session = expected_completed_session()
    symbols = tuple(position.symbol for position in book.open_positions)
    view = build_portfolio_view(
        store, fee_model=fee_model,
        history_loader=lambda symbol: _history(symbol, session),
        quote_loader=_quotes(symbols).get,
        sector_map=_sector_map(), sector_strengths=_sector_strengths(),
        sector_intraday=_sector_intraday(), session=session, rebuild=False)

    rows = position_rows(view)
    under = sum(1 for row in rows
                if row["percent"] is not None and row["percent"] < 0)
    risk = sum(value for value in (_risk_egp(item) for item in view.positions)
               if value is not None)
    equity = view.total_equity_egp
    net = view.unrealized_net_egp
    cash = view.book.cash_egp
    figures = figures_html([
        ("Market value", num(view.market_value_egp, 0), "EGP", ""),
        ("Unrealised", num(net, 0, plus=True), "after costs", _tone_of(net)),
        ("At risk", num(risk, 0),
         UNKNOWN if not equity else f"{risk / equity * 100:.1f}% of capital", ""),
        ("Cash", num(cash, 0, plus=True),
         "overdrawn" if (cash or 0) < 0 else "settled", _tone_of(cash)),
    ])
    unpriced = view.unpriced_count
    meta = f"{len(rows)} open · {under} under water"
    if unpriced:
        meta += f" · {unpriced} unpriced"
    return (pane_html("Positions", "المراكز", meta, positions_body(rows),
                      fixed=figures), unpriced)


def _candidates_pane():
    results = st.session_state.get("results")
    if not results:
        return unavailable_pane(
            "Candidates", "مرشحو اليوم",
            "No scan has been adopted in this session. Run the daily scan from "
            "the Daily Dashboard — this screen reads a scan, it never starts "
            "one."), None
    counts = decision_counts(results)
    coverage = getattr(results, "universe_coverage", None)
    attempted = getattr(coverage, "attempted", None) if coverage else None
    analysed = len(results)
    meta = (f"{counts['BUY']} buy · {counts['WATCH']} watch · "
            f"{counts['AVOID']} avoid · {analysed} analysed")
    if attempted:
        meta += f" of {attempted}"
    return (pane_html("Candidates", "مرشحو اليوم", meta,
                      candidates_body(candidate_rows(results))),
            (counts, analysed, attempted))


def _market_pane(decisions):
    index = index_reading()
    gates = market_gates()
    lag = index.get("lag")
    rows = [
        ("Regime", gates["regime"],
         {"BULL": "green", "BEAR": "red"}.get(gates["regime"], "unknown")),
        ("Index read", "available" if gates["available"] else "no index",
         "" if gates["available"] else "unknown"),
        ("Blocks new buys",
         ("yes" if not gates["passed"] else "no") if gates["required"]
         else "off by choice",
         "" if gates["required"] else "unknown"),
        ("Index session", index.get("session") or UNKNOWN, ""),
        ("Index age", UNKNOWN if lag is None else f"{lag} sessions",
         "amber" if (lag or 0) >= 2 else ""),
        ("Source", index.get("provider") or UNKNOWN, ""),
    ]
    body = kv_rows(rows)
    if decisions:
        counts, analysed, _ = decisions
        body += decision_bars([("buy", counts["BUY"], ""),
                               ("watch", counts["WATCH"], "w"),
                               ("avoid", counts["AVOID"], "")], analysed)
    return pane_html("Market", "السوق", "EGX30", body,
                     fixed=index_block(index["close"], index["change"],
                                       index["percent"], index["previous"]))


#: The funnel's keys as the watch rule records them, in the rule's own order.
FUNNEL_LABELS = (
    ("LongTermTrend", "long-term trend"), ("Liquidity", "liquidity"),
    ("Calm", "calm"), ("OutOfReach", "out of reach"),
    ("AboveTrigger", "already above trigger"),
    ("InvalidRisk", "invalid risk"),
    ("InsufficientHistory", "insufficient history"),
    ("PriceIntegrity", "price integrity"), ("Unusable", "unreadable"),
)


def _funnel_pane():
    funnel = funnel_reading()
    if not funnel:
        return unavailable_pane(
            "Where names stopped", "البوابات",
            "A saved watchlist carries its candidates, not its funnel. Run a "
            "fresh scan on Breakout Watch and the first-refusal counts appear "
            "here — they are not reconstructed from the survivors, which would "
            "be a different measurement wearing the same labels.")
    counts = funnel.get("counts", {})
    survivors = funnel.get("candidates")
    rows = [(label, f"{int(counts.get(key, 0)):,}",
             "unknown" if not counts.get(key) else "")
            for key, label in FUNNEL_LABELS]
    body = kv_rows(rows)
    if survivors is not None:
        body += (f'<div class="kv" style="border-top:1px solid var(--border)">'
                 f'<span class="k">approaching trigger</span>'
                 f'<span class="up">{int(survivors):,}</span></div>')
    session = funnel.get("session")
    return pane_html("Where names stopped", "البوابات",
                     f"first refusal · {session or UNKNOWN}", body)


def show_terminal():
    """One screen. Everything on it was measured somewhere else."""
    apply_global_style()
    # Its own call. Concatenated after a <div>, Markdown closed the HTML block
    # at the blank line and printed the whole stylesheet on screen as text.
    st.markdown(style_html(), unsafe_allow_html=True)

    def build(builder, title_en, title_ar):
        """Every pane fails alone, and says so where it would have drawn."""
        try:
            return builder()
        except Exception as error:                          # noqa: BLE001
            logger.exception("terminal pane %s could not be built", title_en)
            return unavailable_pane(
                title_en, title_ar,
                f"This pane could not be read: {type(error).__name__}. The rest "
                f"of the screen is unaffected, and nothing here was guessed.")

    positions, unpriced = _unpack(build(_positions_pane, "Positions", "المراكز"))
    candidates, decisions = _unpack(
        build(_candidates_pane, "Candidates", "مرشحو اليوم"))
    market = build(lambda: _market_pane(decisions), "Market", "السوق")
    funnel = build(_funnel_pane, "Where names stopped", "البوابات")

    # --- the rail ----------------------------------------------------------
    session, index, run = None, None, (UNKNOWN, "unknown")
    try:
        from holdings.assistant import expected_completed_session

        session = str(expected_completed_session())
    except Exception:                                       # noqa: BLE001
        logger.exception("terminal could not read the expected session")
    try:
        index = index_reading()
    except Exception:                                       # noqa: BLE001
        logger.exception("terminal could not read the index")
    try:
        run = run_status_reading(session)
    except Exception:                                       # noqa: BLE001
        logger.exception("terminal could not read the run status")

    # The day is in the label. The sidebar panel reports TODAY's run and this
    # reports the run for the session on screen; unlabelled, the two read as a
    # contradiction when the session is not today.
    readings = [("session", session or UNKNOWN, ""),
                (f"daily run {session}" if session else "daily run",
                 run[0], run[1])]
    if index:
        readings.append(("EGX30", num(index["close"], 2),
                         "green" if (index["change"] or 0) > 0 else "red"))
    else:
        readings.append(("EGX30", UNKNOWN, "unknown"))
    rail = rail_html(readings, [
        ("run scan", "/", True),
        ("portfolio", "/portfolio", False),
        ("settings", "/show_settings", False),
    ])

    # --- the status line ---------------------------------------------------
    cells = []
    try:
        from config.settings_manager import settings

        cells.append(("history", str(settings.get("live_history_source")
                                     or UNKNOWN), "green"))
    except Exception:                                       # noqa: BLE001
        logger.exception("terminal could not read the history route")
        cells.append(("history", UNKNOWN, "unknown"))
    try:
        cells.append(("frozen record", str(frozen_record_session() or UNKNOWN), ""))
    except Exception:                                       # noqa: BLE001
        logger.exception("terminal could not read the frozen manifest")
        cells.append(("frozen record", UNKNOWN, "unknown"))
    if decisions:
        _, analysed, attempted = decisions
        cells.append(("coverage",
                      f"{analysed}/{attempted}" if attempted else str(analysed),
                      ""))
    else:
        cells.append(("coverage", UNKNOWN, "unknown"))
    if unpriced:
        cells.append((f"{unpriced} position unpriced", "", "unknown"))

    st.markdown(terminal_html(rail, positions, candidates, market, funnel,
                              status_html(cells)), unsafe_allow_html=True)


def _unpack(built):
    """A pane builder returns either markup or ``(markup, extra)``.

    A failed builder returns markup alone, so the extra reading it would have
    produced becomes ``None`` -- which every consumer below already treats as
    "not measured" rather than as zero.
    """
    if isinstance(built, tuple):
        return built
    return built, None
