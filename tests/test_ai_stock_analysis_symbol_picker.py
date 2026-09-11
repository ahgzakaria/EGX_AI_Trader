"""Full-universe searchable picker tests for AI Stock Analysis.

The picker consumes local approved symbol metadata only. These tests deliberately
avoid the analysis service, providers, indicators, scanner, and history store
unless a valid symbol and explicit button press are simulated.
"""

from pathlib import Path

import pandas as pd
import pytest

from core.symbols import (
    SYMBOL_SOURCE,
    ApprovedSymbol,
    load_approved_symbol_options,
    load_symbols,
    resolve_approved_symbol,
    search_approved_symbol_options,
)


#: The authoritative EODHD universe replaced the retired 265-symbol list.
SYMBOLS_PATH = SYMBOL_SOURCE
_NO_INPUT = object()


@pytest.fixture(scope="module")
def options():
    return load_approved_symbol_options(SYMBOLS_PATH)


def _tickers(matches):
    return [option.ticker for option in matches]


def test_full_approved_universe_is_searchable_and_not_limited_to_ten(options):
    approved = load_symbols(SYMBOLS_PATH)
    assert len(approved) == 236      # 241 EODHD codes less 5 registered aliases
    assert len(options) == len({_normalized(value) for value in approved})
    assert len(options) > 10
    assert {"AALR", "COMI", "MPCO", "SWDY", "TMGH"} <= set(_tickers(options))


def test_exact_ticker_match_is_first(options):
    matches = search_approved_symbol_options(options, "COMI")
    assert matches[0].ticker == "COMI"


def test_lowercase_and_ca_tolerant_ticker_search(options):
    assert search_approved_symbol_options(options, "comi")[0].ticker == "COMI"
    assert search_approved_symbol_options(options, "comi.ca")[0].ticker == "COMI"


def test_ticker_substring_search(options):
    assert "COMI" in _tickers(search_approved_symbol_options(options, "OM"))


def test_english_company_name_search(options):
    matches = search_approved_symbol_options(options, "international bank")
    assert matches[0].ticker == "COMI"


def test_company_name_search_uses_the_authoritative_eodhd_name(options):
    matches = search_approved_symbol_options(options, "mansourah poultry")
    assert matches[0].ticker == "MPCO"
    assert matches[0].display_label == "MPCO — Mansourah Poultry"


def test_duplicate_ca_aliases_are_normalized_into_one_option(tmp_path):
    source = tmp_path / "symbols.csv"
    pd.DataFrame(
        [
            {"Ticker": "COMI", "EnglishName": "", "ArabicName": ""},
            {
                "Ticker": "COMI.CA",
                "EnglishName": "Commercial International Bank",
                "ArabicName": "البنك التجاري الدولي",
            },
            {"Ticker": "SWDY.CA", "EnglishName": "Elsewedy Electric"},
        ]
    ).to_csv(source, index=False)

    loaded = load_approved_symbol_options(source)
    assert _tickers(loaded) == ["COMI", "SWDY"]
    assert loaded[0].source_symbol == "COMI"
    assert loaded[0].english_name == "Commercial International Bank"
    assert loaded[0].arabic_name == "البنك التجاري الدولي"


def test_ranking_contract_is_deterministic():
    candidates = (
        ApprovedSymbol("XCOM", "XCOM.CA", english_name="Other"),
        ApprovedSymbol("COMI", "COMI.CA", english_name="Commercial Bank"),
        ApprovedSymbol("ACOM", "ACOM.CA", english_name="Another"),
        ApprovedSymbol("BANK", "BANK.CA", english_name="COM Holdings"),
        ApprovedSymbol("NAME", "NAME.CA", english_name="Alpha COM Group"),
    )
    ranked = search_approved_symbol_options(candidates, "COM", limit=None)
    assert _tickers(ranked) == ["COMI", "ACOM", "XCOM", "BANK", "NAME"]
    assert search_approved_symbol_options(candidates, "COMI")[0].ticker == "COMI"


@pytest.mark.parametrize("invalid", ["ZZZZ-NOT-APPROVED", "unknown company", None, ""])
def test_invalid_or_empty_input_is_rejected(options, invalid):
    assert resolve_approved_symbol(invalid, options) is None


@pytest.mark.parametrize("valid", ["COMI", "comi", "COMI.CA", "comi.ca"])
def test_valid_ticker_text_resolves_to_canonical_symbol(options, valid):
    assert resolve_approved_symbol(valid, options).ticker == "COMI"


def test_display_label_is_ticker_then_full_company_name(options):
    comi = resolve_approved_symbol("COMI", options)
    assert comi.display_label == (
        "COMI — Commercial International Bank-Egypt (CIB)"
    )
    # The selected VALUE stays the canonical ticker, so strategy code is unaffected.
    assert comi.ticker == "COMI"


def test_empty_query_can_browse_complete_universe(options):
    matches = search_approved_symbol_options(options, "", limit=None)
    assert len(matches) == len(options)
    assert _tickers(matches) == sorted(_tickers(matches))


