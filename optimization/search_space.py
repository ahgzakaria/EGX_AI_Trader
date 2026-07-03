from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class StrategyParameters:

    score: int
    confidence: int
    rr: float
    trend: int
    momentum: int
    volume: int


class SearchSpace:

    def __init__(self):

        # ==========================
        # FAST SEARCH
        # ==========================

        self.score = [

            60,
            70,
            80

        ]

        self.confidence = [

            70,
            80,
            90

        ]

        self.rr = [

            1.5,
            2.0

        ]

        self.trend = [

            20,
            30

        ]

        self.momentum = [

            5

        ]

        self.volume = [

            5,
            10

        ]

    # ==========================

    def generate(self):

        for values in product(

            self.score,

            self.confidence,

            self.rr,

            self.trend,

            self.momentum,

            self.volume

        ):

            yield StrategyParameters(

                *values

            )

    # ==========================

    def size(self):

        return (

            len(self.score)

            *

            len(self.confidence)

            *

            len(self.rr)

            *

            len(self.trend)

            *

            len(self.momentum)

            *

            len(self.volume)

        )