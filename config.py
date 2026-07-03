"""SportsReminder configuration constants."""

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edit these before first run
# ─────────────────────────────────────────────────────────────────────────────
FIREBASE_PROJECT   = "sports-reminder-55578"
FIREBASE_API_KEY   = "AIzaSyCd3C1_XN69r8lWUBYPndoGFxmDjnsjX1E"
USERS_COLLECTION   = "users"          # multi-user: users/{uid}
GLOBAL_CONFIG_PATH = "config/global"  # global flags (world_cup_mode etc.)

TIMEZONE_OFFSET    = 3    # Israel (UTC+3)

# ─────────────────────────────────────────────────────────────────────────────
# PLAYER WATCH — stats for specific players, shown in the morning email
# Each entry: display_name, espn_id, team_id (ESPN), team_name, league_id
# ─────────────────────────────────────────────────────────────────────────────
PLAYER_WATCH = [
    {
        "display_name": "Deni Avdija",
        "espn_id":      "4683021",
        "team_id":      "22",           # Portland Trail Blazers
        "team_name":    "Portland Trail Blazers",
        "league_id":    "nba",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# ESPN ENDPOINTS  (league_id → URL)
# ─────────────────────────────────────────────────────────────────────────────
ESPN_ENDPOINTS = {
    "premier_league":       "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "la_liga":              "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "bundesliga":           "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard",
    "serie_a":              "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "ligue_1":              "https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/scoreboard",
    "champions_league":     "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard",
    "europa_league":        "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa/scoreboard",
    "israeli_pl_soccer":    "https://site.api.espn.com/apis/site/v2/sports/soccer/isr.1/scoreboard",
    "mls":                  "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard",
    "fifa_world_cup":       "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
    "nba":                  "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "euroleague":            None,    # uses EuroLeague official API (see below)
    "eurocup":               None,    # uses EuroCup official API (see below)
    "israeli_pl_basketball": None,    # uses TheSportsDB (ESPN returns empty for isr.1 basketball)
}

# ─────────────────────────────────────────────────────────────────────────────
# THESPORTSDB — Israeli leagues (ESPN isr.1 returns only partial team list)
# Free key "3" covers eventsday + eventsseason.
# Basketball ID=4474, Soccer ID=4644 (Israeli Premier League / Ligat HaAl)
# ─────────────────────────────────────────────────────────────────────────────
TSDB_LEAGUES = {
    "israeli_pl_basketball": "Israeli Basketball Premier League",
    "israeli_pl_soccer":     "Israeli Premier League",
}
TSDB_LEAGUE_IDS = {
    "israeli_pl_basketball": "4474",
    "israeli_pl_soccer":     "4644",
}
TSDB_SEASON = "2025-2026"
TSDB_FREE_KEY = "3"

# ─────────────────────────────────────────────────────────────────────────────
# EUROLEAGUE / EUROCUP OFFICIAL API
# ESPN dropped these — use api-live.euroleague.net instead
# Competition codes: E = EuroLeague, U = EuroCup
# Season codes: E2025 = 2025-26 EuroLeague, U2025 = 2025-26 EuroCup
# ─────────────────────────────────────────────────────────────────────────────
EUROLEAGUE_COMPETITION_CODES = {
    "euroleague": ("E", "E2025"),
    "eurocup":    ("U", "U2025"),
}

# ─────────────────────────────────────────────────────────────────────────────
# TEAM NAME MATCHING
# Three-layer approach:
#   1. NOISE_TOKENS  — strip known sponsor words before comparing
#   2. Word-coverage — all words of user's name appear in API name (multi-word)
#   3. ALIASES       — last resort for abbreviations that can't be solved algorithmically
# ─────────────────────────────────────────────────────────────────────────────

# Sponsor / filler words that APIs inject into team names.
# These are NEVER part of a team's actual identity — safe to ignore.
NOISE_TOKENS = {
    # EuroLeague / EuroCup jersey sponsors (updated each season as needed)
    "rapyd",        # Maccabi Rapyd Tel Aviv
    "ibi",          # Hapoel IBI Tel Aviv
    "beko",         # Fenerbahce Beko
    "aktor",        # Panathinaikos Aktor
    "mozzart",      # Partizan Mozzart Bet
    "bet",          # Partizan Mozzart Bet  (also covers "1xbet", etc.)
    "ea7",          # EA7 Emporio Armani Milan
    "emporio",      # EA7 Emporio Armani Milan
    "armani",       # EA7 Emporio Armani Milan
    "ldlc",         # LDLC ASVEL Villeurbanne
    "segafredo",    # Virtus Segafredo Bologna
    "meridianbet",  # Crvena Zvezda Meridianbet
    "betano",       # various soccer clubs
    "ewc",          # Efes branding variants
    "baxi",         # Baxi Manresa (EuroCup)
    "cosea",        # Cosea JL Bourg-en-Bresse (EuroCup)
    "midtown",      # Hapoel Midtown Jerusalem (EuroCup)
    "gain",         # Besiktas Gain Istanbul (EuroCup)
    "betsson",      # Aris Thessaloniki Betsson (EuroCup)
    "cosmorama",    # Panionios Cosmorama (EuroCup)
    "voli",         # Buducnost Voli (EuroCup)
    # Soccer jersey sponsors occasionally appended to team names
    "365",          # bet365 variations
    "ticketportal",
}

# Hard-coded aliases: only for cases the algorithm can't solve
# (abbreviations, historic name changes, etc.)
TEAM_ALIASES = {
    # ESPN soccer abbreviations
    "Man City":                    "Manchester City",
    "Man United":                  "Manchester United",
    "Spurs":                       "Tottenham Hotspur",
    "Nottm Forest":                "Nottingham Forest",
    "Inter":                       "Inter Milan",
    # ESPN drops/changes prefixes or uses short name
    "Barcelona":                   "FC Barcelona",
    "Porto":                       "FC Porto",
    "Bologna":                     "Bologna FC",
    "Milan":                       "AC Milan",
    "Lazio":                       "SS Lazio",
    "Atletico Madrid":             "Atletico Madrid",
    "Atlético Madrid":             "Atletico Madrid",
    "Hoffenheim":                  "TSG Hoffenheim",
    "RB Leipzig":                  "RB Leipzig",
    "Köln":                        "FC Koln",
    "Koln":                        "FC Koln",
    "Valencia":                    "Valencia CF",
    "Sevilla":                     "Sevilla FC",
    "Espanol":                     "RCD Espanyol",
    "Espanyol":                    "RCD Espanyol",
    "Osasuna":                     "CA Osasuna",
    "Alaves":                      "Deportivo Alaves",
    "Alavés":                      "Deportivo Alaves",
    "Paris Saint-Germain":         "Paris Saint-Germain",  # identity, resolves accent issues
    "PSG":                         "Paris Saint-Germain",
    "Lens":                        "Lens",                 # identity
    "Rennes":                      "Rennes",
    "Brentford":                   "Brentford",
    "Bournemouth":                 "Bournemouth",
    # Israeli teams — ESPN uses hyphens / apostrophes
    "Maccabi Tel-Aviv":            "Maccabi Tel Aviv",
    "Hapoel Tel-Aviv":             "Hapoel Tel Aviv",
    "Hapoel Be'er":                "Hapoel Beer Sheva",    # ESPN truncates
    "Hapoel Be'er Sheva":          "Hapoel Beer Sheva",
    "Hapoel Beer-Sheva":           "Hapoel Beer Sheva",
    "Bnei Yehuda Tel-Aviv":        "Bnei Yehuda",
    "Ironi Kiryat-Shmona":         "Ironi Kiryat Shmona",
    "Hapoel Jerusalem":            "Hapoel Jerusalem",
    "Bnei Sakhnin":                "Bnei Sakhnin",
    "Hapoel Hadera":               "Hapoel Hadera",
    "Maccabi Petah-Tikva":         "Maccabi Petah Tikva",
    "Hapoel Raanana":              "Hapoel Raanana",
    "Maccabi Bnei Raina":          "Maccabi Bnei Raina",
    # EuroLeague / EuroCup
    "Zalgiris Kaunas":             "Zalgiris",
    "Crvena zvezda":               "Crvena Zvezda",
    "AS Monaco":                   "Monaco Basket",        # EuroLeague basketball
    "AS MONACO":                   "Monaco Basket",        # uppercase variant from API
    "EA7 Emporio Armani Milan":    "Olimpia Milano",       # full sponsor name → common name
    "EA7 EMPORIO ARMANI MILAN":    "Olimpia Milano",       # uppercase variant from API
    "Armani Milan":                "Olimpia Milano",
    "Olimpia Milano":              "Olimpia Milano",       # identity
    "Baskonia Vitoria-Gasteiz":    "Baskonia",
    "LDLC ASVEL VILLEURBANNE":     "ASVEL",                # EuroCup — ASVEL is 5 chars, below threshold
    "LDLC ASVEL Villeurbanne":     "ASVEL",
    # ESPN uses Italian name for Inter
    "Internazionale":              "Inter Milan",
    "FC Internazionale":           "Inter Milan",
    "FC Internazionale Milano":    "Inter Milan",
    # Rennes — ESPN uses full French name
    "Stade Rennais":               "Rennes",
    "Stade Rennais FC":            "Rennes",
    # MLS abbreviation
    "LAFC":                        "Los Angeles FC",
    # Red Bull Salzburg — ESPN sometimes uses RB abbreviation
    "RB Salzburg":                 "Red Bull Salzburg",
    "FC Red Bull Salzburg":        "Red Bull Salzburg",
    # Champions League / Europa League — ESPN sometimes uses shorter names
    "Real Madrid CF":              "Real Madrid",
    "Inter Milan":                 "Inter Milan",
    "Borussia Dortmund":           "Borussia Dortmund",
    "Sporting CP":                 "Sporting CP",
    "Slavia Prague":               "Slavia Prague",
    "FC Copenhagen":               "FC Copenhagen",
    "Bodo/Glimt":                  "Bodo/Glimt",
    "Union Saint-Gilloise":        "Union Saint-Gilloise",
    "Pafos FC":                    "Pafos FC",
    "Qarabag":                     "Qarabag",
    "Kairat Almaty":               "Kairat Almaty",
    "Villarreal CF":               "Villarreal CF",
    "Red Bull Salzburg":           "Red Bull Salzburg",
    "Eintracht Frankfurt":         "Eintracht Frankfurt",
    "Fenerbahce":                  "Fenerbahce",           # Europa League (no Beko)
    # NBA — "LA" abbreviation for Los Angeles teams
    "Los Angeles Lakers":          "LA Lakers",
    "Los Angeles Clippers":        "LA Clippers",
    # MLS
    "Los Angeles FC":              "Los Angeles FC",
    "Nashville SC":                "Nashville SC",
}

# ─────────────────────────────────────────────────────────────────────────────
# FIFA WORLD CUP 2026 — Emoji flags for national teams
# ESPN abbreviation → ISO 3166-1 alpha-2 (used to build emoji flag)
# ─────────────────────────────────────────────────────────────────────────────
_ESPN_ABBR_TO_ISO2 = {
    "MEX": "MX", "RSA": "ZA", "KOR": "KR", "CZE": "CZ", "CAN": "CA",
    "BIH": "BA", "USA": "US", "PAR": "PY", "QAT": "QA", "SUI": "CH",
    "BRA": "BR", "MAR": "MA", "HAI": "HT", "SCO": "GB",
    "AUS": "AU", "TUR": "TR", "GER": "DE", "CUW": "CW", "NED": "NL",
    "JPN": "JP", "CIV": "CI", "ECU": "EC", "SWE": "SE", "TUN": "TN",
    "ESP": "ES", "CPV": "CV", "BEL": "BE", "EGY": "EG", "KSA": "SA",
    "URU": "UY", "IRN": "IR", "NZL": "NZ", "FRA": "FR", "SEN": "SN",
    "IRQ": "IQ", "NOR": "NO", "ARG": "AR", "ALG": "DZ", "AUT": "AT",
    "JOR": "JO", "POR": "PT", "COD": "CD", "ENG": "GB",
    "CRO": "HR", "GHA": "GH", "PAN": "PA", "UZB": "UZ", "COL": "CO",
}

_SPECIAL_FLAGS = {
    "ENG": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "SCO": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
}
