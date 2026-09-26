import datetime
from config import season_start_year


def test_season_rollover_july_1():
    assert season_start_year(datetime.date(2026, 6, 30)) == 2025
    assert season_start_year(datetime.date(2026, 7, 1)) == 2026
    assert season_start_year(datetime.date(2027, 3, 1)) == 2026
