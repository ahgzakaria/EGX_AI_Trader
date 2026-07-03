from config.settings_manager import settings

# ==================================
# Strategy Settings
# ==================================

strategy = settings.get("strategy")

MIN_SCORE = strategy["min_score"]

MIN_CONFIDENCE = strategy["min_confidence"]

MIN_RR = strategy["min_rr"]

MIN_TREND = strategy["min_trend"]

MIN_MOMENTUM = strategy["min_momentum"]

MIN_VOLUME = strategy["min_volume"]