"""Original Arabic PNG analysis card for the AI Stock Analysis feature.

Layer 4 of the contract (see ``core/ai_stock_analysis_contract``): the exported card is
composed from ALREADY-COMPUTED evidence and ALREADY-WRITTEN narrative. This module renders
pre-formatted display strings — it never derives, re-derives, rounds into, or invents a
numeric value, and it never touches a provider.

Two problems are solved here, both without adding a dependency:

  1. **Arabic shaping + bidi.** Pillow is built without Raqm/HarfBuzz in this environment,
     so ``ImageDraw.text`` would otherwise emit disconnected, left-to-right Arabic. A small
     self-contained text engine (:func:`shape_arabic`, :func:`bidi_reorder`) derives the
     Arabic joining forms from ``unicodedata`` itself and reorders mixed Arabic/Latin/number
     text for a right-to-left paragraph. It is a pragmatic subset of the Unicode
     Bidirectional Algorithm — sufficient for card copy, not a general bidi library.
  2. **Fonts.** An installed Arabic-capable *system* font is discovered at render time. No
     font file is bundled, added, or committed.

The layout, palette and identity mark are original to this project.
"""

from __future__ import annotations

import io
import os
import unicodedata
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

__all__ = [
    "CARD_SIZES", "DEFAULT_CARD_SIZE",
    "ARABIC_FONT_CANDIDATES", "resolve_font_family", "FontUnavailableError",
    "shape_arabic", "bidi_reorder", "display_text",
    "CardChartData", "render_card_png",
]


# --------------------------------------------------------------------------- #
# Card sizes
# --------------------------------------------------------------------------- #

CARD_SIZES = {
    "POST": (1080, 1350),        # required
    "STORY": (1080, 1920),       # optional
}
DEFAULT_CARD_SIZE = "POST"


# --------------------------------------------------------------------------- #
# 1. Arabic text engine — joining forms + pragmatic bidi
# --------------------------------------------------------------------------- #

_FORM_TAGS = ("isolated", "final", "initial", "medial")
_TRANSPARENT_CATEGORIES = frozenset({"Mn", "Me", "Cf"})

TATWEEL = "\u0640"
LAM = "\u0644"
ALEFS = frozenset("\u0622\u0623\u0625\u0627")     # آ أ إ ا


def _build_form_tables():
    """Derive presentation-form tables from ``unicodedata`` (no hand-written table).

    Every Arabic presentation form in U+FB50..U+FEFF decomposes to its base letter with an
    ``<isolated>``/``<final>``/``<initial>``/``<medial>`` compatibility tag, so the whole
    shaping table is available from the Unicode database that ships with Python.

    Only the mandatory lam-alef ligatures (U+FEF5..U+FEFC) are collected as ligatures;
    the optional Presentation Forms-A ligatures are deliberately ignored because font
    support for them is inconsistent.
    """
    forms: dict[str, dict[str, str]] = {}
    ligatures: dict[str, dict[str, str]] = {}
    for code_point in range(0xFB50, 0xFF00):
        char = chr(code_point)
        decomposition = unicodedata.decomposition(char)
        if not decomposition:
            continue
        parts = decomposition.split()
        tag = parts[0].strip("<>")
        if tag not in _FORM_TAGS:
            continue
        base = "".join(chr(int(p, 16)) for p in parts[1:])
        if len(base) == 1:
            forms.setdefault(base, {})[tag] = char
        elif len(base) == 2 and 0xFEF5 <= code_point <= 0xFEFC:
            ligatures.setdefault(base, {})[tag] = char
    return forms, ligatures


_FORMS, _LIGATURES = _build_form_tables()

# Form fallback chains — a lam-alef ligature has only isolated/final shapes.
_FORM_FALLBACK = {
    "isolated": ("isolated",),
    "final": ("final", "isolated"),
    "initial": ("initial", "isolated"),
    "medial": ("medial", "final", "isolated"),
}


def _joining_type(char: str) -> str:
    """Arabic Joining_Type, derived from which presentation forms the letter has.

    ``D`` dual-joining, ``R`` right-joining, ``C`` join-causing (tatweel),
    ``T`` transparent (harakat / combining marks), ``U`` non-joining.
    """
    if char == TATWEEL:
        return "C"
    if unicodedata.category(char) in _TRANSPARENT_CATEGORIES:
        return "T"
    available = _FORMS.get(char)
    if not available:
        return "U"
    if "initial" in available and "medial" in available:
        return "D"
    if "final" in available:
        return "R"
    return "U"                                   # e.g. bare hamza: isolated form only


