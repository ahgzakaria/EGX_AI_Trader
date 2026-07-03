from optimization.search_space import SearchSpace
from optimization.evaluator import StrategyEvaluator
from optimization.results import ResultsManager


def main():

    space = SearchSpace()

    evaluator = StrategyEvaluator()

    manager = ResultsManager()

    total = space.size()

    print("=" * 60)
    print("EGX AI STRATEGY OPTIMIZER")
    print("=" * 60)
    print(f"Total Configurations : {total}")
    print()

    for index, params in enumerate(space.generate(), start=1):

        print(
            f"[{index}/{total}] "
            f"Score={params.score} "
            f"Conf={params.confidence} "
            f"RR={params.rr}"
        )

        result = evaluator.evaluate(params)

        manager.add(result)

        if result:

            print(
                f"PF={result['ProfitFactor']} | "
                f"Net={result['NetProfit']} | "
                f"Trades={result['Trades']}"
            )

    print("\nSaving Results...")

    manager.save()

    best = manager.best()

    print("\n" + "=" * 60)
    print("BEST CONFIGURATION")
    print("=" * 60)

    if best is None:

        print("No valid result found.")

    else:

        for key, value in best.items():

            print(f"{key:15} : {value}")


if __name__ == "__main__":

    main()