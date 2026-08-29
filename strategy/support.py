def support_resistance(df, i, window=20):

    start = max(0, i - window + 1)

    support = float(df["Low"].iloc[start:i + 1].min())

    # Resistance excludes today, matching `strategy/entry.py`, which places
    # Target1 and Target2 on exactly this level.
    #
    # It used to include today, and the two definitions disagreed on 22.4% of
    # real bars while both were consulted in the same decision: `entry.py`
    # placed the targets off the prior high, and `quality_filter` measured its
    # "at least 3% of room" against a different number.
    #
    # Excluding today is the definition that means something. The gate asks
    # whether there is room before overhead supply, and overhead supply is where
    # sellers previously appeared -- a level today's own high has not tested.
    # Including today also made the gate self-referential: a bar that printed a
    # new high raised the ceiling it was then measured against, which is the
    # same shape of error as a breakout breaking out of itself.
    #
    # Measured, and it changes nothing: an isolated run is identical to one
    # without it, trade for trade and reason for reason. Bars where the two
    # definitions disagree are bars where today made a new high, so the close
    # sits near the top of the range and the 3% room gate refuses them either
    # way. The contradiction was real and its cost was zero, which is the
    # cheapest kind of fix and the easiest kind to keep putting off.
    #
    # See DAILY_STRATEGY_DIAGNOSIS.md section 3.
    if i > 0:
        resistance = float(df["High"].iloc[start:i].max())
    else:
        resistance = float(df["High"].iloc[0])

    current_price = float(df["Close"].iloc[i])

    score = 0
    confidence = 0
    reasons = []

    distance_to_support = (
        (current_price - support) / support
    ) * 100

    distance_to_resistance = (
        (resistance - current_price) / current_price
    ) * 100

    # قريب من الدعم
    if distance_to_support <= 3:
        score += 10
        confidence += 10
        reasons.append("Near Support")

    # مساحة صعود جيدة
    if distance_to_resistance >= 5:
        score += 5
        confidence += 5
        reasons.append("Good Upside Potential")

    return {

        "score": score,

        "confidence": confidence,

        "reasons": reasons,

        "support": round(support, 2),

        "resistance": round(resistance, 2)

    }