def _clusterize(text: str):
    """Split text into shaping clusters; a lam+alef pair becomes one right-joining cluster."""
    clusters: list[tuple[str, str]] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == LAM and index + 1 < length and text[index + 1] in ALEFS:
            clusters.append((char + text[index + 1], "R"))
            index += 2
            continue
        clusters.append((char, _joining_type(char)))
        index += 1
    return clusters


def _select_form(cluster: str, tag: str) -> str:
    table = _LIGATURES.get(cluster) or _FORMS.get(cluster)
    if not table:
        return cluster
    for candidate in _FORM_FALLBACK[tag]:
        if candidate in table:
            return table[candidate]
    return cluster


def shape_arabic(text: str) -> str:
    """Replace Arabic letters with their contextual presentation forms (logical order).

    A letter joins to the letter *before* it when that letter is dual-joining or
    join-causing, and to the letter *after* it when both sides permit. Transparent marks
    are skipped when looking for neighbours, so a harakat never breaks a join.
    """
    if not text:
        return text
    clusters = _clusterize(text)
    total = len(clusters)
    out: list[str] = []
    for index, (cluster, joining) in enumerate(clusters):
        if joining in ("T", "U"):
            out.append(cluster)
            continue

        previous = index - 1
        while previous >= 0 and clusters[previous][1] == "T":
            previous -= 1
        following = index + 1
        while following < total and clusters[following][1] == "T":
            following += 1

        previous_type = clusters[previous][1] if previous >= 0 else None
        next_type = clusters[following][1] if following < total else None

        joins_previous = joining in ("D", "R", "C") and previous_type in ("D", "C")
        joins_next = joining in ("D", "C") and next_type in ("D", "R", "C")

        if joins_previous and joins_next:
            tag = "medial"
        elif joins_previous:
            tag = "final"
        elif joins_next:
            tag = "initial"
        else:
            tag = "isolated"
        out.append(_select_form(cluster, tag))
    return "".join(out)


# --- bidi ------------------------------------------------------------------- #

_ET_CHARS = frozenset("+-%\u2030\u00b0$\u00a3\u20ac")      # European terminators
_NUMBER_ADJACENT = frozenset(".,:/\u066b\u066c")            # separators inside numbers


def _bidi_class(char: str) -> str:
    """A coarse bidi class: ``R``, ``L``, ``EN``, ``ET`` or ``N`` (neutral)."""
    code = ord(char)
    if 0x0590 <= code <= 0x08FF or 0xFB1D <= code <= 0xFEFF:
        return "R"
    if char.isdigit():
        return "EN"
    if char in _ET_CHARS:
        return "ET"
    if char in _NUMBER_ADJACENT:
        return "N"
    if char.isalpha():
        return "L"
    return "N"


def _resolve_levels(classes: list[str], base_level: int) -> list[int]:
    """Assign an embedding level per character (1 = RTL run, 2 = LTR/number run)."""
    total = len(classes)
    resolved = list(classes)

    # W5-ish: a run of European terminators next to a number joins the number.
    index = 0
    while index < total:
        if resolved[index] != "ET":
            index += 1
            continue
        end = index
        while end < total and resolved[end] == "ET":
            end += 1
        before = resolved[index - 1] if index > 0 else None
        after = resolved[end] if end < total else None
        replacement = "EN" if "EN" in (before, after) else "N"
        for position in range(index, end):
            resolved[position] = replacement
        index = end

    strong_level = {"R": base_level if base_level % 2 else base_level + 1,
                    "L": base_level + 1 if base_level % 2 else base_level,
                    "EN": base_level + 1 if base_level % 2 else base_level}
    levels: list[int | None] = [strong_level.get(cls) for cls in resolved]

    # Neutrals between two runs of the same level take that level, else the base level.
    index = 0
    while index < total:
        if levels[index] is not None:
            index += 1
            continue
        end = index
        while end < total and levels[end] is None:
            end += 1
        before = levels[index - 1] if index > 0 else None
        after = levels[end] if end < total else None
        fill = before if (before is not None and before == after) else base_level
        for position in range(index, end):
            levels[position] = fill
        index = end
    return [base_level if level is None else level for level in levels]


