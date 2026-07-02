from portfolio.risk import RiskManager


class PositionSizer:

    def __init__(self, capital, risk_percent=1.0):

        self.risk = RiskManager(
            capital,
            risk_percent
        )

    # ==================================
    # Position Summary
    # ==================================

    def calculate(

        self,

        entry_price,

        stop_loss

    ):

        summary = self.risk.summary(

            entry_price,

            stop_loss

        )

        enough_capital = (

            summary["PositionValue"]

            <=

            summary["Capital"]

        )

        cash_remaining = round(

            summary["Capital"]

            -

            summary["PositionValue"],

            2

        )

        summary["EnoughCapital"] = enough_capital

        summary["CashRemaining"] = cash_remaining

        return summary

    # ==================================
    # Position Status
    # ==================================

    def status(

        self,

        entry_price,

        stop_loss

    ):

        data = self.calculate(

            entry_price,

            stop_loss

        )

        if data["Shares"] == 0:

            return "INVALID"

        if data["EnoughCapital"]:

            return "OK"

        return "INSUFFICIENT_CAPITAL"