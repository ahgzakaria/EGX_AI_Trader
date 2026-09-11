import json
import os
from copy import deepcopy


SETTINGS_FILE = "config/settings.json"


DEFAULT_SETTINGS = {
    # Market-data routing changes sources only; trading calculations remain
    # frozen. Rubix is consumed only through the adapter's read-only SQLite.
    "scanner_provider": "rubix",
    "dashboard_provider": "rubix",
    "forward_testing_provider": "rubix",
    # Backtests read the frozen MubasherTrade PRO record (data/frozen_mubasher).
    # "yahoo" selects the archived snapshot, for reproducing findings measured on it.
    "backtest_provider": "frozen_mubasher",
    "fallback_provider": "yahoo",
    # The record the live scanner's daily history comes from. "eodhd" until
    # scripts/record_live_source_shadow.py has shown MubasherTrade PRO's record
    # ("mubasher") agreeing in practice; see core/mubasher_live_history.py.
    "live_history_source": "eodhd",
    "market_data": {
        "cache_path": "data/market_data_cache.sqlite",
        "cache_source_provider": "rubix",
        # Rubix adapter/database locations are supplied by environment or
        # deployment settings; no credentials or network endpoints live here.
        "rubix_db_path": "data/rubix_live_market.db",
        "rubix_quote_stale_seconds": 60,
        "rubix_bar_stale_seconds": 120,
        "rubix_subscription_batch_size": 100,
        # User-specific adapter/DB locations are supplied by environment or
        # settings at deployment time and are never hardcoded in source.
        "tickerchart_adapter_path": "",
        "tickerchart_db_path": "",
        "tickerchart_local_url": "",
        "tickerchart_stale_after_minutes": 1440
    },
    "data": {
        "history_period": "10y",
        "interval": "1d",
        "min_bars": 250
    },
    # Rubix Completed-Daily-Candle Bridge (shadow-first, disabled by default).
    # This never changes any trading strategy, indicator, or threshold. It only
    # controls a diagnostic/optional data layer that appends *validated,
    # completed* Rubix daily sessions newer than Yahoo. Production activation is
    # a separate, explicit, user-approved step; leave `enabled` false until the
    # capability audit and reconciliation reports justify it.
    # DATA-QUALITY gate, not a strategy threshold. It changes no indicator, no
    # score, no reward/risk requirement and no BUY/WATCH/AVOID rule, and no
    # symbol-level decision depends on it. It governs only whether MARKET-WIDE
    # aggregates (regime, breadth, "top market opportunity") may be stated at
    # all. Set against observed runs: healthy scans analyse ~194/241 (~80%),
    # while the 2026-08-04 partial-provider morning had 6/241 (2.5%) current.
    "minimum_daily_market_coverage_percent": 60.0,
    # DATA-QUALITY gate for the live quote overlay, not a strategy threshold.
    # It decides only whether a quote inside the permitted exchange session may
    # still be called live; it changes no indicator, score or decision rule.
    # 300s preserves the previous intraday behaviour (open_stale_after_minutes
    # was 5) while session and phase membership - which are new - do the work
    # that elapsed age was wrongly doing on its own.
    "rubix_live_quote_freshness_seconds": 300.0,
    "rubix_daily_bridge": {
        "enabled": False,
        "shadow_mode": True,
        "close_safety_minutes": 15,
        # Conservative gates: on the audited data these reject every current
        # Rubix session (sparse coverage, ~46% volume capture). Do not lower
        # without re-running RUBIX_DAILY_CAPABILITY_AUDIT.
        "minimum_coverage_ratio": 0.90,
        "minimum_volume_reliability": 0.90,
        "holidays": [],
        "early_closes": {}
    },
    # TradingView research provider (shadow-only, disabled by default). Yahoo
    # remains the production historical source. No automation of the
    # TradingView website is performed anywhere; data only enters via a
    # user-exported CSV directory or a user-configured official webhook.
    # See TRADINGVIEW_ACCESS_CAPABILITY_REPORT.md for the compliance basis.
    "tradingview": {
        "tradingview_provider_enabled": False,
        "tradingview_shadow_mode": True,
        "tradingview_method": "none",
        "tradingview_csv_directory": "",
        "tradingview_webhook_enabled": False,
        "tradingview_webhook_secret": "",
        "tradingview_completed_daily_bridge_enabled": False,
        "tradingview_prefer_only_when_newer": True,
        "tradingview_require_confirmed_bar": True
    },
    # Engineering-only retention and backup policy.  Automatic deletion is
    # deliberately disabled so research evidence remains reproducible.
    "reproducibility": {
        "archive_format": "npz+csv.gz",
        "automatic_dataset_deletion": False,
        "verify_on_replay": True
    },
    "operations": {
        "backup_root": "backups",
        "backup_retention_count": 10,
        "automatic_backup_pruning": False,
        "rubix_supervisor_max_restarts": 20
    },
    "strategy": {
        # These eleven settings were measured against each other on 2026-08-26
        # under the corrected cost model, and the values below won. The
        # alternative -- min_score 65, min_confidence 80, min_trend 25,
        # min_momentum 5, min_volume 5, both gates off, quality_min_adx 20,
        # exit_mode TARGET1, no partial exit, no overlapping trades -- returned
        # -36.23% at profit factor 0.88 and a 46.3% drawdown, against +60.08%,
        # 1.29 and 16.17% here. Three of ten years positive against seven.
        #
        # The higher thresholds filter on a score that carries no signal
        # (r = -0.032), so they discard trades without improving the survivors.
        # See docs/audits/strategies/CONFIG_RECONCILIATION.md.
        "min_score": 50,
        "min_confidence": 65,
        # 3.0 rather than 2.0. Swept at 1.5/2.0/2.5/3.0: risk per share falls
        # monotonically 12.90% -> 6.85%, worst losing streak 22 -> 13 trades and
        # maximum drawdown 58.89% -> 17.62%, because demanding a higher reward
        # ratio selects a tighter stop and a tighter stop loses less when hit.
        # Chosen as a risk control; its return figure is tail-driven and is not
        # the reason. See docs/audits/strategies/MIN_RR_AS_RISK_CONTROL.md.
        "min_rr": 3.0,
        "min_trend": 12,
        "min_momentum": 3,
        "min_volume": 0,
        "watch_score": 60,
        "watch_confidence": 65,
        "market_trend_adx": 25,
        "market_weak_trend_adx": 18,
        "market_index_fast_ema": 50,
        "market_index_slow_ema": 200,

        "max_rr": 100.0,
        "require_candle_confirmation": False,
        "require_market_analyzer": True,

        "require_quality_filter": True,
        "quality_min_adx": 21,
        "quality_min_volume_ratio": 1.0,
        "quality_min_atr_percent": 1.5,
        "quality_min_resistance_room": 3.0
    },

    "backtest": {
        "entry_wait_days": 5,
        "ai_mode": "STRATEGY_ONLY",
        "walk_forward_splits": 5,
        "exit_mode": "TARGET2",
        "max_holding_days": 20,
        "move_to_breakeven": True,
        "partial_exit": True,
        "partial_percent": 0.50,

        "risk_mode": "FIXED",
        "risk_percent": 2,

        "trailing_mode": "EMA20",
        "trailing_atr": 2,
        # At min_rr 1.5 disabling this barely mattered. At 3.0 it is the
        # difference between +40.36% and -32.52%: without it, positions run to
        # the holding cap at 17.09 days instead of 4.52 and bleed against the
        # tight stops that a high reward ratio selects. The effect does not
        # transfer between configurations, which is why it is measured here
        # alongside min_rr rather than inherited from an earlier sweep.
        "trailing_enabled": True,

        "allow_overlapping_trades": True,

        "max_open_positions": 10,
        "max_portfolio_risk_percent": 10,

        "initial_capital": 100000,

        # 0.003 per side was a placeholder, 65% above what the broker actually
        # charges. The contract note in `scalping.fee_schedule` (notional
        # 102,560 EGP) totals 0.1819% per side. The breakout package corrected
        # this already; see tests/test_breakout_weights_are_measured.py. The
        # classic backtest was left behind.
        "commission": 0.001819,
        "slippage": 0.0005,
        # The spread used to cost nothing here. Measured from live quotes across
        # the 170 of 172 symbols the backtest actually traded that have them,
        # the trade-weighted median is 0.500%, charged once per round trip.
        # Together with the commission correction this moves a round trip from
        # 0.703% to 0.964% -- the two errors had been cancelling.
        "spread_percent": 0.5,
        # Universe filter, off by default. When set, the backtest trades only
        # symbols quoting at or inside this spread. Costs are already charged
        # per symbol, so this is a decision about which names to trade at all
        # rather than about how to price them. None means no filter.
        "max_spread_percent": None,
        # Symbols with too few quotes to measure are charged this rather than
        # the universe median. They quote thinner than the thinnest measured
        # name, and thin quoting predicts a wide spread (correlation -0.445;
        # least-quoted quartile 0.652% median against 0.188% for the most). This
        # is the 90th percentile of the measured distribution -- conservative,
        # because the alternative is flattering something nobody has observed.
        "unmeasured_spread_percent": 0.927
    },

    # Additive advisory scoring only. It never replaces the frozen strategy
    # Score, Signal, AI ranking, entry, exit, risk, or portfolio calculations.
    "decision_support": {
        "enabled": True,
        "rvol_lookback": 20,
        "include_ai_probability": True,
        "max_spread_percent": 0.5,
        "minimum_turnover": 1000000.0,
        "minimum_volume": 100000.0,
        "atr_target_percent": 2.0,
        "sector_file": "data/sectors.csv",
        "database_path": "data/decision_support.db",
        "edge_alert_threshold": 9.0,
        "edge_weights": {
            "trend_quality": 1.0, "momentum": 0.8,
            "relative_volume": 0.9, "liquidity": 1.0,
            "spread": 0.9, "bid_ask_balance": 0.4,
            "atr_feasibility": 0.7, "resistance_room": 0.7,
            "support_quality": 0.5, "market_strength": 0.8,
            "sector_strength": 0.5, "setup_quality": 0.8,
            "rubix_freshness": 0.9, "historical_performance": 0.5,
            "volatility": 0.5, "ai_probability": 0.6,
        },
    },

    # Fully isolated paper-only intraday module. These values never feed the
    # frozen daily strategy, AI, ranking, portfolio, or backtest.
    "scalping": {
        "enabled": False,
        "mode": "PAPER_ONLY",
        "take_profit_percent": 2.0,
        "stop_loss_percent": 2.0,
        "require_rubix_fresh": True,
        "allow_yahoo_actionable": False,
        "close_at_session_end": True,
        "entry_cutoff": "14:10",
        "forced_exit_time": "14:25",
        "quote_max_age_seconds": 60,
        "commission": 0.003,
        "slippage": 0.0005,
        "initial_capital": 100000.0,
        "risk_per_trade_percent": 0.5,
        "max_open_positions": 3,
        "max_daily_loss_percent": 2.0,
        "max_trades_per_day": 8,
        "max_consecutive_losses": 3,
        "max_exposure_per_symbol_percent": 20.0,
        "max_spread_percent": 0.5,
        "minimum_liquidity": 100000.0,
        "minimum_relative_volume": 1.0,
        "portfolio_heat_percent": 2.0,
        "revenge_cooldown_minutes": 30,
        "opening_range_minutes": 15,
        "momentum_lookback_bars": 5,
        "breakout_lookback_bars": 20,
        "minimum_momentum_percent": 0.3,
        "database_path": "data/scalping.db",
        "tick_size_bands": [
            {"max_price": 2.0, "tick_size": 0.001},
            {"max_price": None, "tick_size": 0.01},
        ],
    },
    "ai": {
        "enabled": False,
        "min_probability": 70
    },

    # Phase 4 policy values are deliberately configuration, not optimisation.
    # Their initial bands follow the requested illustrative values exactly.
    "ai_risk_overlay": {
        "position_sizing": {
            "bands": [
                {"min_probability": 75, "multiplier": 1.00},
                {"min_probability": 60, "multiplier": 0.75},
                {"min_probability": 45, "multiplier": 0.50}
            ],
            "below_band_action": "size",
            "below_band_multiplier": 0.25
        },
        "ranking": {
            "weights": {
                "strategy_quality": 0.40,
                "ai_probability": 0.35,
                "risk_reward": 0.15,
                "confidence": 0.05,
                "market_regime": 0.05
            },
            "rr_cap": 5.0,
            "regime_scores": {
                "BULL": 1.0,
                "SIDEWAYS": 0.5,
                "BEAR": 0.0
            }
        },
        "hybrid": {
            "emergency_min_probability": 20
        }
    }
}


