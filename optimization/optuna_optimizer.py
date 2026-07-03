import optuna

from optimization.evaluator import StrategyEvaluator


class OptunaOptimizer:

    def __init__(self):

        self.evaluator = StrategyEvaluator()

    # ==================================

    def objective(self, trial):

        score = trial.suggest_int(

            "score",

            55,

            80

        )

        confidence = trial.suggest_int(

            "confidence",

            65,

            90

        )

        rr = trial.suggest_float(

            "rr",

            1.2,

            3.0,

            step=0.1

        )

        trend = trial.suggest_int(

            "trend",

            15,

            35

        )

        momentum = trial.suggest_int(

            "momentum",

            0,

            15

        )

        volume = trial.suggest_int(

            "volume",

            0,

            15

        )

        class Params:

            pass

        params = Params()

        params.score = score
        params.confidence = confidence
        params.rr = rr
        params.trend = trend
        params.momentum = momentum
        params.volume = volume

        result = self.evaluator.evaluate(

            params

        )

        if result is None:

            return -999

        # ==================================
        # Reject Small Sample
        # ==================================

        if result["Trades"] < 300:

            return -999

        # ==================================
        # Objective Function
        # ==================================

        objective = (

            result["ProfitFactor"] * 100

            +

            result["Expectancy"] * 50

            +

            result["WinRate"]

        )

        trial.set_user_attr(

            "Result",

            result

        )

        return objective

    # ==================================

    def run(

        self,

        trials=100

    ):

        study = optuna.create_study(

            direction="maximize",

            study_name="EGX_AI",

            storage="sqlite:///optimization/optuna.db",

            load_if_exists=True

        )

        study.optimize(

            self.objective,

            n_trials=trials,

            show_progress_bar=True

        )

        print()

        print("=" * 60)

        print("BEST PARAMETERS")

        print("=" * 60)

        print(

            study.best_params

        )

        print()

        print("BEST SCORE")

        print(

            study.best_value

        )

        print()

        print("BACKTEST")

        print(

            study.best_trial.user_attrs["Result"]

        )


if __name__ == "__main__":

    optimizer = OptunaOptimizer()

    optimizer.run(100)