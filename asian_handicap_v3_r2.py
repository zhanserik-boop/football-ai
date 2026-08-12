"""Canonical Asian Handicap normalization for Football AI V3 R2.

API-Football commonly labels both Home and Away prices with the same numeric
home-team handicap.  Some feeds instead expose conventional opposite-signed
team handicaps.  This module accepts both layouts, pairs both prices from the
same bookmaker/line, chooses each bookmaker's balanced main line, and only then
builds cross-book consensus.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict


NORMALIZATION_VERSION = 2


def clean(value):
    return "" if value is None else str(value).strip()


def safe_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def round_quarter(value):
    return round(float(value) * 4.0) / 4.0


def parse_provider_value(value):
    """Return provider side and numeric label, averaging split AH lines."""
    text = clean(value).replace("−", "-").replace("–", "-")
    lowered = text.casefold()
    if lowered.startswith("home"):
        side = "HOME"
    elif lowered.startswith("away"):
        side = "AWAY"
    else:
        return None
    numbers = [safe_float(item) for item in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    numbers = [item for item in numbers if item is not None]
    if not numbers:
        return None
    return {"side": side, "provider_handicap": round_quarter(sum(numbers) / len(numbers))}


def _field(row, *names):
    for name in names:
        if name in row and row.get(name) not in {None, ""}:
            return row.get(name)
    return None


def normalize_rows(rows):
    output = []
    for row in rows:
        side = clean(_field(row, "side", "parsed_side")).upper()
        label = safe_float(_field(row, "provider_handicap", "handicap", "parsed_handicap"))
        odd = safe_float(row.get("odd"))
        if side not in {"HOME", "AWAY"} or label is None or odd is None or odd <= 1.0:
            continue
        book_id = clean(_field(row, "bookmaker_id", "bookmaker"))
        if not book_id:
            continue
        output.append({
            "bookmaker_id": book_id,
            "bookmaker": clean(row.get("bookmaker")) or book_id,
            "side": side,
            "provider_handicap": round_quarter(label),
            "odd": odd,
        })
    return output


def paired_ladder(rows):
    """Pair Home/Away prices and normalize every pair to a home handicap."""
    by_book = defaultdict(list)
    for row in normalize_rows(rows):
        by_book[row["bookmaker_id"]].append(row)

    pairs = []
    for book_id, book_rows in by_book.items():
        sides = defaultdict(lambda: defaultdict(list))
        for row in book_rows:
            sides[row["provider_handicap"]][row["side"]].append(row)

        used = set()
        # API-Football layout: Home and Away share the home-team numeric label.
        for label, labels in sides.items():
            if labels["HOME"] and labels["AWAY"]:
                for home in labels["HOME"]:
                    for away in labels["AWAY"]:
                        pairs.append(_pair(
                            book_id, home, away, label, "SAME_LABEL_HOME_AH"
                        ))
                used.add(("HOME", label))
                used.add(("AWAY", label))

        # Conventional layout fallback: Away handicap is the opposite sign.
        for label, labels in sides.items():
            if not labels["HOME"] or ("HOME", label) in used:
                continue
            away_labels = sides.get(round_quarter(-label), {})
            away_rows = away_labels.get("AWAY", []) if away_labels else []
            if away_rows and ("AWAY", round_quarter(-label)) not in used:
                for home in labels["HOME"]:
                    for away in away_rows:
                        pairs.append(_pair(
                            book_id, home, away, label, "OPPOSITE_TEAM_AH"
                        ))
                used.add(("HOME", label))
                used.add(("AWAY", round_quarter(-label)))
    return pairs


def _pair(book_id, home, away, home_handicap, layout):
    return {
        "bookmaker_id": book_id,
        "bookmaker": home["bookmaker"],
        "home_handicap": round_quarter(home_handicap),
        "home_odd": home["odd"],
        "away_odd": away["odd"],
        "provider_layout": layout,
    }


def bookmaker_main_lines(rows):
    by_book = defaultdict(list)
    for row in paired_ladder(rows):
        by_book[row["bookmaker_id"]].append(row)
    selected = []
    for book_rows in by_book.values():
        selected.append(min(
            book_rows,
            key=lambda row: (
                abs(math.log(row["home_odd"] / row["away_odd"])),
                abs((1.0 / row["home_odd"] + 1.0 / row["away_odd"]) - 1.05),
                abs(row["home_handicap"]),
            ),
        ))
    return selected


def market_consensus(rows):
    selected = bookmaker_main_lines(rows)
    if not selected:
        return None
    counts = Counter(row["home_handicap"] for row in selected)
    max_count = max(counts.values())
    candidates = [line for line, count in counts.items() if count == max_count]
    ordered = sorted(row["home_handicap"] for row in selected)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else round_quarter(
        (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    home_line = min(candidates, key=lambda line: abs(line - median))
    same = [row for row in selected if row["home_handicap"] == home_line]
    home_avg = sum(row["home_odd"] for row in same) / len(same)
    away_avg = sum(row["away_odd"] for row in same) / len(same)
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "home_handicap": home_line,
        "home_average_odds": home_avg,
        "away_average_odds": away_avg,
        "home_best_odds": max(row["home_odd"] for row in same),
        "away_best_odds": max(row["away_odd"] for row in same),
        "home_best_bookmaker": max(same, key=lambda row: row["home_odd"])["bookmaker"],
        "away_best_bookmaker": max(same, key=lambda row: row["away_odd"])["bookmaker"],
        "bookmakers": len(same),
        "selected_bookmakers": len(selected),
        "provider_layouts": sorted({row["provider_layout"] for row in same}),
    }


def signal_market(rows, signal):
    consensus = market_consensus(rows)
    signal = clean(signal).upper()
    if consensus is None or signal not in {"HOME", "AWAY"}:
        return None
    home = signal == "HOME"
    return {
        "handicap": consensus["home_handicap"] if home else -consensus["home_handicap"],
        "average_odds": consensus["home_average_odds"] if home else consensus["away_average_odds"],
        "best_odds": consensus["home_best_odds"] if home else consensus["away_best_odds"],
        "best_bookmaker": consensus["home_best_bookmaker"] if home else consensus["away_best_bookmaker"],
        "bookmakers": consensus["bookmakers"],
        "home_handicap": consensus["home_handicap"],
        "normalization_version": NORMALIZATION_VERSION,
        "provider_layouts": consensus["provider_layouts"],
    }


def line_move_toward_signal(opening_home_handicap, current_home_handicap, signal):
    opening = safe_float(opening_home_handicap)
    current = safe_float(current_home_handicap)
    signal = clean(signal).upper()
    if opening is None or current is None or signal not in {"HOME", "AWAY"}:
        return None
    home_move = opening - current
    return home_move if signal == "HOME" else -home_move
