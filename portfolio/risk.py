class RiskManager:

    def __init__(

        self,

        capital,

        risk_percent=1.0

    ):

        self.capital = float(capital)

        self.risk_percent = float(risk_percent)

    # ==================================
    # Amount to Risk
    # ==================================

    def risk_amount(self):

        return round(

            self.capital *

            self.risk_percent /

            100,

            2

        )

    # ==================================
    # Position Size
    # ==================================

    def position_size(

        self,

        entry_price,

        stop_loss

    ):

        risk_per_share = entry_price - stop_loss

        if risk_per_share <= 0:

            return 0

        shares = int(

            self.risk_amount()

            /

            risk_per_share

        )

        return max(shares, 0)

    # ==================================
    # Position Value
    # ==================================

    def position_value(

        self,

        entry_price,

        stop_loss

    ):

        shares = self.position_size(

            entry_price,

            stop_loss

        )

        return round(

            shares *

            entry_price,

            2

        )

    # ==================================
    # Maximum Loss
    # ==================================

    def maximum_loss(

        self,

        entry_price,

        stop_loss

    ):

        shares = self.position_size(

            entry_price,

            stop_loss

        )

        return round(

            shares *

            (

                entry_price -

                stop_loss

            ),

            2

        )

    # ==================================
    # Summary
    # ==================================

    def summary(

        self,

        entry_price,

        stop_loss

    ):

        shares = self.position_size(

            entry_price,

            stop_loss

        )

        value = self.position_value(

            entry_price,

            stop_loss

        )

        loss = self.maximum_loss(

            entry_price,

            stop_loss

        )

        return {

            "Capital": self.capital,

            "RiskPercent": self.risk_percent,

            "RiskAmount": self.risk_amount(),

            "Shares": shares,

            "PositionValue": value,

            "MaximumLoss": loss

        }