def bidi_reorder(text: str, *, base_rtl: bool = True) -> str:
    """Reorder ``text`` from logical order into visual (left-to-right drawing) order.

    Implements the Unicode rule L2 over the coarse levels resolved above: reverse every
    contiguous run at each level from the highest down to the lowest odd level. Latin words
    and numbers keep their internal left-to-right order inside a right-to-left line.

    This is a pragmatic subset of the full bidirectional algorithm — it has no explicit
    directional formatting codes, no paragraph splitting and no mirroring table beyond the
    bracket pairs below. It is intended for short card copy.
    """
    if not text:
        return text
    base_level = 1 if base_rtl else 0
    levels = _resolve_levels([_bidi_class(char) for char in text], base_level)
    chars = list(text)

    highest = max(levels)
    lowest_odd = min((level for level in levels if level % 2), default=highest + 1)
    for level in range(highest, lowest_odd - 1, -1):
        index = 0
        while index < len(chars):
            if levels[index] < level:
                index += 1
                continue
            end = index
            while end < len(chars) and levels[end] >= level:
                end += 1
            chars[index:end] = chars[index:end][::-1]
            levels[index:end] = levels[index:end][::-1]
            index = end

    mirrored = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{",
                "<": ">", ">": "<"}
    return "".join(mirrored.get(char, char) if level % 2 else char
                   for char, level in zip(chars, levels))


def display_text(text: str, *, base_rtl: bool = True) -> str:
    """Shape then reorder ``text`` so Pillow can draw it left-to-right correctly.

    Text with no Arabic letters is returned untouched.
    """
    if not text:
        return ""
    if not any(_bidi_class(char) == "R" for char in text):
        return text
    return bidi_reorder(shape_arabic(text), base_rtl=base_rtl)


# --------------------------------------------------------------------------- #
# 2. Fonts — installed system fonts only
# --------------------------------------------------------------------------- #

class FontUnavailableError(RuntimeError):
    """Raised when no installed Arabic-capable system font can be found."""


# (regular, bold) filenames, most preferred first. Discovered on the system font path —
# nothing is downloaded, bundled or committed.
#
# Note: a font shipping Arabic *letters* does not necessarily ship the Arabic Presentation
# Forms-B block this renderer draws. Dubai, for example, is missing 52 of them, so it is
# not a candidate here — see :func:`_font_supports_arabic`.
ARABIC_FONT_CANDIDATES = (
    ("segoeui.ttf", "segoeuib.ttf"),
    ("tahoma.ttf", "tahomabd.ttf"),
    ("arial.ttf", "arialbd.ttf"),
    ("NotoNaskhArabic-Regular.ttf", "NotoNaskhArabic-Bold.ttf"),
    ("Arial Unicode.ttf", "Arial Unicode.ttf"),
)

_FONT_DIRECTORIES = (
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype",
    "/usr/share/fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
)

# Every assigned Arabic Presentation Forms-B character \u2014 exactly what the shaper emits.
_ARABIC_FORM_CHARS = tuple(
    chr(code) for code in range(0xFE70, 0xFEFD)
    if unicodedata.category(chr(code)) != "Cn")

_font_support_cache: dict[str, bool] = {}


def _glyph_signature(font, char: str):
    mask = font.getmask(char)
    return mask.size, bytes(mask)


def _font_supports_arabic(path: str) -> bool:
    """True when the font has a real glyph for every Arabic presentation form.

    A missing glyph still renders \u2014 as the font's ``.notdef`` box \u2014 so measuring the mask
    size is not enough. Each form's rendered bitmap is compared against the ``.notdef``
    bitmap (probed through an unassigned private-use code point); an exact match means the
    form is absent and the font would draw a row of empty boxes.
    """
    if path in _font_support_cache:
        return _font_support_cache[path]
    supported = False
    try:
        font = ImageFont.truetype(path, 24)
        notdef = _glyph_signature(font, "\uE000")
        supported = all(_glyph_signature(font, char) != notdef
                        for char in _ARABIC_FORM_CHARS)
    except Exception:
        supported = False
    _font_support_cache[path] = supported
    return supported


def resolve_font_family(preferred: str | None = None) -> tuple[str, str]:
    """Return ``(regular_path, bold_path)`` for an installed Arabic-capable font family.

    ``preferred`` may be an explicit path to a regular font file; its bold sibling is used
    when discoverable, otherwise the regular face is used for both.
    """
    if preferred:
        if not os.path.exists(preferred):
            raise FontUnavailableError(f"font not found: {preferred}")
        directory, name = os.path.split(preferred)
        for regular, bold in ARABIC_FONT_CANDIDATES:
            if name.lower() == regular.lower():
                bold_path = os.path.join(directory, bold)
                return preferred, (bold_path if os.path.exists(bold_path) else preferred)
        return preferred, preferred

    for regular, bold in ARABIC_FONT_CANDIDATES:
        for directory in _FONT_DIRECTORIES:
            if not directory:
                continue
            regular_path = os.path.join(directory, regular)
            if not os.path.exists(regular_path) or not _font_supports_arabic(regular_path):
                continue
            bold_path = os.path.join(directory, bold)
            return regular_path, (bold_path if os.path.exists(bold_path) else regular_path)
    raise FontUnavailableError(
        "no installed Arabic-capable system font found; install one (e.g. Dubai, "
        "Segoe UI, Tahoma or Noto Naskh Arabic). Font files are never bundled here.")