def test_display_limit_never_hides_exact_match(options):
    matches = search_approved_symbol_options(options, "TMGH", limit=1)
    assert _tickers(matches) == ["TMGH"]


def test_no_provider_or_analysis_call_while_typing(monkeypatch, options):
    import dashboard.ai_stock_analysis as page

    fake = _PickerStreamlit(next_value="CO", button_returns=False)
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "section_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(page, "load_approved_symbol_options", lambda: options)
    monkeypatch.setattr(
        page,
        "run_analysis",
        lambda *args, **kwargs: pytest.fail("analysis ran while typing"),
    )

    symbol, pressed = page._symbol_selector()
    assert symbol is None
    assert pressed is False
    assert fake.button_disabled is True
    assert fake.option_count == len(options)


def test_exactly_one_selected_symbol_reaches_runner_after_button(
    monkeypatch, options
):
    import dashboard.ai_stock_analysis as page

    fake = _PickerStreamlit(next_value=_select("MPCO"), button_returns=True)
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "section_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(page, "load_approved_symbol_options", lambda: options)

    symbol, pressed = page._symbol_selector()
    calls = []
    if pressed:
        page.run_analysis(symbol, runner=lambda value: calls.append(value) or object())

    assert symbol == "MPCO"
    assert pressed is True
    assert calls == ["MPCO"]


def test_selected_symbol_survives_normal_streamlit_rerun(
    monkeypatch, options
):
    import dashboard.ai_stock_analysis as page

    fake = _PickerStreamlit(next_value=_select("COMI"), button_returns=False)
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "section_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(page, "load_approved_symbol_options", lambda: options)

    first, _ = page._symbol_selector()
    fake.next_value = _NO_INPUT
    second, _ = page._symbol_selector()

    assert first == second == "COMI"
    assert fake.session_state[page.STATE_SELECTED] == "COMI"


def test_changing_search_text_does_not_rerun_previous_analysis(
    monkeypatch, options
):
    import dashboard.ai_stock_analysis as page

    fake = _PickerStreamlit(next_value="different query", button_returns=False)
    fake.session_state[page.STATE_BUNDLE] = "existing-analysis"
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "section_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(page, "load_approved_symbol_options", lambda: options)
    monkeypatch.setattr(
        page,
        "run_analysis",
        lambda *args, **kwargs: pytest.fail("previous analysis reran"),
    )

    symbol, pressed = page._symbol_selector()
    assert (symbol, pressed) == (None, False)
    assert fake.session_state[page.STATE_BUNDLE] == "existing-analysis"


def test_empty_state_and_disabled_analyze_button(monkeypatch, options):
    import dashboard.ai_stock_analysis as page

    fake = _PickerStreamlit(next_value=None, button_returns=True)
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "section_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(page, "load_approved_symbol_options", lambda: options)

    symbol, pressed = page._symbol_selector()
    assert symbol is None
    assert pressed is False
    assert fake.button_disabled is True
    assert fake.placeholder == "اكتب رمز السهم أو اسم الشركة"
    assert fake.index is None


def test_invalid_new_option_shows_bilingual_no_match_message(
    monkeypatch, options
):
    import dashboard.ai_stock_analysis as page

    fake = _PickerStreamlit(next_value="definitely invalid", button_returns=True)
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "section_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(page, "load_approved_symbol_options", lambda: options)

    symbol, pressed = page._symbol_selector()
    assert (symbol, pressed) == (None, False)
    assert any(
        "لم يتم العثور على سهم مطابق" in message
        and "No matching symbol found" in message
        for message in fake.messages
    )


def _normalized(value):
    text = str(value).strip().upper()
    return text[:-3] if text.endswith(".CA") else text


def _select(ticker):
    def choose(options):
        return next(option for option in options if option.ticker == ticker)

    return choose


class _Column:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _PickerStreamlit:
    """Small stateful fake limited to the picker boundary."""

    def __init__(self, *, next_value=_NO_INPUT, button_returns=False):
        self.session_state = {}
        self.next_value = next_value
        self.button_returns = button_returns
        self.button_disabled = None
        self.placeholder = None
        self.index = "unset"
        self.option_count = 0
        self.messages = []

    def columns(self, spec):
        return [_Column() for _ in range(len(spec))]

    def selectbox(self, label, options, index=0, key=None, **kwargs):
        values = list(options)
        self.option_count = len(values)
        self.placeholder = kwargs.get("placeholder")
        self.index = index
        if self.next_value is _NO_INPUT:
            return self.session_state.get(key)
        value = self.next_value(values) if callable(self.next_value) else self.next_value
        self.session_state[key] = value
        self.next_value = _NO_INPUT
        return value

    def button(self, label, disabled=False, **kwargs):
        self.button_disabled = disabled
        return bool(self.button_returns and not disabled)

    def markdown(self, *args, **kwargs):
        return None

    def warning(self, message, **kwargs):
        self.messages.append(str(message))
