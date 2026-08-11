import json
import os

import joblib
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

MODEL_FILE = "btts_core_model_2026.joblib"
META_FILE = "btts_core_model_2026_meta.json"

FEATURE_FILE = "btts_live_features.csv"

OUTPUT_FILE = "btts_live_predictions.csv"


# ============================================================
# LOAD FROZEN SPEC
# ============================================================

print()
print("=" * 80)
print("FOOTBALL AI — BTTS LIVE MODEL")
print("=" * 80)


if not os.path.exists(
    MODEL_FILE
):
    raise FileNotFoundError(
        f"Missing frozen model: {MODEL_FILE}"
    )


if not os.path.exists(
    META_FILE
):
    raise FileNotFoundError(
        f"Missing model meta: {META_FILE}"
    )


if not os.path.exists(
    FEATURE_FILE
):
    raise FileNotFoundError(
        f"Missing live features: {FEATURE_FILE}"
    )


with open(
    META_FILE,
    "r",
    encoding="utf-8"
) as f:

    meta = json.load(f)


features = meta[
    "features"
]


rule = meta[
    "frozen_rule"
]


model_low = float(
    rule[
        "model_probability_min_inclusive"
    ]
)


model_high = float(
    rule[
        "model_probability_max_exclusive"
    ]
)


min_edge = float(
    rule[
        "minimum_edge"
    ]
)


print(
    "Frozen feature count:",
    len(features)
)


print(
    "Frozen probability zone:",
    f"{model_low:.0%}",
    "to",
    f"<{model_high:.0%}"
)


print(
    "Frozen minimum edge:",
    f"{min_edge:.0%}"
)


# ============================================================
# OUTPUT SCHEMA
# ============================================================

output_columns = [

    "fixture_id",
    "kickoff_utc",

    "home_team",
    "away_team",

    "model_yes",
    "model_zone",
    "btts_status",

    "expected_home_xg",
    "expected_away_xg",
    "expected_total_xg",

    "home_xg_matches",
    "away_xg_matches",

    "home_sot_matches",
    "away_sot_matches",
]


# ============================================================
# LOAD MODEL + LIVE FEATURES
# ============================================================

model = joblib.load(
    MODEL_FILE
)


try:

    df = pd.read_csv(
        FEATURE_FILE
    ).copy()

except pd.errors.EmptyDataError:

    df = pd.DataFrame()


print()
print(
    "Live fixtures:",
    len(df)
)


# ============================================================
# EMPTY INPUT
#
# Important:
# overwrite old prediction output with a valid empty CSV.
# This prevents stale fixtures/predictions from surviving.
# ============================================================

if len(df) == 0:

    output = pd.DataFrame(
        columns=output_columns
    )


    output.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )


    print()
    print("=" * 80)
    print("BTTS LIVE PREDICTIONS")
    print("=" * 80)

    print(
        "No live fixtures to predict."
    )


    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    print(
        "Fixtures:",
        0
    )

    print(
        "Inside frozen 60-65% zone:",
        0
    )

    print(
        "Outside frozen zone:",
        0
    )

    print()

    print(
        "Saved:",
        OUTPUT_FILE
    )

    print(
        "API REQUESTS USED: 0"
    )

    print("=" * 80)

    raise SystemExit(0)


# ============================================================
# VERIFY EXACT INPUT FEATURES
# ============================================================

missing = [

    col

    for col in features

    if col not in df.columns
]


if missing:

    raise RuntimeError(
        "Missing frozen BTTS features: "
        +
        ", ".join(
            missing
        )
    )


for col in features:

    df[
        col
    ] = pd.to_numeric(

        df[
            col
        ],

        errors="coerce"
    )


bad = df[
    features
].isna().any(
    axis=1
)


if bad.any():

    print()
    print(
        "ERROR: fixtures with missing model features:"
    )


    cols = [

        "fixture_id",
        "home_team",
        "away_team",
    ]


    print(

        df.loc[
            bad,
            cols
        ].to_string(
            index=False
        )
    )


    raise RuntimeError(
        "Live feature matrix contains NaN."
    )


# ============================================================
# MODEL PROBABILITY
# ============================================================

df[
    "model_yes"
] = (

    model.predict_proba(
        df[
            features
        ]
    )[:, 1]
)


# ============================================================
# FROZEN MODEL ZONE
#
# IMPORTANT:
# This is NOT yet a shadow bet.
#
# Market odds are still required.
# ============================================================

df[
    "model_zone"
] = (

    (
        df[
            "model_yes"
        ]
        >=
        model_low
    )

    &

    (
        df[
            "model_yes"
        ]
        <
        model_high
    )
)


df[
    "btts_status"
] = "OUTSIDE MODEL ZONE"


df.loc[

    df[
        "model_zone"
    ],

    "btts_status"

] = "WAIT FOR MARKET"


# ============================================================
# OUTPUT
# ============================================================

output = df[
    output_columns
].copy()


output.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)


# ============================================================
# DISPLAY
# ============================================================

print()
print("=" * 80)
print("BTTS LIVE PREDICTIONS")
print("=" * 80)


for _, row in output.iterrows():

    print()

    print(
        f"{row['home_team']} "
        f"vs "
        f"{row['away_team']}"
    )

    print(
        "  Model BTTS YES:",
        f"{row['model_yes']:.2%}"
    )

    print(
        "  Frozen zone:",
        "YES"
        if row[
            "model_zone"
        ]
        else
        "NO"
    )

    print(
        "  Expected xG:",
        f"{row['expected_home_xg']:.3f}",
        "-",
        f"{row['expected_away_xg']:.3f}"
    )

    print(
        "  Status:",
        row[
            "btts_status"
        ]
    )


# ============================================================
# SUMMARY
# ============================================================

inside = int(
    output[
        "model_zone"
    ].sum()
)


print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)


print(
    "Fixtures:",
    len(output)
)


print(
    "Inside frozen 60-65% zone:",
    inside
)


print(
    "Outside frozen zone:",
    len(output)
    -
    inside
)


print()

print(
    "IMPORTANT:"
)

print(
    "MODEL ZONE != BET"
)

print(
    "A SHADOW BET requires:"
)

print(
    "60% <= model_yes < 65%"
)

print(
    "AND fair-market edge >= 3%"
)

print()

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "API REQUESTS USED: 0"
)

print("=" * 80)