# --------------------------------------------------------------------------- #
# 3. Palette — the approved dark terminal theme, as RGB
# --------------------------------------------------------------------------- #

PALETTE = {
    "bg": (11, 18, 32),
    "bg_2": (14, 23, 41),
    "surface": (19, 28, 48),
    "surface_2": (23, 34, 59),
    "border": (34, 48, 73),
    "text": (230, 237, 247),
    "muted": (142, 161, 189),
    "green": (52, 211, 153),
    "amber": (251, 191, 36),
    "red": (248, 113, 113),
    "blue": (96, 165, 250),
    "gray": (148, 163, 184),
}
_TONES = ("green", "amber", "red", "blue", "gray")


def _tone_colour(tone: str):
    return PALETTE.get(tone if tone in _TONES else "gray")


def _tint(colour, alpha: float):
    """Blend ``colour`` toward the card background — a flat stand-in for translucency."""
    background = PALETTE["bg"]
    return tuple(int(background[i] + (colour[i] - background[i]) * alpha) for i in range(3))


# --------------------------------------------------------------------------- #
# 4. Chart data — already-computed numbers only
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CardChartData:
    """Numbers for the compact card chart. Every value is copied from evidence as-is.

    The renderer positions these on a session range bar; it performs no analysis. The only
    arithmetic it does is the linear pixel mapping needed to place a value on an axis.
    """
    low: float | None = None
    high: float | None = None
    open: float | None = None
    close: float | None = None
    previous_close: float | None = None
    last: float | None = None
    support: float | None = None
    resistance: float | None = None
    trigger: float | None = None
    target: float | None = None
    stop: float | None = None

    def has_axis(self) -> bool:
        """True when a low/high axis with a positive span is available to plot on."""
        return (self.low is not None and self.high is not None
                and float(self.high) > float(self.low))


# --------------------------------------------------------------------------- #
# 5. Rendering
# --------------------------------------------------------------------------- #

@dataclass
class _Fonts:
    regular_path: str
    bold_path: str
    _cache: dict = field(default_factory=dict)

    def get(self, size: int, bold: bool = False):
        key = (size, bold)
        if key not in self._cache:
            path = self.bold_path if bold else self.regular_path
            self._cache[key] = ImageFont.truetype(path, size)
        return self._cache[key]


