import json
import os
from copy import deepcopy


SETTINGS_FILE = "config/settings.json"


DEFAULT_SETTINGS = {
    "strategy": {
        "min_score": 65,
        "min_confidence": 80,
        "min_rr": 2.0,
        "min_trend": 25,
        "min_momentum": 5,
        "min_volume": 5
    },

    "backtest": {
        "entry_wait_days": 5,
        "exit_mode": "TARGET1",
        "max_holding_days": 20,
        "move_to_breakeven": True,
        "partial_exit": False,
        "partial_percent": 0.50,

        "risk_mode": "FIXED",
        "risk_percent": 2,

        "trailing_mode": "EMA20",
        "trailing_atr": 2,

        "allow_overlapping_trades": False,

        "initial_capital": 100000,

        "commission": 0.003,
        "slippage": 0.0005
    },

    "ai": {
        "enabled": False,
        "min_probability": 70
    }
}


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

            return json.load(f)

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