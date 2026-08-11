import pandas as pd

from historical_context_builder import (
    add_basic_derived_fields,
    add_rest_days,
    build_coverage_report,
    normalize_team_name,
)


def test_team_aliases_normalize_to_understat_names():
    assert normalize_team_name("Man United") == "Manchester United"
    assert normalize_team_name("Man City") == "Manchester City"
    assert normalize_team_name("Wolves") == "Wolverhampton Wanderers"
    assert normalize_team_name("Nott'm Forest") == "Nottingham Forest"
    assert normalize_team_name("Arsenal") == "Arsenal"


def test_rest_days_are_calculated_per_team_without_future_data():
    d = pd.DataFrame(
        [
            {
                "season": 2022,
                "date": pd.Timestamp("2022-08-01"),
                "home_team": "Arsenal",
                "away_team": "Chelsea",
            },
            {
                "season": 2022,
                "date": pd.Timestamp("2022-08-06"),
                "home_team": "Liverpool",
                "away_team": "Arsenal",
            },
            {
                "season": 2022,
                "date": pd.Timestamp("2022-08-09"),
                "home_team": "Chelsea",
                "away_team": "Liverpool",
            },
        ]
    )

    out = add_rest_days(d)
    first = out.iloc[0]
    second = out.iloc[1]
    third = out.iloc[2]

    assert pd.isna(first["home_rest_days"])
    assert pd.isna(first["away_rest_days"])
    assert pd.isna(second["home_rest_days"])
    assert second["away_rest_days"] == 5.0
    assert third["home_rest_days"] == 8.0
    assert third["away_rest_days"] == 3.0


def test_basic_derived_fields_are_arithmetic_only():
    d = pd.DataFrame(
        [
            {
                "home_goals": 2,
                "away_goals": 1,
                "home_xg": 1.8,
                "away_xg": 0.7,
                "home_shots": 12,
                "away_shots": 8,
                "home_sot": 5,
                "away_sot": 2,
                "home_yellow": 2,
                "away_yellow": 3,
                "home_red": 0,
                "away_red": 1,
                "home_fouls": 10,
                "away_fouls": 14,
                "home_corners": 6,
                "away_corners": 4,
            }
        ]
    )

    out = add_basic_derived_fields(d).iloc[0]
    assert out["total_goals"] == 3
    assert out["total_xg"] == 2.5
    assert out["home_xg_diff"] == 1.1
    assert out["home_shot_diff"] == 4
    assert out["home_sot_diff"] == 3
    assert out["total_yellow"] == 5
    assert out["total_red"] == 1
    assert out["total_fouls"] == 24
    assert out["total_corners"] == 10


def test_coverage_report_is_explicit_by_season_and_column():
    d = pd.DataFrame(
        {
            "season": [2022, 2022],
            "home_xg": [1.0, None],
            "away_xg": [0.5, 0.8],
            "referee": ["A Ref", None],
        }
    )

    report = build_coverage_report(d)
    home_xg = report[(report["season"] == 2022) & (report["column"] == "home_xg")].iloc[0]
    away_xg = report[(report["season"] == 2022) & (report["column"] == "away_xg")].iloc[0]
    referee = report[(report["season"] == 2022) & (report["column"] == "referee")].iloc[0]

    assert home_xg["coverage_pct"] == 50.0
    assert away_xg["coverage_pct"] == 100.0
    assert referee["coverage_pct"] == 50.0
