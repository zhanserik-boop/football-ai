import csv
from collections import defaultdict, deque


# =========================================================
# SETTINGS
# =========================================================

SEASONS = [2022, 2023, 2024, 2025]

WINDOW = 10
MIN_HISTORY = 3

LINEUPS_FILE = "epl_lineups_4seasons.csv"

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

        if value in (
            "",
            None,
            "None"
        ):
            return None

        return float(value)

    except:
        return None


def clipped(
    value,
    low=0.0,
    high=1.0
):

    return max(
        low,
        min(
            value,
            high
        )
    )


# =========================================================
# ENGINE
# =========================================================

class LiveLineupEngine:

    def __init__(self):

        # =============================================
        # Historical fixture lineups
        # =============================================

        self.lineups = defaultdict(
            lambda: defaultdict(
                lambda: {
                    "starters": {},
                    "subs": {}
                }
            )
        )


        # =============================================
        # Historical player-match stats
        # =============================================

        self.player_stats = defaultdict(
            lambda: defaultdict(dict)
        )


        # =============================================
        # Rolling state
        # =============================================

        self.team_games = defaultdict(int)

        self.known_players = defaultdict(set)


        self.starter_history = defaultdict(
            lambda: deque(
                maxlen=WINDOW
            )
        )


        self.minutes_history = defaultdict(
            lambda: deque(
                maxlen=WINDOW
            )
        )


        self.rating_history = defaultdict(
            lambda: deque(
                maxlen=WINDOW
            )
        )


        self.history_matches = []


        print(
            "\n=============================================="
        )

        print(
            "LIVE LINEUP ENGINE — INITIALIZING"
        )

        print(
            "=============================================="
        )


        self.load_historical_lineups()

        self.load_player_stats()

        self.build_match_order()

        self.replay_history()


        print(
            "\n=============================================="
        )

        print(
            "LIVE LINEUP ENGINE READY"
        )

        print(
            "=============================================="
        )

        print(
            "Historical matches replayed:",
            len(
                self.history_matches
            )
        )

        print(
            "Teams in state:",
            len(
                self.team_games
            )
        )

        print(
            "Known team/player pairs:",
            sum(
                len(players)
                for players
                in self.known_players.values()
            )
        )

        print(
            "Window:",
            WINDOW
        )

        print(
            "=============================================="
        )


    # =====================================================
    # LOAD HISTORICAL LINEUPS
    # =====================================================

    def load_historical_lineups(
        self
    ):

        seen = set()


        with open(
            LINEUPS_FILE,
            encoding="utf-8-sig"
        ) as file:

            reader = csv.DictReader(
                file
            )


            for row in reader:

                try:

                    fixture_id = int(
                        row[
                            "fixture_id"
                        ]
                    )

                    season = int(
                        row[
                            "season"
                        ]
                    )

                    team = (
                        row[
                            "team"
                        ].strip()
                    )

                    player_id = str(
                        row[
                            "player_id"
                        ]
                    ).strip()

                    starter = int(
                        row[
                            "starter"
                        ]
                    )

                except:
                    continue


                if not player_id:
                    continue


                key = (
                    fixture_id,
                    team,
                    player_id,
                    starter
                )


                if key in seen:
                    continue


                seen.add(
                    key
                )


                info = {

                    "player_id":
                        player_id,

                    "player":
                        row.get(
                            "player",
                            ""
                        ),

                    "position":
                        row.get(
                            "position",
                            ""
                        )
                }


                if starter == 1:

                    self.lineups[
                        fixture_id
                    ][team][
                        "starters"
                    ][
                        player_id
                    ] = info


                else:

                    self.lineups[
                        fixture_id
                    ][team][
                        "subs"
                    ][
                        player_id
                    ] = info


        print(
            "Historical fixtures with lineups:",
            len(
                self.lineups
            )
        )


    # =====================================================
    # LOAD PLAYER MATCH STATS
    # =====================================================

    def load_player_stats(
        self
    ):

        records = 0


        for season in SEASONS:

            filename = (
                PLAYER_FILES[
                    season
                ]
            )


            with open(
                filename,
                encoding="utf-8-sig"
            ) as file:

                reader = csv.DictReader(
                    file
                )


                for row in reader:

                    try:

                        fixture_id = int(
                            row[
                                "fixture_id"
                            ]
                        )

                        team = (
                            row[
                                "team"
                            ].strip()
                        )

                        player_id = str(
                            row[
                                "player_id"
                            ]
                        ).strip()

                    except:
                        continue


                    if not player_id:
                        continue


                    minutes = safe_float(
                        row.get(
                            "minutes"
                        )
                    )


                    rating = safe_float(
                        row.get(
                            "rating"
                        )
                    )


                    self.player_stats[
                        fixture_id
                    ][team][
                        player_id
                    ] = {

                        "minutes":
                            (
                                0.0
                                if minutes is None
                                else minutes
                            ),

                        "rating":
                            rating
                    }


                    records += 1


        print(
            "Historical player-match records:",
            records
        )


    # =====================================================
    # BUILD HISTORICAL MATCH ORDER
    # =====================================================

    def build_match_order(
        self
    ):

        matches = {}


        with open(
            LINEUPS_FILE,
            encoding="utf-8-sig"
        ) as file:

            reader = csv.DictReader(
                file
            )


            for row in reader:

                try:

                    fixture_id = int(
                        row[
                            "fixture_id"
                        ]
                    )

                    season = int(
                        row[
                            "season"
                        ]
                    )

                    date = (
                        row[
                            "date"
                        ][:10]
                    )

                    home = (
                        row[
                            "match_home"
                        ].strip()
                    )

                    away = (
                        row[
                            "match_away"
                        ].strip()
                    )

                except:
                    continue


                matches[
                    fixture_id
                ] = {

                    "season":
                        season,

                    "fixture_id":
                        fixture_id,

                    "date":
                        date,

                    "home":
                        home,

                    "away":
                        away
                }


        self.history_matches = sorted(

            matches.values(),

            key=lambda x: (
                x["date"],
                x["fixture_id"]
            )
        )


    # =====================================================
    # PLAYER SCORE
    #
    # THIS MATCHES build_lineup_strength.py
    # =====================================================

    def get_player_score(
        self,
        team,
        player_id
    ):

        player_id = str(
            player_id
        )


        key = (
            team,
            player_id
        )


        starts = (
            self.starter_history[
                key
            ]
        )


        minutes = (
            self.minutes_history[
                key
            ]
        )


        ratings = (
            self.rating_history[
                key
            ]
        )


        n = len(
            starts
        )


        if n == 0:

            return {

                "score":
                    0.0,

                "start_rate":
                    0.0,

                "minute_rate":
                    0.0,

                "rating_score":
                    0.0,

                "avg_rating":
                    None,

                "history_games":
                    0
            }


        # =============================================
        # START RATE
        # =============================================

        start_rate = (
            sum(
                starts
            )
            / n
        )


        # =============================================
        # MINUTES
        # =============================================

        minute_rate = (
            sum(
                minutes
            )
            /
            (
                90.0
                * n
            )
        )


        minute_rate = clipped(
            minute_rate
        )


        # =============================================
        # RATING
        # =============================================

        valid_ratings = [

            rating

            for rating
            in ratings

            if rating is not None

        ]


        if valid_ratings:

            avg_rating = (
                sum(
                    valid_ratings
                )
                /
                len(
                    valid_ratings
                )
            )


            rating_score = clipped(

                (
                    avg_rating
                    - 6.0
                )
                / 2.0

            )


        else:

            avg_rating = None

            rating_score = 0.25


        # =============================================
        # EXACT HISTORICAL WEIGHTS
        #
        # Start regularity: 50%
        # Minutes:            35%
        # Rating:             15%
        # =============================================

        score = (

            0.50
            * start_rate

            +

            0.35
            * minute_rate

            +

            0.15
            * rating_score

        )


        return {

            "score":
                score,

            "start_rate":
                start_rate,

            "minute_rate":
                minute_rate,

            "rating_score":
                rating_score,

            "avg_rating":
                avg_rating,

            "history_games":
                n
        }


    # =====================================================
    # UPDATE ONE HISTORICAL MATCH
    #
    # SAME LOGIC AS HISTORICAL BUILDER
    # =====================================================

    def update_historical_team(
        self,
        fixture_id,
        team
    ):

        lineup = (
            self.lineups[
                fixture_id
            ].get(
                team,
                {}
            )
        )


        starters = (
            lineup.get(
                "starters",
                {}
            )
        )


        subs = (
            lineup.get(
                "subs",
                {}
            )
        )


        current_players = set(
            starters.keys()
        )


        current_players |= set(
            subs.keys()
        )


        current_players |= set(

            self.player_stats[
                fixture_id
            ].get(
                team,
                {}
            ).keys()

        )


        # =============================================
        # Add newly seen players
        # =============================================

        self.known_players[
            team
        ].update(
            current_players
        )


        # =============================================
        # EVERY KNOWN PLAYER receives one observation
        #
        # If absent:
        # starter = 0
        # minutes = 0
        #
        # This is exactly what makes injured/transferred
        # players decay through the rolling 10 games.
        # =============================================

        for player_id in list(
            self.known_players[
                team
            ]
        ):

            key = (
                team,
                player_id
            )


            starter_flag = (

                1

                if (
                    player_id
                    in starters
                )

                else 0

            )


            stat = (

                self.player_stats[
                    fixture_id
                ]
                .get(
                    team,
                    {}
                )
                .get(
                    player_id
                )

            )


            if stat is None:

                minutes = 0.0

                rating = None


            else:

                minutes = (
                    stat[
                        "minutes"
                    ]
                )

                rating = (
                    stat[
                        "rating"
                    ]
                )


            self.starter_history[
                key
            ].append(
                starter_flag
            )


            self.minutes_history[
                key
            ].append(
                minutes
            )


            self.rating_history[
                key
            ].append(
                rating
            )


        self.team_games[
            team
        ] += 1


    # =====================================================
    # REPLAY 4 SEASONS
    # =====================================================

    def replay_history(
        self
    ):

        for match in (
            self.history_matches
        ):

            fixture_id = (
                match[
                    "fixture_id"
                ]
            )


            home = (
                match[
                    "home"
                ]
            )


            away = (
                match[
                    "away"
                ]
            )


            self.update_historical_team(
                fixture_id,
                home
            )


            self.update_historical_team(
                fixture_id,
                away
            )


    # =====================================================
    # EXPECTED BEST XI
    # =====================================================

    def get_expected_xi(
        self,
        team
    ):

        candidates = []


        for player_id in (
            self.known_players[
                team
            ]
        ):

            info = (
                self.get_player_score(
                    team,
                    player_id
                )
            )


            candidates.append({

                "player_id":
                    player_id,

                "score":
                    info[
                        "score"
                    ],

                "start_rate":
                    info[
                        "start_rate"
                    ],

                "minute_rate":
                    info[
                        "minute_rate"
                    ],

                "history_games":
                    info[
                        "history_games"
                    ],
            })


        candidates.sort(

            key=lambda x:
                x["score"],

            reverse=True
        )


        return candidates[:11]


    # =====================================================
    # CALCULATE ONE LIVE TEAM
    # =====================================================

    def calculate_live_team(
        self,
        team,
        starters
    ):

        """
        starters:

        list of dictionaries from API, e.g.

        [
            {
                "player_id": "123",
                "player": "Player Name"
            }
        ]
        """


        actual_strength = 0.0

        known_starters = 0

        new_starters = 0

        regular_starters = 0

        player_details = []


        starter_ids = set()


        for player in starters:

            player_id = str(
                player[
                    "player_id"
                ]
            )


            starter_ids.add(
                player_id
            )


            info = (
                self.get_player_score(
                    team,
                    player_id
                )
            )


            actual_strength += (
                info[
                    "score"
                ]
            )


            if (
                info[
                    "history_games"
                ]
                >= MIN_HISTORY
            ):

                known_starters += 1


            else:

                new_starters += 1


            if (
                info[
                    "start_rate"
                ]
                >= 0.60
            ):

                regular_starters += 1


            player_details.append({

                "player_id":
                    player_id,

                "player":
                    player.get(
                        "player",
                        ""
                    ),

                "score":
                    info[
                        "score"
                    ],

                "start_rate":
                    info[
                        "start_rate"
                    ],

                "minute_rate":
                    info[
                        "minute_rate"
                    ],

                "rating":
                    info[
                        "avg_rating"
                    ],

                "history_games":
                    info[
                        "history_games"
                    ],
            })


        # =============================================
        # Expected best XI
        # =============================================

        expected_xi = (
            self.get_expected_xi(
                team
            )
        )


        expected_strength = sum(

            x[
                "score"
            ]

            for x
            in expected_xi

        )


        # =============================================
        # LINEUP SHOCK
        #
        # Negative:
        # actual XI weaker than expected XI
        # =============================================

        lineup_shock = (

            actual_strength
            - expected_strength

        )


        # =============================================
        # MISSING REGULAR PLAYERS
        # =============================================

        missing_regular = 0


        for player_id in (
            self.known_players[
                team
            ]
        ):

            info = (
                self.get_player_score(
                    team,
                    player_id
                )
            )


            if (
                info[
                    "start_rate"
                ]
                < 0.60
            ):

                continue


            if (
                player_id
                not in starter_ids
            ):

                missing_regular += 1


        # =============================================
        # CONTINUITY WITH PREVIOUS MATCH
        # =============================================

        continuity_values = []


        for player_id in (
            starter_ids
        ):

            hist = (
                self.starter_history[
                    (
                        team,
                        player_id
                    )
                ]
            )


            if hist:

                continuity_values.append(
                    hist[-1]
                )


        if continuity_values:

            continuity = (

                sum(
                    continuity_values
                )
                /
                len(
                    starter_ids
                )

            )


        else:

            continuity = 0.0


        # =============================================
        # DATA COVERAGE
        # =============================================

        starter_count = len(
            starter_ids
        )


        if starter_count > 0:

            coverage = (
                known_starters
                / starter_count
            )


        else:

            coverage = 0.0


        return {

            "team":
                team,

            "starter_count":
                starter_count,

            "known_starters":
                known_starters,

            "new_starters":
                new_starters,

            "coverage":
                coverage,

            "regular_starters":
                regular_starters,

            "actual_strength":
                actual_strength,

            "expected_strength":
                expected_strength,

            "lineup_shock":
                lineup_shock,

            "missing_regular":
                missing_regular,

            "continuity":
                continuity,

            "players":
                player_details,

            "expected_xi":
                expected_xi,
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
        threshold=1.5
    ):

        home = (
            self.calculate_live_team(
                home_team,
                home_starters
            )
        )


        away = (
            self.calculate_live_team(
                away_team,
                away_starters
            )
        )


        shock_diff = (

            home[
                "lineup_shock"
            ]

            -

            away[
                "lineup_shock"
            ]

        )


        # =============================================
        # OUR FROZEN HISTORICAL SIGNAL
        # =============================================

        if shock_diff >= threshold:

            signal = "HOME"


        elif shock_diff <= -threshold:

            signal = "AWAY"


        else:

            signal = "NO SIGNAL"


        # =============================================
        # Coverage warning
        #
        # IMPORTANT:
        # This DOES NOT change the raw signal.
        #
        # It only tells us whether historical EPL data
        # knows the current players well.
        # =============================================

        min_coverage = min(

            home[
                "coverage"
            ],

            away[
                "coverage"
            ]

        )


        if min_coverage >= 0.80:

            data_quality = (
                "HIGH"
            )


        elif min_coverage >= 0.60:

            data_quality = (
                "MEDIUM"
            )


        else:

            data_quality = (
                "LOW"
            )


        return {

            "home_team":
                home_team,

            "away_team":
                away_team,

            "home":
                home,

            "away":
                away,

            "shock_diff":
                shock_diff,

            "abs_shock":
                abs(
                    shock_diff
                ),

            "threshold":
                threshold,

            "signal":
                signal,

            "data_quality":
                data_quality,

            "minimum_coverage":
                min_coverage,
        }


    # =====================================================
    # API-FOOTBALL LINEUPS -> ENGINE FORMAT
    # =====================================================

    def calculate_from_api_response(
        self,
        home_team,
        away_team,
        api_lineups,
        threshold=1.5
    ):

        home_starters = []

        away_starters = []


        for team_data in (
            api_lineups
        ):

            team_name = (

                team_data
                .get(
                    "team",
                    {}
                )
                .get(
                    "name"
                )

            )


            starters = []


            for item in (
                team_data.get(
                    "startXI",
                    []
                )
            ):

                player = (
                    item.get(
                        "player",
                        {}
                    )
                )


                player_id = (
                    player.get(
                        "id"
                    )
                )


                if player_id is None:
                    continue


                starters.append({

                    "player_id":
                        str(
                            player_id
                        ),

                    "player":
                        player.get(
                            "name",
                            ""
                        ),

                    "position":
                        player.get(
                            "pos",
                            ""
                        ),
                })


            if team_name == home_team:

                home_starters = (
                    starters
                )


            elif team_name == away_team:

                away_starters = (
                    starters
                )


        if (
            len(
                home_starters
            ) != 11
            or
            len(
                away_starters
            ) != 11
        ):

            raise ValueError(

                "Confirmed starting XI incomplete: "
                f"{home_team} "
                f"{len(home_starters)} players, "
                f"{away_team} "
                f"{len(away_starters)} players"

            )


        return self.calculate_match(

            home_team=
                home_team,

            away_team=
                away_team,

            home_starters=
                home_starters,

            away_starters=
                away_starters,

            threshold=
                threshold

        )


    # =====================================================
    # PRETTY PRINT
    # =====================================================

    def print_result(
        self,
        result
    ):

        home = (
            result[
                "home"
            ]
        )

        away = (
            result[
                "away"
            ]
        )


        print(
            "\n=============================================="
        )

        print(
            "LIVE LINEUP SHOCK"
        )

        print(
            "=============================================="
        )


        print(
            result[
                "home_team"
            ],
            "-",
            result[
                "away_team"
            ]
        )


        print()


        print(
            f"{result['home_team']:25} | "
            f"Strength "
            f"{home['actual_strength']:.2f} | "
            f"Expected "
            f"{home['expected_strength']:.2f} | "
            f"Shock "
            f"{home['lineup_shock']:+.2f}"
        )


        print(
            f"{result['away_team']:25} | "
            f"Strength "
            f"{away['actual_strength']:.2f} | "
            f"Expected "
            f"{away['expected_strength']:.2f} | "
            f"Shock "
            f"{away['lineup_shock']:+.2f}"
        )


        print()


        print(
            "ShockDiff:",
            f"{result['shock_diff']:+.2f}"
        )


        print(
            "Threshold:",
            f"{result['threshold']:.2f}"
        )


        print(
            "SIGNAL:",
            result[
                "signal"
            ]
        )


        print()


        print(
            "Coverage:"
        )


        print(
            f"{result['home_team']}: "
            f"{home['known_starters']}/"
            f"{home['starter_count']} "
            f"({home['coverage']*100:.1f}%)"
        )


        print(
            f"{result['away_team']}: "
            f"{away['known_starters']}/"
            f"{away['starter_count']} "
            f"({away['coverage']*100:.1f}%)"
        )


        print(
            "Data quality:",
            result[
                "data_quality"
            ]
        )


        print(
            "\n=============================================="
        )


# =========================================================
# STANDALONE TEST
# =========================================================

if __name__ == "__main__":

    engine = LiveLineupEngine()


    print(
        "\nEngine initialized successfully."
    )


    print(
        "\nTeams with most historical matches:"
    )


    teams = sorted(

        engine.team_games.items(),

        key=lambda x:
            x[1],

        reverse=True

    )


    for (
        team,
        games
    ) in teams[:20]:

        expected = (
            engine.get_expected_xi(
                team
            )
        )


        expected_strength = sum(

            x[
                "score"
            ]

            for x
            in expected

        )


        print(

            f"{team:25} | "

            f"Games "
            f"{games:3d} | "

            f"Known players "
            f"{len(engine.known_players[team]):3d} | "

            f"Expected XI strength "
            f"{expected_strength:.2f}"

        )


    print(
        "\n=============================================="
    )

    print(
        "READY FOR market_monitor_v2.py"
    )

    print(
        "=============================================="
    )