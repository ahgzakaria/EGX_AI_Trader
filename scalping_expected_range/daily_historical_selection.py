"""EODHD-daily baseline selection for the Expected Range scalping workflow.

This module is deliberately pre-session and historical-only. It computes robust
full-session daily metrics from completed EODHD OHLCV through a supplied cutoff,
builds an immutable ranked snapshot, and exposes a separate Rubix-intraday
enrichment readiness state.

Daily OHLC cannot reveal High/Low ordering or isolate the EGX closing auction.
Nothing here claims first-touch, continuous-session-only, or auction-adjusted
historical evidence. No live quote, spread, momentum, RVOL, breakout, or current
session input is accepted by the scoring API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import math
from typing import Mapping

import numpy as np
import pandas as pd

from scalping_expected_range.config import DailyHistoricalSelectionConfig


EODHD_DAILY = "EODHD_DAILY"
DAILY_SELECTION_READY = "DAILY_HISTORICAL_SELECTION_READY"
DAILY_SELECTION_INSUFFICIENT = "HISTORICAL_DAILY_DATA_INSUFFICIENT"
DAILY_SELECTION_UNAVAILABLE = "HISTORICAL_DAILY_DATA_UNAVAILABLE"
DAILY_SELECTION_STALE = "HISTORICAL_DAILY_DATA_STALE"
VOLUME_HISTORY_READY = "VOLUME_HISTORY_READY"
VOLUME_HISTORY_UNRESOLVED = "VOLUME_HISTORY_UNRESOLVED"
VOLUME_HISTORY_UNAVAILABLE = "VOLUME_HISTORY_UNAVAILABLE"
# Backward-compatible name for callers of the checkpoint module.
DAILY_LIQUIDITY_UNAVAILABLE = VOLUME_HISTORY_UNRESOLVED

INTRADAY_ENRICHMENT_READY = "INTRADAY_HISTORICAL_ENRICHMENT_READY"
INTRADAY_ENRICHMENT_NOT_READY = "INTRADAY_HISTORICAL_ENRICHMENT_NOT_READY"

DAILY_AUCTION_DISCLOSURE = (
    "Daily historical metrics may include official closing-auction effects. "
    "Continuous-session-only historical enrichment is not yet available."
)
DAILY_PATH_DISCLOSURE = (
    "Daily OHLC does not reveal whether the session high or low occurred first; "
    "no first-touch target/stop claim is made."
)

UNAVAILABLE_INTRADAY_METRICS = (
    "first-touch target/stop statistics",
    "time-of-day consistency",
    "continuous-session-only historical range",
    "auction-adjusted historical metrics",
    "intraday path confidence",
)


@dataclass(frozen=True)
class DailyReadiness:
    status: str
    valid_sessions: int
    minimum_sessions: int
    preferred_sessions: int
    preferred_ready: bool
    reason: str | None = None


@dataclass(frozen=True)
class IntradayEnrichmentReadiness:
    status: str
    valid_sessions: int
    required_sessions: int
    unavailable_metrics: tuple[str, ...]


@dataclass(frozen=True)
class EODHDDailyLoadResult:
    symbol: str
    status: str
    frame: pd.DataFrame | None
    detail: str | None
    source_provider: str = EODHD_DAILY


@dataclass(frozen=True)
class DailyHistoricalMetrics:
    valid_session_count: int
    mean_daily_range_percent: float
    median_daily_range_percent: float
    range_p25_percent: float
    range_p75_percent: float
    range_iqr_percent: float
    range_mad_percent: float
    range_p90_percent: float
    maximum_daily_range_percent: float
    range_hit_1pct_frequency: float
    range_hit_1_5pct_frequency: float
    range_hit_2pct_frequency: float
    range_hit_2_5pct_frequency: float
    range_hit_3pct_frequency: float
    median_upper_excursion_percent: float
    median_lower_excursion_percent: float
    upper_excursion_mad_percent: float
    lower_excursion_mad_percent: float
    outlier_session_count: int
    outlier_session_rate: float
    mean_median_divergence_percent: float
    mean_median_divergence_ratio: float
    inside_normal_range_band_frequency: float
    median_volume: float
    mean_volume: float
    median_turnover_egp: float
    mean_turnover_egp: float
    volume_consistency: float
    zero_volume_session_count: int
    abnormal_gap_session_count: int
    abnormal_gap_session_rate: float
    maximum_absolute_open_gap_percent: float
    daily_movement_potential_score: float
    daily_range_stability_score: float
    daily_volatility_zone_consistency_score: float
    daily_liquidity_score: float | None


@dataclass(frozen=True)
class DailySelectionResult:
    symbol: str
    source_provider: str
    interval: str
    raw_adjusted_mode: str
    metric_version: str
    config_version: str
    data_cutoff: str
    latest_session: str | None
    source_data_fingerprint: str | None
    readiness: DailyReadiness
    metrics: DailyHistoricalMetrics | None
    volume_history_status: str
    historical_scalping_potential: float | None
    historical_rank: int | None
    eligible: bool
    eligibility_reasons: tuple[str, ...]
    overlap_validation_status: str
    auction_disclosure: str = DAILY_AUCTION_DISCLOSURE
    path_disclosure: str = DAILY_PATH_DISCLOSURE
    first_touch_available: bool = False

    def as_dict(self) -> dict:
        row = asdict(self)
        row["eligibility_reasons"] = list(self.eligibility_reasons)
        return row


@dataclass(frozen=True)
class FrozenDailyWatchlist:
    source_provider: str
    metric_version: str
    config_version: str
    data_cutoff: str
    generated_at: str
    snapshot_id: str
    results: tuple[DailySelectionResult, ...]
    candidate_symbols: tuple[str, ...]
    auction_disclosure: str = DAILY_AUCTION_DISCLOSURE

    def as_dict(self) -> dict:
        return {
            "source_provider": self.source_provider,
            "metric_version": self.metric_version,
            "config_version": self.config_version,
            "data_cutoff": self.data_cutoff,
            "generated_at": self.generated_at,
            "snapshot_id": self.snapshot_id,
            "candidate_symbols": list(self.candidate_symbols),
            "auction_disclosure": self.auction_disclosure,
            "results": [result.as_dict() for result in self.results],
        }


def assess_daily_readiness(
    valid_sessions: int,
    *,
    source_available: bool = True,
    config: DailyHistoricalSelectionConfig | None = None,
) -> DailyReadiness:
    cfg = config or DailyHistoricalSelectionConfig()
    count = max(0, int(valid_sessions))
    if not source_available:
        return DailyReadiness(
            DAILY_SELECTION_UNAVAILABLE,
            count,
            cfg.minimum_valid_sessions,
            cfg.preferred_valid_sessions,
            False,
            "EODHD daily history is unavailable; no fallback is permitted",
        )
    if count < cfg.minimum_valid_sessions:
        return DailyReadiness(
            DAILY_SELECTION_INSUFFICIENT,
            count,
            cfg.minimum_valid_sessions,
            cfg.preferred_valid_sessions,
            False,
            f"{count} valid completed sessions < {cfg.minimum_valid_sessions}",
        )
    return DailyReadiness(
        DAILY_SELECTION_READY,
        count,
        cfg.minimum_valid_sessions,
        cfg.preferred_valid_sessions,
        count >= cfg.preferred_valid_sessions,
        None,
    )


def assess_intraday_enrichment(
    valid_sessions: int,
    *,
    config: DailyHistoricalSelectionConfig | None = None,
) -> IntradayEnrichmentReadiness:
    cfg = config or DailyHistoricalSelectionConfig()
    count = max(0, int(valid_sessions))
    ready = count >= cfg.intraday_enrichment_minimum_sessions
    return IntradayEnrichmentReadiness(
        INTRADAY_ENRICHMENT_READY if ready else INTRADAY_ENRICHMENT_NOT_READY,
        count,
        cfg.intraday_enrichment_minimum_sessions,
        () if ready else UNAVAILABLE_INTRADAY_METRICS,
    )


def load_eodhd_daily_history(
    symbol: str,
    *,
    client=None,
    data_cutoff: date | str | None = None,
    config: DailyHistoricalSelectionConfig | None = None,
) -> EODHDDailyLoadResult:
    """Load corporate-action-aware EODHD daily history with no fallback.

    A full cached EOD response is requested so explicit D-1 filtering remains in
    :func:`analyze_daily_history`. Prices use the repository's existing split
    adjustment; volume uses its event-specific reconciliation policy. An
    unresolved event inside the configured 60-session window is disclosed in
    frame metadata and later blocks only scoring that requires liquidity.
    """

    cfg = config or DailyHistoricalSelectionConfig()
    base = str(symbol).strip().upper().split(".")[0]
    cutoff = _to_date(data_cutoff) if data_cutoff is not None else None
    try:
        if client is None:
            from providers.eodhd_client import EODHDClient

            client = EODHDClient()
        raw = client.eod(f"{base}.EGX", order="a", cache_ttl_seconds=6 * 3600)
        splits = client.get_json(
            f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400
        )
    except Exception as error:
        return EODHDDailyLoadResult(
            base,
            DAILY_SELECTION_UNAVAILABLE,
            None,
            f"{type(error).__name__}: {error}",
        )

    rows = [
        row
        for row in (raw or [])
        if isinstance(row, dict)
        and all(row.get(key) is not None for key in ("date", "open", "high", "low", "close"))
        and (cutoff is None or _to_date(row["date"]) <= cutoff)
    ]
    if not rows:
        return EODHDDailyLoadResult(
            base,
            DAILY_SELECTION_UNAVAILABLE,
            None,
            "EODHD returned no daily OHLC rows",
        )

    from providers.eodhd_adjustment import adjust
    from providers.eodhd_volume_adjustment import resolve_operational_volume

    applicable_splits = [
        event
        for event in (splits or [])
        if cutoff is None
        or (
            isinstance(event, dict)
            and event.get("date") is not None
            and _to_date(event["date"]) <= cutoff
        )
    ]

    source = pd.DataFrame(
        {
            "Date": pd.to_datetime([row["date"] for row in rows], errors="coerce"),
            "Open": [row["open"] for row in rows],
            "High": [row["high"] for row in rows],
            "Low": [row["low"] for row in rows],
            "Close": [row["close"] for row in rows],
            "Volume": [row.get("volume", 0) for row in rows],
        }
    )
    try:
        adjusted = adjust(source, applicable_splits)
        output = adjusted.frame
        served_volume, volume_meta = resolve_operational_volume(
            base,
            output["Date"],
            output["Raw Volume"],
            applicable_splits,
            lookback_sessions=cfg.lookback_sessions,
        )
    except Exception as error:
        return EODHDDailyLoadResult(
            base,
            DAILY_SELECTION_UNAVAILABLE,
            None,
            f"corporate-action normalization failed: {type(error).__name__}: {error}",
        )

    frame = output.set_index(pd.to_datetime(output["Date"]))[
        ["Open", "High", "Low", "Close"]
    ].copy()
    frame["Volume"] = served_volume.to_numpy()
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "effective_provider": "eodhd",
        "source_provider": cfg.source_provider,
        "provider_symbol": f"{base}.EGX",
        "interval": "1d",
        "price_series": "SPLIT_ADJUSTED",
        "raw_adjusted_mode": cfg.raw_adjusted_mode,
        "normalization_data_cutoff": pd.Timestamp(output["Date"].max())
        .date()
        .isoformat(),
        "fallback_used": False,
        "yahoo_network_used": False,
        **adjusted.provenance,
        **volume_meta,
    }
    return EODHDDailyLoadResult(base, "OK", frame, None)


def analyze_daily_history(
    symbol: str,
    daily: pd.DataFrame | None,
    *,
    data_cutoff: date | str,
    source_available: bool = True,
    source_provider: str = EODHD_DAILY,
    overlap_validation_status: str = "RUBIX_INTRADAY_NOT_READY",
    config: DailyHistoricalSelectionConfig | None = None,
) -> DailySelectionResult:
    """Calculate the daily-only baseline result for one symbol.

    ``data_cutoff`` is mandatory and all later rows are discarded. Callers must
    supply EODHD data; a different provider is rejected rather than treated as a
    fallback. Missing source data returns an unavailable state with a ``None``
    score, never a zero.
    """

    cfg = config or DailyHistoricalSelectionConfig()
    cutoff = _to_date(data_cutoff)
    base = str(symbol).strip().upper().split(".")[0]
    source_ok = (
        source_available
        and source_provider == EODHD_DAILY
        and _has_eodhd_provenance(daily)
    )

    if daily is None or not source_ok:
        return _unavailable_result(
            base,
            cutoff,
            cfg,
            overlap_validation_status,
            "provider provenance is not EODHD_DAILY" if daily is not None else None,
        )

    cleaned = _clean_completed_daily(daily, cutoff)
    selected = cleaned.tail(cfg.lookback_sessions)
    readiness = assess_daily_readiness(
        len(selected), source_available=True, config=cfg
    )
    latest = (
        pd.Timestamp(selected.index[-1]).date().isoformat()
        if not selected.empty
        else None
    )
    fingerprint = _frame_fingerprint(selected) if not selected.empty else None

    if selected.empty:
        return DailySelectionResult(
            base,
            cfg.source_provider,
            "1d",
            cfg.raw_adjusted_mode,
            cfg.metric_version,
            cfg.config_version,
            cutoff.isoformat(),
            None,
            None,
            assess_daily_readiness(0, source_available=False, config=cfg),
            None,
            VOLUME_HISTORY_UNAVAILABLE,
            None,
            None,
            False,
            ("NO_VALID_EODHD_DAILY_SESSIONS",),
            overlap_validation_status,
        )

    latest_date = _to_date(latest)
    stale_days = max(0, (cutoff - latest_date).days)
    if (
        readiness.status == DAILY_SELECTION_READY
        and stale_days > cfg.maximum_stale_calendar_days
    ):
        readiness = DailyReadiness(
            DAILY_SELECTION_STALE,
            readiness.valid_sessions,
            readiness.minimum_sessions,
            readiness.preferred_sessions,
            readiness.preferred_ready,
            (
                f"latest valid positive-volume session {latest} is "
                f"{stale_days} calendar days before cutoff {cutoff.isoformat()}"
            ),
        )

    metrics = _compute_metrics(selected, cfg)
    volume_safe = _volume_safe(daily, cutoff)
    volume_history_status = (
        VOLUME_HISTORY_READY if volume_safe else VOLUME_HISTORY_UNRESOLVED
    )
    if not volume_safe:
        metrics = DailyHistoricalMetrics(
            **{
                **asdict(metrics),
                "daily_liquidity_score": None,
            }
        )

    score = None
    if readiness.status == DAILY_SELECTION_READY and metrics.daily_liquidity_score is not None:
        weights = cfg.weights
        score = _round(
            metrics.daily_movement_potential_score * weights.movement_potential
            + metrics.daily_range_stability_score * weights.range_stability
            + metrics.daily_volatility_zone_consistency_score
            * weights.zone_consistency
            + metrics.daily_liquidity_score * weights.liquidity,
            4,
        )

    eligible, reasons = _eligibility(metrics, score, readiness, volume_safe, cfg)
    return DailySelectionResult(
        base,
        cfg.source_provider,
        "1d",
        cfg.raw_adjusted_mode,
        cfg.metric_version,
        cfg.config_version,
        cutoff.isoformat(),
        latest,
        fingerprint,
        readiness,
        metrics,
        volume_history_status,
        score,
        None,
        eligible,
        reasons,
        overlap_validation_status,
    )


def build_frozen_daily_watchlist(
    histories: Mapping[str, pd.DataFrame | None],
    *,
    data_cutoff: date | str,
    unavailable_symbols: set[str] | frozenset[str] = frozenset(),
    overlap_validation_status: str = "RUBIX_INTRADAY_NOT_READY",
    generated_at: datetime | str | None = None,
    config: DailyHistoricalSelectionConfig | None = None,
) -> FrozenDailyWatchlist:
    """Explicitly build one immutable EODHD-daily snapshot.

    This function is the rebuild action. It has no Streamlit, live quote, or
    current-session input, so reruns and Rubix movement cannot reorder it.
    """

    cfg = config or DailyHistoricalSelectionConfig()
    cutoff = _to_date(data_cutoff)
    unavailable = {str(value).upper().split(".")[0] for value in unavailable_symbols}
    symbols = sorted(
        {str(symbol).upper().split(".")[0] for symbol in histories} | unavailable
    )
    normalized_histories = {
        str(symbol).upper().split(".")[0]: frame
        for symbol, frame in histories.items()
    }
    raw_results = [
        analyze_daily_history(
            symbol,
            normalized_histories.get(symbol),
            data_cutoff=cutoff,
            source_available=symbol not in unavailable,
            source_provider=cfg.source_provider,
            overlap_validation_status=overlap_validation_status,
            config=cfg,
        )
        for symbol in symbols
    ]

    scored = sorted(
        (result for result in raw_results if result.historical_scalping_potential is not None),
        key=lambda result: (
            -float(result.historical_scalping_potential),
            result.symbol,
        ),
    )
    ranks = {result.symbol: index + 1 for index, result in enumerate(scored)}
    ranked = tuple(
        DailySelectionResult(
            **{
                **asdict(result),
                "readiness": result.readiness,
                "metrics": result.metrics,
                "eligibility_reasons": result.eligibility_reasons,
                "historical_rank": ranks.get(result.symbol),
            }
        )
        for result in raw_results
    )
    ranked = tuple(
        sorted(
            ranked,
            key=lambda result: (
                result.historical_rank is None,
                result.historical_rank or 10**9,
                result.symbol,
            ),
        )
    )
    candidates = tuple(
        result.symbol for result in ranked if result.eligible
    )
    stamp = _iso_timestamp(generated_at)
    snapshot_payload = {
        "source": cfg.source_provider,
        "metric_version": cfg.metric_version,
        "config_version": cfg.config_version,
        "cutoff": cutoff.isoformat(),
        "results": [
            (
                result.symbol,
                result.source_data_fingerprint,
                result.historical_scalping_potential,
                result.historical_rank,
                result.eligible,
            )
            for result in ranked
        ],
    }
    snapshot_id = hashlib.sha256(
        json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return FrozenDailyWatchlist(
        cfg.source_provider,
        cfg.metric_version,
        cfg.config_version,
        cutoff.isoformat(),
        stamp,
        snapshot_id,
        ranked,
        candidates,
    )


def _compute_metrics(
    daily: pd.DataFrame, cfg: DailyHistoricalSelectionConfig
) -> DailyHistoricalMetrics:
    open_ = daily["Open"].astype(float)
    high = daily["High"].astype(float)
    low = daily["Low"].astype(float)
    close = daily["Close"].astype(float)
    volume = daily["Volume"].astype(float)

    ranges = (high - low) / open_ * 100.0
    upper = (high - open_) / open_ * 100.0
    lower = (low - open_) / open_ * 100.0
    lower_magnitude = (-lower).clip(lower=0.0)
    close_change = (close - open_) / open_ * 100.0
    turnover = close * volume
    absolute_open_gap = ((open_ / close.shift(1) - 1.0) * 100.0).abs().dropna()
    abnormal_gaps = absolute_open_gap >= cfg.abnormal_gap_percent

    median_range = float(ranges.median())
    mean_range = float(ranges.mean())
    p25 = float(ranges.quantile(0.25))
    p75 = float(ranges.quantile(0.75))
    mad = _mad(ranges)
    outliers = _outlier_mask(ranges, cfg.outlier_mad_z)
    outlier_rate = float(outliers.mean())
    divergence = abs(mean_range - median_range)
    divergence_ratio = divergence / median_range if median_range > 0 else 1.0
    normal_band_frequency = float(((ranges >= p25) & (ranges <= p75)).mean())

    hit = {threshold: float((ranges >= threshold).mean()) for threshold in (1, 1.5, 2, 2.5, 3)}
    movement_score = _movement_score(median_range, hit)
    stability_score = _stability_score(
        ranges,
        median_range,
        mean_range,
        mad,
        p25,
        p75,
        outlier_rate,
        hit[1.5],
        cfg,
    )
    zone_score = _zone_consistency_score(upper, lower_magnitude, cfg)
    liquidity_score, volume_consistency = _liquidity_score(volume, turnover)

    # close_change is deliberately calculated as part of the per-session baseline
    # contract but never enters any score. Referencing it here ensures non-finite
    # values cannot be silently ignored by future callers.
    if not np.isfinite(close_change.to_numpy(dtype=float)).all():
        raise ValueError("non-finite close-change values after daily cleaning")

    return DailyHistoricalMetrics(
        valid_session_count=int(len(daily)),
        mean_daily_range_percent=_round(mean_range),
        median_daily_range_percent=_round(median_range),
        range_p25_percent=_round(p25),
        range_p75_percent=_round(p75),
        range_iqr_percent=_round(p75 - p25),
        range_mad_percent=_round(mad),
        range_p90_percent=_round(float(ranges.quantile(0.90))),
        maximum_daily_range_percent=_round(float(ranges.max())),
        range_hit_1pct_frequency=_round(hit[1], 6),
        range_hit_1_5pct_frequency=_round(hit[1.5], 6),
        range_hit_2pct_frequency=_round(hit[2], 6),
        range_hit_2_5pct_frequency=_round(hit[2.5], 6),
        range_hit_3pct_frequency=_round(hit[3], 6),
        median_upper_excursion_percent=_round(float(upper.median())),
        median_lower_excursion_percent=_round(float(lower.median())),
        upper_excursion_mad_percent=_round(_mad(upper)),
        lower_excursion_mad_percent=_round(_mad(lower)),
        outlier_session_count=int(outliers.sum()),
        outlier_session_rate=_round(outlier_rate, 6),
        mean_median_divergence_percent=_round(divergence),
        mean_median_divergence_ratio=_round(divergence_ratio, 6),
        inside_normal_range_band_frequency=_round(normal_band_frequency, 6),
        median_volume=_round(float(volume.median()), 2),
        mean_volume=_round(float(volume.mean()), 2),
        median_turnover_egp=_round(float(turnover.median()), 2),
        mean_turnover_egp=_round(float(turnover.mean()), 2),
        volume_consistency=_round(volume_consistency, 6),
        zero_volume_session_count=int((volume <= 0).sum()),
        abnormal_gap_session_count=int(abnormal_gaps.sum()),
        abnormal_gap_session_rate=_round(
            float(abnormal_gaps.mean()) if not abnormal_gaps.empty else 0.0,
            6,
        ),
        maximum_absolute_open_gap_percent=_round(
            float(absolute_open_gap.max()) if not absolute_open_gap.empty else 0.0
        ),
        daily_movement_potential_score=_round(movement_score),
        daily_range_stability_score=_round(stability_score),
        daily_volatility_zone_consistency_score=_round(zone_score),
        daily_liquidity_score=_round(liquidity_score),
    )


def _movement_score(median_range: float, hit: Mapping[float, float]) -> float:
    median_component = _unit((median_range - 0.75) / (3.50 - 0.75))
    frequency_component = (
        0.10 * hit[1]
        + 0.20 * hit[1.5]
        + 0.30 * hit[2]
        + 0.20 * hit[2.5]
        + 0.20 * hit[3]
    )
    return 100.0 * (0.65 * median_component + 0.35 * frequency_component)


def _stability_score(
    ranges: pd.Series,
    median_range: float,
    mean_range: float,
    mad: float,
    p25: float,
    p75: float,
    outlier_rate: float,
    useful_frequency: float,
    cfg: DailyHistoricalSelectionConfig,
) -> float:
    if median_range <= 0:
        return 0.0
    mad_ratio = mad / median_range
    iqr_ratio = (p75 - p25) / median_range
    divergence_ratio = abs(mean_range - median_range) / median_range
    robust_half_width = max(
        cfg.robust_band_mad_multiplier * mad,
        0.10 * median_range,
    )
    robust_frequency = float(
        (
            (ranges >= median_range - robust_half_width)
            & (ranges <= median_range + robust_half_width)
        ).mean()
    )
    consistency = (
        0.25 * _unit(1.0 - mad_ratio / 0.50)
        + 0.20 * _unit(1.0 - iqr_ratio / 1.00)
        + 0.20 * robust_frequency
        + 0.20 * _unit(1.0 - outlier_rate / 0.20)
        + 0.15 * _unit(1.0 - divergence_ratio / 0.75)
    )
    useful_width = 0.50 * _unit((median_range - 0.75) / 2.25) + 0.50 * useful_frequency
    return 100.0 * (0.60 * consistency + 0.40 * useful_width)


def _zone_consistency_score(
    upper: pd.Series,
    lower_magnitude: pd.Series,
    cfg: DailyHistoricalSelectionConfig,
) -> float:
    upper_consistency, upper_envelope, upper_outliers = _zone_side(upper, cfg)
    lower_consistency, lower_envelope, lower_outliers = _zone_side(
        lower_magnitude, cfg
    )
    envelope = math.sqrt(max(0.0, upper_envelope * lower_envelope))
    outlier_score = _unit(1.0 - max(upper_outliers, lower_outliers) / 0.20)
    return 100.0 * (
        0.30 * upper_consistency
        + 0.30 * lower_consistency
        + 0.25 * envelope
        + 0.15 * outlier_score
    )


def _zone_side(
    values: pd.Series, cfg: DailyHistoricalSelectionConfig
) -> tuple[float, float, float]:
    median = float(values.median())
    mad = _mad(values)
    p25 = float(values.quantile(0.25))
    p75 = float(values.quantile(0.75))
    scale = max(abs(median), 0.25)
    dispersion = (
        0.55 * _unit(1.0 - (mad / scale) / 0.75)
        + 0.45 * _unit(1.0 - ((p75 - p25) / scale) / 1.50)
    )
    half_width = max(cfg.robust_band_mad_multiplier * mad, 0.25)
    envelope = float(
        ((values >= median - half_width) & (values <= median + half_width)).mean()
    )
    outlier_rate = float(_outlier_mask(values, cfg.outlier_mad_z).mean())
    return dispersion, envelope, outlier_rate


def _liquidity_score(
    volume: pd.Series, turnover: pd.Series
) -> tuple[float, float]:
    median_volume = float(volume.median())
    median_turnover = float(turnover.median())
    consistency = (
        float((volume >= 0.50 * median_volume).mean()) if median_volume > 0 else 0.0
    )
    nonzero = float((volume > 0).mean())
    score = 100.0 * (
        0.55 * _log_unit(median_turnover, 500_000.0, 50_000_000.0)
        + 0.20 * _log_unit(median_volume, 30_000.0, 3_000_000.0)
        + 0.20 * consistency
        + 0.05 * nonzero
    )
    return score, consistency


def _eligibility(
    metrics: DailyHistoricalMetrics,
    score: float | None,
    readiness: DailyReadiness,
    volume_safe: bool,
    cfg: DailyHistoricalSelectionConfig,
) -> tuple[bool, tuple[str, ...]]:
    reasons = []
    threshold = cfg.eligibility
    if readiness.status != DAILY_SELECTION_READY:
        reasons.append(readiness.status)
    if not volume_safe or metrics.daily_liquidity_score is None:
        reasons.append(VOLUME_HISTORY_UNRESOLVED)
    if score is None or score < threshold.minimum_historical_score:
        reasons.append("HISTORICAL_SCORE_BELOW_MINIMUM")
    if metrics.median_daily_range_percent < threshold.minimum_median_range_percent:
        reasons.append("MEDIAN_DAILY_RANGE_BELOW_MINIMUM")
    if metrics.range_hit_2pct_frequency < threshold.minimum_two_percent_frequency:
        reasons.append("TWO_PERCENT_RANGE_FREQUENCY_BELOW_MINIMUM")
    if metrics.daily_range_stability_score < threshold.minimum_range_stability_score:
        reasons.append("DAILY_RANGE_STABILITY_BELOW_MINIMUM")
    if (
        metrics.daily_volatility_zone_consistency_score
        < threshold.minimum_zone_consistency_score
    ):
        reasons.append("DAILY_ZONE_CONSISTENCY_BELOW_MINIMUM")
    if (
        metrics.daily_liquidity_score is None
        or metrics.daily_liquidity_score < threshold.minimum_liquidity_score
    ):
        reasons.append("DAILY_LIQUIDITY_SCORE_BELOW_MINIMUM")
    if metrics.median_turnover_egp < threshold.minimum_median_turnover_egp:
        reasons.append("MEDIAN_TURNOVER_BELOW_MINIMUM")
    return not reasons, tuple(reasons)


def _clean_completed_daily(daily: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    frame = daily.copy()
    rename = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "date": "Date",
    }
    frame = frame.rename(
        columns={column: rename.get(str(column).lower(), column) for column in frame.columns}
    )
    if "Date" in frame.columns:
        frame.index = pd.to_datetime(frame.pop("Date"), errors="coerce")
    else:
        frame.index = pd.to_datetime(frame.index, errors="coerce")
    if isinstance(frame.index, pd.DatetimeIndex) and frame.index.tz is not None:
        frame.index = frame.index.tz_convert("Africa/Cairo").tz_localize(None)
    complete_column = next(
        (
            column
            for column in ("Complete", "IsComplete", "complete", "is_complete")
            if column in frame.columns
        ),
        None,
    )
    if complete_column is not None:
        complete = frame[complete_column].map(_is_completed_flag)
        frame = frame[complete]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame(columns=required)
    frame = frame[~frame.index.isna()]
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required)
    frame = frame[
        (frame["Open"] > 0)
        & (frame["High"] > 0)
        & (frame["Low"] > 0)
        & (frame["Close"] > 0)
        & (frame["Volume"] > 0)
    ]
    frame = frame[
        (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1))
        & (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1))
    ]
    session_dates = pd.DatetimeIndex(frame.index).normalize()
    frame = frame[pd.Index(session_dates.date) <= cutoff]
    session_dates = pd.DatetimeIndex(frame.index).normalize()
    duplicate_sessions = session_dates.duplicated(keep=False)
    frame = frame[~duplicate_sessions]
    frame.index = session_dates[~duplicate_sessions]
    frame = frame.sort_index()
    return frame[required]


def _unavailable_result(
    symbol: str,
    cutoff: date,
    cfg: DailyHistoricalSelectionConfig,
    overlap_validation_status: str,
    detail: str | None,
) -> DailySelectionResult:
    reasons = [DAILY_SELECTION_UNAVAILABLE]
    if detail:
        reasons.append(detail)
    return DailySelectionResult(
        symbol,
        cfg.source_provider,
        "1d",
        cfg.raw_adjusted_mode,
        cfg.metric_version,
        cfg.config_version,
        cutoff.isoformat(),
        None,
        None,
        assess_daily_readiness(0, source_available=False, config=cfg),
        None,
        VOLUME_HISTORY_UNAVAILABLE,
        None,
        None,
        False,
        tuple(reasons),
        overlap_validation_status,
    )


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    canonical = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
    canonical.insert(0, "Date", [value.date().isoformat() for value in canonical.index])
    payload = canonical.to_csv(index=False, float_format="%.10g", lineterminator="\n")
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _volume_safe(frame: pd.DataFrame, cutoff: date) -> bool:
    metadata = dict((frame.attrs or {}).get("market_data", {}))
    normalized_through = metadata.get("normalization_data_cutoff")
    if normalized_through and _to_date(normalized_through) > cutoff:
        return False
    return bool(metadata.get("volume_safe_for_lookback", True))


def _has_eodhd_provenance(frame: pd.DataFrame | None) -> bool:
    if frame is None:
        return False
    metadata = dict((frame.attrs or {}).get("market_data", {}))
    providers = {
        str(metadata.get(key, "")).strip().upper()
        for key in ("provider", "effective_provider", "source_provider")
        if metadata.get(key)
    }
    return bool(providers) and providers <= {"EODHD", EODHD_DAILY}


def _is_completed_flag(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {
            "",
            "0",
            "false",
            "forming",
            "incomplete",
            "partial",
            "pending",
        }
    return bool(value)


def _outlier_mask(values: pd.Series, threshold: float) -> pd.Series:
    median = float(values.median())
    mad = _mad(values)
    if mad > 0:
        return (0.67448975 * (values - median).abs() / mad) > threshold
    p25 = float(values.quantile(0.25))
    p75 = float(values.quantile(0.75))
    iqr = p75 - p25
    if iqr <= 0:
        return pd.Series(False, index=values.index)
    return (values < p25 - 1.5 * iqr) | (values > p75 + 1.5 * iqr)


def _mad(values: pd.Series) -> float:
    median = float(values.median())
    return float((values - median).abs().median())


def _log_unit(value: float, low: float, high: float) -> float:
    if value <= 0 or low <= 0 or high <= low:
        return 0.0
    return _unit((math.log10(value) - math.log10(low)) / (math.log10(high) - math.log10(low)))


def _unit(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not np.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _to_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _iso_timestamp(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    return str(value)
