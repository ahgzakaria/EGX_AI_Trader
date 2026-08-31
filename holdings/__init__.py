"""The positions the user actually holds, and how to leave them well.

Deliberately NOT inside ``portfolio/``. That package is the backtest's
simulator, its risk sizing and its position accounting -- all of it about
hypothetical trades over frozen history. This package is about real money in a
real account, and the two must never be imported for each other by accident: a
number from the simulator printed beside a real average cost would look exactly
like a fact about the user's account.

What lives here:

* ``store``   -- durable SQLite storage of what was bought, sold and received.
* ``book``    -- average-cost accounting over that history.
* ``plan``    -- the exit plan for a held position, built from existing engines.
* ``rules``   -- the adaptive layer that revises a plan from live evidence.

Nothing here places an order, and nothing here is advice. Every recommendation
this package produces is a reading of measured evidence against a plan the user
can see, change, or ignore.
"""

#: One file, outside version control (``*.db`` is gitignored), holding the only
#: copy of data this project cannot rebuild from a provider: what the user owns.
DEFAULT_DATABASE = "data/portfolio.db"
