import json
import os


WATCHLIST_FILE = "data/watchlist.json"


class Watchlist:

    def __init__(self):

        os.makedirs("data", exist_ok=True)

        if not os.path.exists(WATCHLIST_FILE):

            with open(
                WATCHLIST_FILE,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump([], f)

    def load(self):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                symbols = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

        return symbols if isinstance(symbols, list) else []

    def save(self, symbols):

        with open(

            WATCHLIST_FILE,

            "w",

            encoding="utf-8"

        ) as f:

            json.dump(

                sorted(list(set(symbols))),

                f,

                indent=4

            )

    def add(self, symbol):

        symbols = self.load()

        if symbol not in symbols:

            symbols.append(symbol)

            self.save(symbols)

    def remove(self, symbol):

        symbols = self.load()

        if symbol in symbols:

            symbols.remove(symbol)

            self.save(symbols)

    def clear(self):

        self.save([])
