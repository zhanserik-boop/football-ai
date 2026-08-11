from pathlib import Path
import pandas as pd

FILES = [
    "epl_lineups_4seasons.csv",
    "player_match_history_2022.csv",
    "player_match_history_2023.csv",
    "player_match_history_2024.csv",
    "player_match_history_2025.csv",
]

KEYWORDS = ("formation", "coach", "manager", "fixture", "team", "player", "starter", "position", "minutes")


def audit_file(filename):
    path = Path(filename)
    if not path.exists():
        print(f"MISSING: {filename}")
        return

    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    print("=" * 72)
    print("FILE:", filename)
    print("ROWS:", len(df))
    print("COLUMNS:", ", ".join(map(str, df.columns)))

    interesting = [
        str(c) for c in df.columns
        if any(k in str(c).lower() for k in KEYWORDS)
    ]
    print("CONTEXT_COLUMNS:", ", ".join(interesting) if interesting else "NONE")

    for label, needles in {
        "FORMATION": ("formation",),
        "COACH": ("coach", "manager"),
    }.items():
        matches = [c for c in df.columns if any(n in str(c).lower() for n in needles)]
        print(f"HAS_{label}:", "YES" if matches else "NO")
        if matches:
            for col in matches:
                print(f"  {col}: non_null={int(df[col].notna().sum())}")

    fixture_cols = [c for c in df.columns if "fixture" in str(c).lower()]
    if fixture_cols:
        col = fixture_cols[0]
        print("UNIQUE_FIXTURES:", int(df[col].nunique(dropna=True)))


def main():
    for filename in FILES:
        audit_file(filename)


if __name__ == "__main__":
    main()