class _Canvas:
    """Small drawing helper: RTL-aware text, pills, rules and rounded panels."""

    def __init__(self, image: Image.Image, fonts: _Fonts):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.fonts = fonts
        self.width, self.height = image.size

    # --- text ---
    def width_of(self, text: str, size: int, bold: bool = False) -> float:
        return self.draw.textlength(display_text(text), font=self.fonts.get(size, bold))

    def text_rtl(self, right_x: int, y: int, text: str, size: int, colour, bold=False):
        """Draw right-aligned RTL text; returns the drawn width."""
        rendered = display_text(text)
        font = self.fonts.get(size, bold)
        self.draw.text((right_x, y), rendered, font=font, fill=colour, anchor="ra")
        return self.draw.textlength(rendered, font=font)

    def text_ltr(self, left_x: int, y: int, text: str, size: int, colour, bold=False):
        rendered = display_text(text, base_rtl=False)
        font = self.fonts.get(size, bold)
        self.draw.text((left_x, y), rendered, font=font, fill=colour, anchor="la")
        return self.draw.textlength(rendered, font=font)

    def text_centre(self, centre_x: int, y: int, text: str, size: int, colour, bold=False):
        rendered = display_text(text)
        self.draw.text((centre_x, y), rendered, font=self.fonts.get(size, bold),
                       fill=colour, anchor="ma")

    def fit_rtl(self, text: str, size: int, max_width: int) -> str:
        """Truncate a single line to ``max_width``, ellipsising rather than overflowing."""
        text = str(text)
        if self.width_of(text, size) <= max_width:
            return text
        trimmed = text
        while trimmed and self.width_of(trimmed + "…", size) > max_width:
            trimmed = trimmed[:-1]
        return (trimmed.rstrip() + "…") if trimmed else ""

    def wrap_rtl(self, text: str, size: int, max_width: int, max_lines: int) -> list[str]:
        """Greedy word wrap measured on the *rendered* form; returns logical lines."""
        words = str(text).split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and self.width_of(candidate, size) > max_width:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    break
            else:
                current = candidate
        if current and len(lines) < max_lines:
            lines.append(current)
        if lines and len(lines) == max_lines and current and lines[-1] != current:
            lines[-1] = lines[-1].rstrip() + "…"
        return lines

    # --- shapes ---
    def panel(self, box, fill=None, outline=None, radius=18, accent=None):
        self.draw.rounded_rectangle(box, radius=radius,
                                    fill=fill or PALETTE["surface"],
                                    outline=outline or PALETTE["border"], width=2)
        if accent:
            x0, y0, x1, y1 = box
            self.draw.rounded_rectangle((x1 - 6, y0 + 8, x1 - 2, y1 - 8), radius=3, fill=accent)

    def pill_size(self, text: str, size: int) -> tuple[int, int]:
        pad_x, pad_y = int(size * 0.7), int(size * 0.42)
        return (int(self.width_of(text, size, bold=True) + pad_x * 2), int(size + pad_y * 2))

    def pill_rtl(self, right_x: int, y: int, text: str, size: int, tone="gray",
                 solid=False) -> int:
        """Right-anchored rounded pill; returns its left edge x."""
        colour = _tone_colour(tone)
        pad_x, pad_y = int(size * 0.7), int(size * 0.42)
        box_width, box_height = self.pill_size(text, size)
        left = right_x - box_width
        fill = colour if solid else _tint(colour, 0.20)
        outline = colour if solid else _tint(colour, 0.55)
        self.draw.rounded_rectangle((left, y, right_x, y + box_height),
                                    radius=box_height // 2, fill=fill, outline=outline, width=2)
        self.draw.text((right_x - pad_x, y + pad_y), display_text(text),
                       font=self.fonts.get(size, True),
                       fill=PALETTE["bg"] if solid else colour, anchor="ra")
        return left

    def pill_at_left(self, left_x: int, y: int, text: str, size: int, tone="gray",
                     solid=False) -> int:
        """Left-anchored rounded pill; returns its right edge x."""
        box_width, _ = self.pill_size(text, size)
        self.pill_rtl(left_x + box_width, y, text, size, tone, solid)
        return left_x + box_width

    def rule(self, x0: int, x1: int, y: int, colour=None):
        self.draw.line((x0, y, x1, y), fill=colour or PALETTE["border"], width=2)


def _identity_mark(canvas: _Canvas, right_x: int, y: int, box: int):
    """An original EGX AI Trader mark: a rounded tile with three ascending bars."""
    left = right_x - box
    canvas.draw.rounded_rectangle((left, y, right_x, y + box), radius=int(box * 0.28),
                                  fill=_tint(PALETTE["blue"], 0.28),
                                  outline=PALETTE["blue"], width=2)
    unit = box / 9.0
    base_y = y + box - unit * 1.8
    for index, height_units in enumerate((2.0, 3.4, 4.8)):
        bar_left = left + unit * (1.7 + index * 2.1)
        top = base_y - unit * height_units
        colour = PALETTE["blue"] if index < 2 else PALETTE["green"]
        canvas.draw.rounded_rectangle((bar_left, top, bar_left + unit * 1.35, base_y),
                                      radius=int(unit * 0.45), fill=colour)


def _draw_range_chart(canvas: _Canvas, box, chart: CardChartData, currency: str):
    """Compact session-range chart: axis low→high with supplied markers plotted on it.

    Only supplied numbers are plotted. When no low/high axis exists the panel says so
    rather than drawing an invented scale.
    """
    x0, y0, x1, y1 = box
    canvas.panel(box)
    inner_right, inner_left = x1 - 34, x0 + 34
    centre_x = (x0 + x1) // 2

    if not chart.has_axis():
        canvas.text_centre(centre_x, y0 + (y1 - y0) // 2 - 16,
                           "لا يوجد نطاق جلسة متاح · Session Range Unavailable",
                           26, PALETTE["muted"])
        return

    canvas.text_centre(centre_x, y0 + 14, f"نطاق الجلسة والمستويات · {currency}", 21,
                       PALETTE["muted"])

    low, high = float(chart.low), float(chart.high)
    values = [value for value in (chart.support, chart.resistance, chart.trigger,
                                  chart.target, chart.stop, chart.previous_close,
                                  chart.last, chart.open, chart.close)
              if value is not None]
    axis_low = min([low] + [float(v) for v in values])
    axis_high = max([high] + [float(v) for v in values])
    span = axis_high - axis_low or 1.0
    pad = span * 0.06
    axis_low, axis_high = axis_low - pad, axis_high + pad
    span = axis_high - axis_low

    def position(value):
        # RTL axis: low on the right, high on the left.
        ratio = (float(value) - axis_low) / span
        return inner_right - ratio * (inner_right - inner_left)

    track_y = y0 + int((y1 - y0) * 0.60)
    canvas.draw.rounded_rectangle((inner_left, track_y - 7, inner_right, track_y + 7),
                                  radius=7, fill=_tint(PALETTE["blue"], 0.16),
                                  outline=PALETTE["border"], width=2)

    # session low→high band
    session_left, session_right = position(high), position(low)
    canvas.draw.rounded_rectangle((session_left, track_y - 7, session_right, track_y + 7),
                                  radius=7, fill=_tint(PALETTE["blue"], 0.46))

    # Reference levels as vertical ticks. Two levels can legitimately sit at the same
    # price (a resistance that is also the scenario trigger), so labels are staggered
    # instead of being drawn on top of each other.
    label_rows: list[tuple[float, int]] = []
    for value, tone, label in ((chart.stop, "red", "وقف"),
                               (chart.support, "green", "دعم"),
                               (chart.resistance, "amber", "مقاومة"),
                               (chart.trigger, "blue", "تفعيل"),
                               (chart.target, "green", "هدف")):
        if value is None:
            continue
        x = position(value)
        colour = _tone_colour(tone)
        row = 0
        while any(abs(x - taken) < 70 and row == taken_row
                  for taken, taken_row in label_rows):
            row += 1
        label_rows.append((x, row))
        tick = 24 + row * 22
        # Labels sit above the track so they can never collide with the axis readouts.
        canvas.draw.line((x, track_y - tick, x, track_y + 24), fill=colour, width=3)
        canvas.text_centre(int(x), track_y - tick - 28, label, 21, colour)

    # open / close / last markers
    if chart.open is not None:
        x = position(chart.open)
        canvas.draw.ellipse((x - 9, track_y - 9, x + 9, track_y + 9),
                            fill=PALETTE["bg"], outline=PALETTE["muted"], width=3)
    marker = chart.last if chart.last is not None else chart.close
    if marker is not None:
        x = position(marker)
        canvas.draw.ellipse((x - 13, track_y - 13, x + 13, track_y + 13),
                            fill=PALETTE["text"], outline=PALETTE["bg"], width=3)

    canvas.text_rtl(inner_right, track_y + 32, f"أدنى {_plain(chart.low)}", 22,
                    PALETTE["muted"])
    canvas.text_ltr(inner_left, track_y + 32, f"{_plain(chart.high)} أعلى", 22,
                    PALETTE["muted"])


def _plain(value) -> str:
    """Format a chart axis label. Display only — the value itself is never altered."""
    if value is None:
        return "—"
    number = float(value)
    return f"{number:.3f}" if abs(number) < 1 else f"{number:,.2f}"


def _tone_for_recommendation(label: str) -> str:
    text = str(label)
    if "تجنب" in text or "غير كافية" in text:
        return "red"
    if "جاهز" in text:
        return "green"
    if "قريب" in text:
        return "amber"
    if "مراقبة" in text:
        return "blue"
    return "gray"


def narrative_source_label(model: str | None) -> str:
    """Return the honest narrative provenance label printed on exported cards."""
    value = str(model or "").strip()
    if not value:
        return "Narrative  Unavailable"
    if "fallback" in value.lower():
        return "Narrative  Deterministic Fallback"
    return f"Narrative model  {value}"


def _footer_geometry(height: int, margin: int) -> dict:
    """Fixed bottom band: safety badges, disclaimer and provenance never move."""
    badges_y = height - margin - 46
    disclaimer_y = badges_y - 34
    evidence_y = disclaimer_y - 28
    quality_y = evidence_y - 28
    return {"badges_y": badges_y, "disclaimer_y": disclaimer_y,
            "evidence_y": evidence_y, "quality_y": quality_y,
            "rule_y": quality_y - 16, "top": quality_y - 16}


def render_card_png(payload, chart: CardChartData | None = None, *,
                    size: str = DEFAULT_CARD_SIZE, font_path: str | None = None,
                    currency: str = "EGP") -> bytes:
    """Render a :class:`CardPayload` (+ optional chart numbers) to PNG bytes.

    ``payload`` carries pre-formatted display strings only; this function lays them out and
    never re-derives a value. ``size`` is a key of :data:`CARD_SIZES` (``POST`` 1080×1350,
    ``STORY`` 1080×1920).

    Layout is measure-then-draw: every block's height is computed first, the leftover
    vertical space is shared between the blocks, and the safety footer is pinned to the
    bottom — so a long narrative can never overrun the "Production Disabled" badges.
    """
    if size not in CARD_SIZES:
        raise ValueError(f"unknown card size {size!r}; expected one of {sorted(CARD_SIZES)}")
    width, height = CARD_SIZES[size]
    chart = chart or CardChartData()

    regular, bold = resolve_font_family(font_path)
    fonts = _Fonts(regular, bold)
    image = Image.new("RGB", (width, height), PALETTE["bg"])
    canvas = _Canvas(image, fonts)

    margin = 48
    right, left = width - margin, margin
    content_width = right - left
    footer = _footer_geometry(height, margin)

    # A soft top glow so the card does not read as a flat slab.
    for row in range(260):
        canvas.draw.line((0, row, width, row),
                         fill=_tint(PALETTE["surface_2"], (1.0 - row / 260.0) * 0.9))

    # ----- measure --------------------------------------------------------- #
    price_rows = tuple(payload.price_rows)[:8]
    level_rows = tuple(payload.level_rows)[:6]
    scenario_rows = tuple(payload.scenario_rows)[:7]
    headline = str(payload.narrative_headline or "")
    summary = str(payload.narrative_summary or "")

    header_h = 110
    identity_h = 124
    price_row_h, side_row_h = 46, 40
    chart_h = 200 if size == "POST" else 250
    min_chart_h = 160
    min_gap = 16
    # The taller story format spreads its slack between blocks instead of leaving a void
    # at the bottom of the card.
    max_gap = 56 if size == "POST" else 112

    headline_lines = canvas.wrap_rtl(headline, 27, content_width - 60, 2) if headline else []
    max_summary_lines = 3 if size == "POST" else 6
    summary_lines = (canvas.wrap_rtl(summary, 25, content_width - 60, max_summary_lines)
                     if summary else [])

    def _price_h():
        return (((len(price_rows) + 1) // 2) * price_row_h + 24) if price_rows else 0

    def _side_h():
        return max([0]
                   + ([len(level_rows) * side_row_h + 66] if level_rows else [])
                   + ([len(scenario_rows) * side_row_h + 66] if scenario_rows else []))

    def _narrative_h():
        if not (headline_lines or summary_lines):
            return 0
        return 22 + len(headline_lines) * 38 + len(summary_lines) * 36 + 18

    def _heights():
        return (header_h, identity_h, _price_h(), chart_h, _side_h(), _narrative_h())

    available = footer["top"] - margin

    def _overflow():
        heights = [h for h in _heights() if h]
        return sum(heights) + min_gap * max(len(heights) - 1, 1) - available

    # Shed content in priority order until the card fits. The safety footer is pinned, so
    # this can only ever shorten optional copy — never the mandatory badges. Secondary
    # table rows go before narrative text, and the narrative keeps at least one line for
    # as long as anything else can give way.
    while _overflow() > 0 and len(scenario_rows) > 4:
        scenario_rows = scenario_rows[:-1]
    while _overflow() > 0 and len(level_rows) > 4:
        level_rows = level_rows[:-1]
    while _overflow() > 0 and len(summary_lines) > 2:
        summary_lines.pop()
    if _overflow() > 0 and chart_h > min_chart_h:
        chart_h = max(min_chart_h, chart_h - _overflow())
    while _overflow() > 0 and len(headline_lines) > 1:
        headline_lines.pop()
    while _overflow() > 0 and len(summary_lines) > 1:
        summary_lines.pop()
    while _overflow() > 0 and summary_lines:
        summary_lines.pop()

    price_h, side_h, narrative_h = _price_h(), _side_h(), _narrative_h()
    natural = sum(h for h in _heights() if h)
    gaps = max(sum(1 for h in _heights() if h) - 1, 1)
    gap = (max(min_gap, min(max_gap, (available - natural) // gaps))
           if available > natural else min_gap)

    cursor = margin

    # ----- header ---------------------------------------------------------- #
    _identity_mark(canvas, right, cursor, 74)
    canvas.text_rtl(right - 92, cursor + 4, "EGX AI Trader", 34, PALETTE["text"], bold=True)
    canvas.text_rtl(right - 92, cursor + 46, "تحليل سهم بالذكاء الاصطناعي", 23,
                    PALETTE["muted"])
    canvas.text_ltr(left, cursor + 8, "AI STOCK ANALYSIS", 22, PALETTE["blue"], bold=True)
    canvas.text_ltr(left, cursor + 42, str(payload.as_of_label), 21, PALETTE["muted"])
    canvas.rule(left, right, cursor + 96)
    cursor += header_h + gap - 18

    # ----- symbol + recommendation ------------------------------------------ #
    canvas.text_rtl(right, cursor, str(payload.symbol), 74, PALETTE["text"], bold=True)
    canvas.text_rtl(right, cursor + 88, canvas.fit_rtl(payload.title, 25, content_width - 40),
                    25, PALETTE["muted"])
    canvas.pill_at_left(left, cursor + 10, str(payload.recommendation_label), 27,
                        _tone_for_recommendation(payload.recommendation_label), solid=True)
    if payload.confidence_label:
        canvas.text_ltr(left, cursor + 72, f"Confidence  {payload.confidence_label}",
                        23, PALETTE["muted"])
    cursor += identity_h + gap

    # ----- price rows -------------------------------------------------------- #
    if price_rows:
        canvas.panel((left, cursor, right, cursor + price_h))
        column_width = content_width // 2
        for index, (label, value) in enumerate(price_rows):
            column, row = index % 2, index // 2
            cell_right = right - 26 - column * column_width
            y = cursor + 15 + row * price_row_h
            canvas.text_rtl(cell_right, y + 6, str(label), 23, PALETTE["muted"])
            canvas.text_ltr(cell_right - column_width + 34, y, str(value), 28,
                            PALETTE["text"], bold=True)
        cursor += price_h + gap

    # ----- compact chart ----------------------------------------------------- #
    _draw_range_chart(canvas, (left, cursor, right, cursor + chart_h), chart, currency)
    cursor += chart_h + gap

    # ----- key levels + scenario --------------------------------------------- #
    if side_h:
        gutter = 20
        half = (content_width - gutter) // 2
        block_top = cursor

        def _row_panel(panel_right, panel_left, rows, heading, accent):
            panel_h = len(rows) * side_row_h + 70
            canvas.panel((panel_left, block_top, panel_right, block_top + panel_h),
                         accent=accent)
            canvas.text_rtl(panel_right - 24, block_top + 16, heading, 24, PALETTE["text"],
                            bold=True)
            value_width = (panel_right - panel_left) // 2 - 24
            for index, (label, value) in enumerate(rows):
                y = block_top + 58 + index * side_row_h
                canvas.text_rtl(panel_right - 24, y, str(label), 22, PALETTE["muted"])
                canvas.text_ltr(panel_left + 24, y - 2,
                                canvas.fit_rtl(value, 24, value_width), 24,
                                PALETTE["text"], bold=True)

        if level_rows:
            _row_panel(right, right - half, level_rows, "المستويات · Key Levels",
                       PALETTE["blue"])
        if scenario_rows:
            _row_panel(left + half, left, scenario_rows, "السيناريو · Scenario",
                       PALETTE["green"])
        cursor += side_h + gap

    # ----- narrative ---------------------------------------------------------- #
    if narrative_h:
        canvas.panel((left, cursor, right, cursor + narrative_h),
                     fill=PALETTE["surface_2"], accent=PALETTE["amber"])
        text_y = cursor + 18
        for line in headline_lines:
            canvas.text_rtl(right - 26, text_y, line, 27, PALETTE["text"], bold=True)
            text_y += 38
        for line in summary_lines:
            canvas.text_rtl(right - 26, text_y, line, 25, PALETTE["muted"])
            text_y += 36

    # ----- provenance + mandatory safety badges -------------------------------- #
    canvas.rule(left, right, footer["rule_y"])
    if payload.data_quality_label:
        canvas.text_rtl(right, footer["quality_y"],
                        canvas.fit_rtl(payload.data_quality_label, 21, content_width),
                        21, PALETTE["muted"])
    provenance = narrative_source_label(payload.narrative_model)
    if payload.evidence_version:
        provenance = f"Evidence  {payload.evidence_version}  ·  {provenance}"
    if provenance:
        canvas.text_rtl(right, footer["evidence_y"],
                        canvas.fit_rtl(provenance, 19, content_width),
                        19, PALETTE["muted"])
    if payload.disclaimer:
        canvas.text_rtl(right, footer["disclaimer_y"],
                        canvas.fit_rtl(payload.disclaimer, 19, content_width), 19,
                        PALETTE["muted"])

    badge_right = right
    for label, badge_tone in (("Production Disabled · التنفيذ الحقيقي غير مفعّل", "red"),
                              ("Research / Paper Mode", "amber"),
                              ("Decision Support Only", "blue")):
        badge_right = canvas.pill_rtl(badge_right, footer["badges_y"], label, 20,
                                      badge_tone) - 12

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