def _merge_defaults(defaults, values):
    """Preserve user values while safely adding newly introduced settings."""
    merged = deepcopy(defaults)
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


class SettingsManager:

    def __init__(self):

        os.makedirs("config", exist_ok=True)

        if not os.path.exists(SETTINGS_FILE):

            self.save(DEFAULT_SETTINGS)

        self.data = self.load()

    # ==================================
    # Load
    # ==================================

    def load(self):

        with open(

            SETTINGS_FILE,

            "r",

            encoding="utf-8"

        ) as f:

            loaded = json.load(f)

        if not isinstance(loaded, dict):
            raise ValueError("Settings file must contain a JSON object")

        return _merge_defaults(DEFAULT_SETTINGS, loaded)

    # ==================================
    # Save
    # ==================================

    def save(self, data):

        with open(

            SETTINGS_FILE,

            "w",

            encoding="utf-8"

        ) as f:

            json.dump(

                data,

                f,

                indent=4

            )

        self.data = data

    # ==================================
    # Reload
    # ==================================

    def reload(self):

        self.data = self.load()

    # ==================================
    # Reset
    # ==================================

    def reset(self):

        self.save(

            deepcopy(DEFAULT_SETTINGS)

        )

    # ==================================
    # Get Section
    # ==================================

    def get(self, section):

        return self.data.get(section, {})

    # ==================================
    # Set Section
    # ==================================

    def set(self, section, values):

        self.data[section] = values

        self.save(self.data)


settings = SettingsManager()
