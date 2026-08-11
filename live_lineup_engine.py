import csv
import os
from collections import defaultdict, deque


# =========================================================
# SETTINGS
# =========================================================

SEASONS = [2022, 2023, 2024, 2025]
WINDOW = 10
MIN_HISTORY = 3

LINEUPS_FILE = "epl_lineups_4seasons.csv"
CURRENT_SQUADS_FILE = "current_squads_2026.csv"

PLAYER_FILES = {
    2022: "player_match_history_2022.csv",
    2023: "player_match_history_2023.csv",
    2024: "player_match_history_2024.csv",
    2025: "player_match_history_2025.csv",
}


# =========================================================
# HELPERS
# =========================================================

def safe_float(value):
    try:
        if value in ("", None, "None"):
            return None
        return float(value)
    except Exception:
        return None


def clipped(value, low=0.0, high=1.0):
    return max(low, min(value, high))


# =========================================================
# ENGINE
# =========================================================

class LiveLineupEngine:

    def __init__(self):
        self.lineups = defaultdict(
            lambda: defaultdict(
                lambda: {"starters": {}, "subs": {}}
            )
        )

        self.player_stats = defaultdict(
            lambda: defaultdict(dict)
        )

        self.team_games = defaultdict(int)
        self.known_players = defaultdict(set)

        self.starter_history = defaultdict(
            lambda: deque(maxlen=WINDOW)
        )
        self.minutes_history = defaultdict(
            lambda: deque(maxlen=WINDOW)
        )
        self.rating_history = defaultdict(
            lambda: deque(maxlen=WINDOW)
        )

        self.history_matches = []

        # Current 2026/27 eligibility layer.
        # Historical data determines strength; this file determines
        # whether the player still belongs to the club now.
        self.current_squads = defaultdict(set)
        self.current_squad_info = defaultdict(dict)
        self.current_squad_snapshot_utc = None

        print("\n==============================================")
        print("LIVE LINEUP ENGINE — INITIALIZING")
        print("==============================================")

        self.load_historical_lineups()
        self.load_player_stats()
        self.build_match_order()
        self.replay_history()
        self.load_current_squads()

        print("\n==============================================")
        print("LIVE LINEUP ENGINE READY")
        print("==============================================")
        print("Historical matches replayed:", len(self.history_matches))
        print("Teams in state:", len(self.team_games))
        print(
            "Known team/player pairs:",
            sum(len(players) for players in self.known_players.values())
        )
        print("Current squad teams:", len(self.current_squads))
        print(
            "Current squad players:",
            sum(len(players) for players in self.current_squads.values())
        )
        print("Squad snapshot UTC:", self.current_squad_snapshot_utc)
        print("Window:", WINDOW)
        print("==============================================")

    # =====================================================
    # LOAD HISTORICAL LINEUPS
    # =====================================================

    def load_historical_lineups(self):
        seen = set()

        with open(LINEUPS_FILE, encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)

            for row in reader:
                try:
                    fixture_id = int(row["fixture_id"])
                    team = row["team"].strip()
                    player_id = str(row["player_id"]).strip()
                    starter = int(row["starter"])
                except Exception:
                    continue

                if not player_id:
                    continue

                key = (fixture_id, team, player_id, starter)
                if key in seen:
                    continue
                seen.add(key)

                info = {
                    "player_id": player_id,
                    "player": row.get("player", ""),
                    "position": row.get("position", ""),
                }

                bucket = "starters" if starter == 1 else "subs"
                self.lineups[fixture_id][team][bucket][player_id] = info

        print("Historical fixtures with lineups:", len(self.lineups))

    # =====================================================
    # LOAD PLAYER MATCH STATS
    # =====================================================

    def load_player_stats(self):
        records = 0

        for season in SEASONS:
            filename = PLAYER_FILES[season]

            with open(filename, encoding="utf-8-sig") as file:
                reader = csv.DictReader(file)

                for row in reader:
                    try:
                        fixture_id = int(row["fixture_id"])
                        team = row["team"].strip()
                        player_id = str(row["player_id"]).strip()
                    except Exception:
                        continue

                    if not player_id:
                        continue

                    minutes = safe_float(row.get("minutes"))
                    rating = safe_float(row.get("rating"))

                    self.player_stats[fixture_id][team][player_id] = {
                        "minutes": 0.0 if minutes is None else minutes,
                        "rating": rating,
                    }
                    records += 1

        print("Historical player-match records:", records)

    # =====================================================
    # BUILD HISTORICAL MATCH ORDER
    # =====================================================

    def build_match_order(self):
        matches = {}

        with open(LINEUPS_FILE, encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)

            for row in reader:
                try:
                    fixture_id = int(row["fixture_id"])
                    season = int(row["season"])
                    date = row["date"][:10]
                    home = row["match_home"].strip()
                    away = row["match_away"].strip()
                except Exception:
                    continue

                matches[fixture_id] = {
                    "season": season,
                    "fixture_id": fixture_id,
                    "date": date,
                    "home": home,
                    "away": away,
                }

        self.history_matches = sorted(
            matches.values(),
            key=lambda x: (x["date"], x["fixture_id"]),
        )

    # =====================================================
    # PLAYER SCORE — FROZEN HISTORICAL FORMULA
    # =====================================================

    def get_player_score(self, team, player_id):
        player_id = str(player_id)
        key = (team, player_id)

        starts = self.starter_history[key]
        minutes = self.minutes_history[key]
        ratings = self.rating_history[key]
        n = len(starts)

        if n == 0:
            return {
                "score": 0.0,
                "start_rate": 0.0,
                "minute_rate": 0.0,
                "rating_score": 0.0,
                "avg_rating": None,
                "history_games": 0,
            }

        start_rate = sum(starts) / n
        minute_rate = clipped(sum(minutes) / (90.0 * n))

        valid_ratings = [x for x in ratings if x is not None]
        if valid_ratings:
            avg_rating = sum(valid_ratings) / len(valid_ratings)
            rating_score = clipped((avg_rating - 6.0) / 2.0)
        else:
            avg_rating = None
            rating_score = 0.25

        # Frozen weights: starts 50%, minutes 35%, rating 15%.
        score = (
            0.50 * start_rate
            + 0.35 * minute_rate
            + 0.15 * rating_score
        )

        return {
            "score": score,
            "start_rate": start_rate,
            "minute_rate": minute_rate,
            "rating_score": rating_score,
            "avg_rating": avg_rating,
            "history_games": n,
        }

    # =====================================================
    # HISTORICAL REPLAY
    # =====================================================

    def update_historical_team(self, fixture_id, team):
        lineup = self.lineups[fixture_id].get(team, {})
        starters = lineup.get("starters", {})
        subs = lineup.get("subs", {})

        current_players = set(starters.keys())
        current_players |= set(subs.keys())
        current_players |= set(
            self.player_stats[fixture_id].get(team, {}).keys()
        )

        self.known_players[team].update(current_players)

        for player_id in list(self.known_players[team]):
            key = (team, player_id)
            starter_flag = 1 if player_id in starters else 0

            stat = (
                self.player_stats[fixture_id]
                .get(team, {})
                .get(player_id)
            )

            if stat is None:
                minutes = 0.0
                rating = None
            else:
                minutes = stat["minutes"]
                rating = stat["rating"]

            self.starter_history[key].append(starter_flag)
            self.minutes_history[key].append(minutes)
            self.rating_history[key].append(rating)

        self.team_games[team] += 1

    def replay_history(self):
        for match in self.history_matches:
            fixture_id = match["fixture_id"]
            self.update_historical_team(fixture_id, match["home"])
            self.update_historical_team(fixture_id, match["away"])

    # =====================================================
    # CURRENT SQUADS
    # =====================================================

    def load_current_squads(self):
        if not os.path.exists(CURRENT_SQUADS_FILE):
            raise FileNotFoundError(
                f"{CURRENT_SQUADS_FILE} not found. "
                "Run update_squads_transfers.py before starting the live system."
            )

        rows = 0

        with open(CURRENT_SQUADS_FILE, encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)

            required = {"team_name", "player_id", "player_name"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise RuntimeError(
                    "Current squad file missing columns: "
                    + ", ".join(sorted(missing))
                )

            for row in reader:
                team = str(row.get("team_name", "")).strip()
                player_id = str(row.get("player_id", "")).strip()
                player_name = str(row.get("player_name", "")).strip()

                if not team or not player_id:
                    continue

                self.current_squads[team].add(player_id)
                self.current_squad_info[team][player_id] = {
                    "player_id": player_id,
                    "player": player_name,
                    "position": str(row.get("position", "")).strip(),
                }

                snapshot = str(row.get("snapshot_utc", "")).strip()
                if snapshot:
                    self.current_squad_snapshot_utc = snapshot

                rows += 1

        if len(self.current_squads) != 20:
            raise RuntimeError(
                f"Current squad validation failed: expected 20 EPL teams, "
                f"got {len(self.current_squads)}"
            )

        thin_teams = {
            team: len(players)
            for team, players in self.current_squads.items()
            if len(players) < 15
        }
        if thin_teams:
            raise RuntimeError(
                "Current squad validation failed; too few players: "
                + repr(thin_teams)
            )

        print("Current squad records loaded:", rows)

    def is_current_player(self, team, player_id):
        return str(player_id) in self.current_squads.get(team, set())

    # =====================================================
    # LIVE PLAYER SCORE
    # =====================================================

    def get_live_player_score(self, team, player_id):
        """
        Preserve the frozen team-specific historical score when available.

        For a current player who transferred from another EPL club, reuse
        his existing EPL history by persistent API player_id instead of
        treating him as a zero-strength new player.

        If no usable EPL history exists anywhere, keep score at zero and
        mark the player UNKNOWN. Coverage/data_quality then protects the
        downstream betting decision from this uncertainty.
        """
        player_id = str(player_id)

        own = self.get_player_score(team, player_id)
        if own["history_games"] >= MIN_HISTORY:
            out = dict(own)
            out["history_source"] = team
            out["player_status"] = "KNOWN"
            return out

        cross_team = []
        for historical_team in self.known_players:
            if historical_team == team:
                continue
            if player_id not in self.known_players[historical_team]:
                continue

            info = self.get_player_score(historical_team, player_id)
            if info["history_games"] >= MIN_HISTORY:
                cross_team.append((historical_team, info))

        if cross_team:
            # Prefer the strongest available evidence: most observations,
            # then score as deterministic tie-break.
            historical_team, info = max(
                cross_team,
                key=lambda x: (
                    x[1]["history_games"],
                    x[1]["score"],
                ),
            )
            out = dict(info)
            out["history_source"] = historical_team
            out["player_status"] = "TRANSFERRED_EPL_HISTORY"
            return out

        out = dict(own)
        out["history_source"] = None
        out["player_status"] = "NEW_UNKNOWN"
        return out

    # =====================================================
    # EXPECTED BEST XI — CURRENT-SQUAD ELIGIBILITY
    # =====================================================

    def get_expected_xi(self, team):
        if team not in self.current_squads:
            raise ValueError(
                f"Team {team!r} not found in current 2026/27 squad snapshot"
            )

        candidates = []

        # Critical change: only CURRENT club members are eligible.
        # A transferred-out historical star can no longer inflate the
        # expected XI and create a false negative lineup shock.
        for player_id in self.current_squads[team]:
            info = self.get_live_player_score(team, player_id)
            player_meta = self.current_squad_info[team].get(player_id, {})

            candidates.append({
                "player_id": player_id,
                "player": player_meta.get("player", ""),
                "position": player_meta.get("position", ""),
                "score": info["score"],
                "start_rate": info["start_rate"],
                "minute_rate": info["minute_rate"],
                "history_games": info["history_games"],
                "history_source": info["history_source"],
                "player_status": info["player_status"],
            })

        candidates.sort(
            key=lambda x: (
                x["score"],
                x["history_games"],
                x["player_id"],
            ),
            reverse=True,
        )

        return candidates[:11]

    # =====================================================
    # CALCULATE ONE LIVE TEAM
    # =====================================================

    def calculate_live_team(self, team, starters):
        actual_strength = 0.0
        known_starters = 0
        new_starters = 0
        regular_starters = 0
        player_details = []
        starter_ids = set()

        for player in starters:
            player_id = str(player["player_id"])
            starter_ids.add(player_id)

            info = self.get_live_player_score(team, player_id)
            actual_strength += info["score"]

            if info["history_games"] >= MIN_HISTORY:
                known_starters += 1
            else:
                new_starters += 1

            if info["start_rate"] >= 0.60:
                regular_starters += 1

            player_details.append({
                "player_id": player_id,
                "player": player.get("player", ""),
                "score": info["score"],
                "start_rate": info["start_rate"],
                "minute_rate": info["minute_rate"],
                "rating": info["avg_rating"],
                "history_games": info["history_games"],
                "history_source": info["history_source"],
                "player_status": info["player_status"],
                "current_squad_member": self.is_current_player(
                    team, player_id
                ),
            })

        expected_xi = self.get_expected_xi(team)
        expected_strength = sum(x["score"] for x in expected_xi)

        # Frozen Lineup Shock arithmetic is unchanged.
        lineup_shock = actual_strength - expected_strength

        # Only current squad members can count as missing regulars.
        missing_regular = 0
        for player_id in self.current_squads[team]:
            info = self.get_live_player_score(team, player_id)
            if info["start_rate"] < 0.60:
                continue
            if player_id not in starter_ids:
                missing_regular += 1

        continuity_values = []
        for player_id in starter_ids:
            hist = self.starter_history[(team, player_id)]
            if hist:
                continuity_values.append(hist[-1])

        continuity = (
            sum(continuity_values) / len(starter_ids)
            if continuity_values
            else 0.0
        )

        starter_count = len(starter_ids)
        coverage = (
            known_starters / starter_count
            if starter_count > 0
            else 0.0
        )

        unknown_starters = [
            x for x in player_details
            if x["player_status"] == "NEW_UNKNOWN"
        ]
        non_current_starters = [
            x for x in player_details
            if not x["current_squad_member"]
        ]

        return {
            "team": team,
            "starter_count": starter_count,
            "known_starters": known_starters,
            "new_starters": new_starters,
            "unknown_starters": len(unknown_starters),
            "non_current_starters": len(non_current_starters),
            "coverage": coverage,
            "regular_starters": regular_starters,
            "actual_strength": actual_strength,
            "expected_strength": expected_strength,
            "lineup_shock": lineup_shock,
            "missing_regular": missing_regular,
            "continuity": continuity,
            "players": player_details,
            "expected_xi": expected_xi,
        }

    # =====================================================
    # CALCULATE FULL LIVE MATCH
    # =====================================================

    def calculate_match(
        self,
        home_team,
        away_team,
        home_starters,
        away_starters,
        threshold=1.5,
    ):
        home = self.calculate_live_team(home_team, home_starters)
        away = self.calculate_live_team(away_team, away_starters)

        shock_diff = home["lineup_shock"] - away["lineup_shock"]

        # Frozen signal threshold is unchanged.
        if shock_diff >= threshold:
            signal = "HOME"
        elif shock_diff <= -threshold:
            signal = "AWAY"
        else:
            signal = "NO SIGNAL"

        min_coverage = min(home["coverage"], away["coverage"])

        if min_coverage >= 0.80:
            data_quality = "HIGH"
        elif min_coverage >= 0.60:
            data_quality = "MEDIUM"
        else:
            data_quality = "LOW"

        # An API XI containing a player who is not in the fresh squad file
        # is treated conservatively. Keep raw signal for audit, but force
        # LOW quality so downstream AH Agent cannot treat it as a clean bet.
        if home["non_current_starters"] or away["non_current_starters"]:
            data_quality = "LOW"

        return {
            "home_team": home_team,
            "away_team": away_team,
            "home": home,
            "away": away,
            "shock_diff": shock_diff,
            "abs_shock": abs(shock_diff),
            "threshold": threshold,
            "signal": signal,
            "data_quality": data_quality,
            "minimum_coverage": min_coverage,
        }

    # =====================================================
    # API-FOOTBALL LINEUPS -> ENGINE FORMAT
    # =====================================================

    def calculate_from_api_response(
        self,
        home_team,
        away_team,
        api_lineups,
        threshold=1.5,
    ):
        home_starters = []
        away_starters = []

        for team_data in api_lineups:
            team_name = team_data.get("team", {}).get("name")
            starters = []

            for item in team_data.get("startXI", []):
                player = item.get("player", {})
                player_id = player.get("id")
                if player_id is None:
                    continue

                starters.append({
                    "player_id": str(player_id),
                    "player": player.get("name", ""),
                    "position": player.get("pos", ""),
                })

            if team_name == home_team:
                home_starters = starters
            elif team_name == away_team:
                away_starters = starters

        if len(home_starters) != 11 or len(away_starters) != 11:
            raise ValueError(
                "Confirmed starting XI incomplete: "
                f"{home_team} {len(home_starters)} players, "
                f"{away_team} {len(away_starters)} players"
            )

        return self.calculate_match(
            home_team=home_team,
            away_team=away_team,
            home_starters=home_starters,
            away_starters=away_starters,
            threshold=threshold,
        )

    # =====================================================
    # PRETTY PRINT
    # =====================================================

    def print_result(self, result):
        home = result["home"]
        away = result["away"]

        print("\n==============================================")
        print("LIVE LINEUP SHOCK")
        print("==============================================")
        print(result["home_team"], "-", result["away_team"])
        print()
        print(
            f"{result['home_team']:25} | "
            f"Strength {home['actual_strength']:.2f} | "
            f"Expected {home['expected_strength']:.2f} | "
            f"Shock {home['lineup_shock']:+.2f}"
        )
        print(
            f"{result['away_team']:25} | "
            f"Strength {away['actual_strength']:.2f} | "
            f"Expected {away['expected_strength']:.2f} | "
            f"Shock {away['lineup_shock']:+.2f}"
        )
        print()
        print("ShockDiff:", f"{result['shock_diff']:+.2f}")
        print("Threshold:", f"{result['threshold']:.2f}")
        print("SIGNAL:", result["signal"])
        print()
        print("Coverage:")
        print(
            f"{result['home_team']}: "
            f"{home['known_starters']}/{home['starter_count']} "
            f"({home['coverage']*100:.1f}%) | "
            f"unknown={home['unknown_starters']} | "
            f"not-current={home['non_current_starters']}"
        )
        print(
            f"{result['away_team']}: "
            f"{away['known_starters']}/{away['starter_count']} "
            f"({away['coverage']*100:.1f}%) | "
            f"unknown={away['unknown_starters']} | "
            f"not-current={away['non_current_starters']}"
        )
        print("Data quality:", result["data_quality"])
        print("\n==============================================")


# =========================================================
# STANDALONE TEST
# =========================================================

if __name__ == "__main__":
    engine = LiveLineupEngine()

    print("\nEngine initialized successfully.")
    print("\nCurrent 2026/27 EPL squads:")

    teams = sorted(
        engine.current_squads.items(),
        key=lambda x: x[0],
    )

    for team, players in teams:
        expected = engine.get_expected_xi(team)
        expected_strength = sum(x["score"] for x in expected)
        known_expected = sum(
            1 for x in expected
            if x["history_games"] >= MIN_HISTORY
        )
        unknown_expected = sum(
            1 for x in expected
            if x["player_status"] == "NEW_UNKNOWN"
        )

        print(
            f"{team:25} | "
            f"Squad {len(players):2d} | "
            f"Expected known {known_expected:2d}/11 | "
            f"Unknown {unknown_expected:2d} | "
            f"Expected XI strength {expected_strength:.2f}"
        )

    print("\n==============================================")
    print("READY FOR market_monitor_v2.py")
    print("==============================================")
