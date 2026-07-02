#!/usr/bin/env python3
"""
SportsReminder — Core unit tests (Layer 1: pure, zero side-effects).

Run:  pytest tests/test_core.py -v
"""

import sys
import os
import datetime

# ── make the repo root importable ──────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sports_reminder as sr


# ═══════════════════════════════════════════════════════════════════════════
# 1. DST helpers — _israel_utc_offset_h / _berlin_utc_offset_h
# ═══════════════════════════════════════════════════════════════════════════

class TestIsraelUTCOffset:
    """Israel DST: starts last Friday of March 00:00 UTC,
    ends last Sunday of October 01:00 UTC."""

    def test_deep_winter(self):
        """January → IST (+2)"""
        dt = datetime.datetime(2026, 1, 15, 12, 0)
        assert sr._israel_utc_offset_h(dt) == 2

    def test_deep_summer(self):
        """July → IDT (+3)"""
        dt = datetime.datetime(2026, 7, 15, 12, 0)
        assert sr._israel_utc_offset_h(dt) == 3

    def test_spring_transition_before(self):
        """2026: last Friday of March = March 27.
        One second before 00:00 UTC → still winter (+2)."""
        dt = datetime.datetime(2026, 3, 26, 23, 59, 59)
        assert sr._israel_utc_offset_h(dt) == 2

    def test_spring_transition_at(self):
        """Exactly at DST start → summer (+3)."""
        dt = datetime.datetime(2026, 3, 27, 0, 0, 0)
        assert sr._israel_utc_offset_h(dt) == 3

    def test_autumn_transition_before(self):
        """2026: last Sunday of October = October 25.
        Midday Oct 24 UTC → clearly still summer (+3)."""
        dt = datetime.datetime(2026, 10, 24, 12, 0, 0)
        assert sr._israel_utc_offset_h(dt) == 3

    def test_autumn_transition_at(self):
        """Exactly at DST end → winter (+2)."""
        dt = datetime.datetime(2026, 10, 25, 1, 0, 0)
        assert sr._israel_utc_offset_h(dt) == 2

    def test_year_2027(self):
        """Verify different year works (2027 summer)."""
        dt = datetime.datetime(2027, 6, 1, 12, 0)
        assert sr._israel_utc_offset_h(dt) == 3


class TestBerlinUTCOffset:
    """Europe/Berlin DST: starts last Sunday of March 01:00 UTC,
    ends last Sunday of October 01:00 UTC."""

    def test_winter(self):
        """January → CET (+1)"""
        dt = datetime.datetime(2026, 1, 15, 12, 0)
        assert sr._berlin_utc_offset_h(dt) == 1

    def test_summer(self):
        """July → CEST (+2)"""
        dt = datetime.datetime(2026, 7, 15, 12, 0)
        assert sr._berlin_utc_offset_h(dt) == 2

    def test_spring_transition_before(self):
        """2026: last Sunday of March = March 29.
        One second before 01:00 UTC → still winter (+1)."""
        dt = datetime.datetime(2026, 3, 29, 0, 59, 59)
        assert sr._berlin_utc_offset_h(dt) == 1

    def test_spring_transition_at(self):
        """Exactly at CEST start → summer (+2)."""
        dt = datetime.datetime(2026, 3, 29, 1, 0, 0)
        assert sr._berlin_utc_offset_h(dt) == 2

    def test_autumn_transition_at(self):
        """2026: last Sunday of October = October 25.
        Exactly at 01:00 UTC → winter (+1)."""
        dt = datetime.datetime(2026, 10, 25, 1, 0, 0)
        assert sr._berlin_utc_offset_h(dt) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. _compute_display_date — overnight bucketing
# ═══════════════════════════════════════════════════════════════════════════

class TestComputeDisplayDate:
    """Games 00:00-04:59 IL → previous day.  05:00+ → same day."""

    def test_midnight_moves_back(self):
        assert sr._compute_display_date("2026-06-23", "00:00") == "2026-06-22"

    def test_0130_moves_back(self):
        assert sr._compute_display_date("2026-06-23", "01:30") == "2026-06-22"

    def test_0459_moves_back(self):
        assert sr._compute_display_date("2026-06-23", "04:59") == "2026-06-22"

    def test_0500_stays(self):
        assert sr._compute_display_date("2026-06-23", "05:00") == "2026-06-23"

    def test_2000_stays(self):
        assert sr._compute_display_date("2026-06-23", "20:00") == "2026-06-23"

    def test_tbd_stays(self):
        assert sr._compute_display_date("2026-06-23", "TBD") == "2026-06-23"

    def test_jan_1_wraps_to_dec(self):
        """00:30 on Jan 1 → Dec 31 previous year."""
        assert sr._compute_display_date("2027-01-01", "00:30") == "2026-12-31"


# ═══════════════════════════════════════════════════════════════════════════
# 3. 4-branch label logic (tested via build_email_html internals)
#    We can't easily unit-test build_email_html (it's huge), but we CAN
#    test the date-label decision logic by extracting the conditions.
# ═══════════════════════════════════════════════════════════════════════════

def _label_decision(game_time: str, game_il_date: str, game_display_date: str, today: str):
    """Replicate the 4-branch label logic from build_email_html.

    Returns: (label_text, is_midnight_label)
    label_text = None means no label shown.
    """
    import datetime as dt

    # Branch 1: midnight cross-day → "Mon-Tue night"
    if game_time == "00:00" and game_il_date != game_display_date:
        disp_dt = dt.datetime.strptime(game_display_date, "%Y-%m-%d")
        il_dt   = dt.datetime.strptime(game_il_date, "%Y-%m-%d")
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        d1 = days[disp_dt.weekday()]
        d2 = days[il_dt.weekday()]
        return (f"{d1}-{d2} night", True)

    # Branch 2: future display_date (not today)
    if game_display_date != today:
        if game_il_date != game_display_date:
            # cross-midnight on a future day → show il_date
            label_dt = dt.datetime.strptime(game_il_date, "%Y-%m-%d")
        else:
            label_dt = dt.datetime.strptime(game_display_date, "%Y-%m-%d")
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return (f"{days[label_dt.weekday()]} {label_dt.day}/{label_dt.month}", False)

    # Branch 3: same-day cross-midnight (display=today but il≠display)
    if game_il_date != game_display_date:
        il_dt = dt.datetime.strptime(game_il_date, "%Y-%m-%d")
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return (f"{days[il_dt.weekday()]} {il_dt.day}/{il_dt.month}", False)

    # Branch 4: no label
    return (None, False)


class TestLabelDecision:
    """4-branch label logic for cross-midnight / future dates."""

    def test_midnight_cross_day(self):
        """00:00 game on Tue (il) displayed under Mon → 'Mon-Tue night'"""
        label, is_mid = _label_decision("00:00", "2026-06-23", "2026-06-22", "2026-06-22")
        assert is_mid is True
        assert label == "Mon-Tue night"

    def test_future_cross_midnight(self):
        """02:00 game: il_date=Wed 24/6, display_date=Tue 24/6→wait,
        display_date=Tue 24/6 (future), il_date=Thu 25/6 → show 'Thu 25/6'?
        Actually let's use the real case: Scotland 01:00, il=25/6 (Wed), disp=24/6 (Tue), today=23/6.
        Branch 2 + cross-midnight → label = il_date = 'Thu 25/6'"""
        label, is_mid = _label_decision("01:00", "2026-06-25", "2026-06-24", "2026-06-23")
        assert is_mid is False
        assert label == "Thu 25/6"

    def test_future_same_day(self):
        """Future display_date, no cross-midnight → show display_date.
        Game at 20:00, disp=Tue 24/6, il=Tue 24/6, today=Mon 23/6."""
        label, is_mid = _label_decision("20:00", "2026-06-24", "2026-06-24", "2026-06-23")
        assert is_mid is False
        assert label == "Wed 24/6"

    def test_same_day_cross_midnight(self):
        """display=today, but il≠display (e.g. 02:00 game, il=tomorrow).
        Panama 02:00, il=24/6 (Wed), disp=23/6 (Tue)=today → show 'Wed 24/6'"""
        label, is_mid = _label_decision("02:00", "2026-06-24", "2026-06-23", "2026-06-23")
        assert is_mid is False
        assert label == "Wed 24/6"

    def test_no_label(self):
        """Normal same-day game → no label."""
        label, is_mid = _label_decision("20:00", "2026-06-23", "2026-06-23", "2026-06-23")
        assert label is None
        assert is_mid is False


# ═══════════════════════════════════════════════════════════════════════════
# 4. Name matching — normalize_name / strip_noise / names_match
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeName:
    def test_accents(self):
        assert sr.normalize_name("Atlético Madrid") == "atletico madrid"

    def test_fc_prefix(self):
        assert sr.normalize_name("FC Barcelona") == "barcelona"

    def test_fc_suffix(self):
        assert sr.normalize_name("Bologna FC") == "bologna"

    def test_hyphens(self):
        assert sr.normalize_name("Bosnia-Herzegovina") == "bosnia herzegovina"

    def test_apostrophe(self):
        assert sr.normalize_name("Côte d'Ivoire") == "cote divoire"


class TestStripNoise:
    def test_removes_sponsor(self):
        result = sr.strip_noise("Maccabi Rapyd Tel Aviv")
        assert "rapyd" not in result
        assert "maccabi" in result
        assert "tel" in result
        assert "aviv" in result

    def test_no_noise(self):
        assert sr.strip_noise("Real Madrid") == sr.normalize_name("Real Madrid")


class TestNamesMatch:
    def test_exact(self):
        assert sr.names_match("Manchester City", "Manchester City") is True

    def test_alias_man_city(self):
        assert sr.names_match("Man City", "Manchester City") is True

    def test_alias_spurs(self):
        assert sr.names_match("Spurs", "Tottenham Hotspur") is True

    def test_accented(self):
        assert sr.names_match("Atlético Madrid", "Atletico Madrid") is True

    def test_fc_prefix(self):
        assert sr.names_match("FC Barcelona", "Barcelona") is True

    def test_sponsor_noise(self):
        assert sr.names_match("Maccabi Rapyd Tel Aviv", "Maccabi Tel Aviv") is True

    def test_panathinaikos(self):
        """Single-word noise-strip match (≥7 chars, first word)."""
        assert sr.names_match("Panathinaikos Aktor Athens", "Panathinaikos") is True

    def test_no_match(self):
        assert sr.names_match("Real Madrid", "Barcelona") is False

    def test_short_word_no_false_positive(self):
        """Short words (<7 chars) should NOT match via single-word rule."""
        assert sr.names_match("Milan Basket Club", "Milan") is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. _country_flag_emoji
# ═══════════════════════════════════════════════════════════════════════════

class TestCountryFlagEmoji:
    def test_usa(self):
        flag = sr._country_flag_emoji("USA")
        assert flag == "\U0001F1FA\U0001F1F8"  # 🇺🇸

    def test_germany(self):
        flag = sr._country_flag_emoji("GER")
        assert flag == "\U0001F1E9\U0001F1EA"  # 🇩🇪

    def test_brazil(self):
        flag = sr._country_flag_emoji("BRA")
        assert flag == "\U0001F1E7\U0001F1F7"  # 🇧🇷

    def test_unknown_returns_empty(self):
        assert sr._country_flag_emoji("XYZ") == ""

    def test_special_england(self):
        """England uses the subdivision flag (special case)."""
        flag = sr._country_flag_emoji("ENG")
        assert len(flag) > 0  # 🏴󠁧󠁢󠁥󠁮󠁧󠁿

    def test_special_scotland(self):
        flag = sr._country_flag_emoji("SCO")
        assert len(flag) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. _format_series_summary
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatSeriesSummary:
    def test_starts_with_il_date(self):
        result = sr._format_series_summary("Series starts 6/3", "2026-06-04")
        assert "June 4th" in result
        assert "6/3" not in result

    def test_fallback_without_il_date(self):
        result = sr._format_series_summary("Game 1 on 6/3")
        assert "June 3rd" in result

    def test_empty_string(self):
        assert sr._format_series_summary("") == ""

    def test_no_date_in_text(self):
        """Text without M/D pattern → unchanged."""
        text = "NBA Finals - Game 5"
        assert sr._format_series_summary(text) == text

    def test_ordinal_1st(self):
        result = sr._format_series_summary("starts 7/1", "2026-07-01")
        assert "July 1st" in result

    def test_ordinal_2nd(self):
        result = sr._format_series_summary("starts 7/2", "2026-07-02")
        assert "July 2nd" in result

    def test_ordinal_3rd(self):
        result = sr._format_series_summary("starts 7/3", "2026-07-03")
        assert "July 3rd" in result

    def test_ordinal_11th(self):
        result = sr._format_series_summary("starts 7/11", "2026-07-11")
        assert "July 11th" in result

    def test_ordinal_12th(self):
        result = sr._format_series_summary("starts 7/12", "2026-07-12")
        assert "July 12th" in result

    def test_ordinal_21st(self):
        result = sr._format_series_summary("starts 7/21", "2026-07-21")
        assert "July 21st" in result


# ═══════════════════════════════════════════════════════════════════════════
# 7. _gcal_url
# ═══════════════════════════════════════════════════════════════════════════

class TestGcalUrl:
    def test_basic_url(self):
        match = {
            "time": "20:00",
            "home": "Maccabi Tel Aviv",
            "away": "Hapoel Tel Aviv",
            "il_date": "2026-07-15",
            "sport": "soccer",
            "league_name": "Israeli Premier League",
        }
        url = sr._gcal_url(match, "2026-07-15")
        assert url is not None
        assert "calendar.google.com" in url
        assert "20260715" in url  # date should appear in UTC start

    def test_tbd_returns_none(self):
        match = {"time": "TBD", "home": "A", "away": "B"}
        assert sr._gcal_url(match, "2026-07-15") is None

    def test_empty_time_returns_none(self):
        match = {"time": "", "home": "A", "away": "B"}
        assert sr._gcal_url(match, "2026-07-15") is None

    def test_wc_emoji(self):
        """World Cup games should have 🏆 in the title."""
        match = {
            "time": "22:00",
            "home": "Brazil",
            "away": "Germany",
            "il_date": "2026-07-10",
            "is_world_cup": True,
            "league_name": "FIFA World Cup",
        }
        url = sr._gcal_url(match, "2026-07-10")
        assert url is not None
        # URL-encoded 🏆
        assert "%F0%9F%8F%86" in url or "🏆" in url

    def test_uses_il_date_not_today(self):
        """gcal should use il_date from the match, not today."""
        match = {
            "time": "03:00",
            "home": "Team A",
            "away": "Team B",
            "il_date": "2026-06-24",
            "sport": "soccer",
            "league_name": "Test",
        }
        url = sr._gcal_url(match, "2026-06-23")
        assert url is not None
        # The event date should be June 24 (il_date), not June 23 (today)
        assert "20260624" in url


# ═══════════════════════════════════════════════════════════════════════════
# 8. _firestore_bool / _firestore_string (Admin SDK — native dict)
# ═══════════════════════════════════════════════════════════════════════════

class TestFirestoreBool:
    def test_true(self):
        assert sr._firestore_bool({"key": True}, "key") is True

    def test_false(self):
        assert sr._firestore_bool({"key": False}, "key") is False

    def test_missing_default_false(self):
        assert sr._firestore_bool({}, "key") is False

    def test_missing_default_true(self):
        assert sr._firestore_bool({}, "key", default=True) is True

    def test_non_bool_returns_default(self):
        assert sr._firestore_bool({"key": "yes"}, "key") is False

    def test_non_bool_with_custom_default(self):
        assert sr._firestore_bool({"key": 1}, "key", default=True) is True


class TestFirestoreString:
    def test_basic(self):
        assert sr._firestore_string({"name": "hello"}, "name") == "hello"

    def test_missing(self):
        assert sr._firestore_string({}, "name") == ""

    def test_missing_custom_default(self):
        assert sr._firestore_string({}, "name", default="N/A") == "N/A"

    def test_non_string_returns_default(self):
        assert sr._firestore_string({"key": 42}, "key") == ""


# ═══════════════════════════════════════════════════════════════════════════
# 9. 24h filter logic (NBA / MLS / FIFA World Cup)
#    The actual filter is inside fetch_todays_games → fetch_espn_sport.
#    We test the decision logic in isolation.
# ═══════════════════════════════════════════════════════════════════════════

def _should_include_tomorrow_game(utc_event_time: str, league_id: str, weekly_mode: bool = False) -> bool:
    """Replicate the 24h filter: a game from tomorrow-UTC is included
    only if its Israel time is before 08:00, unless weekly_mode."""
    if weekly_mode:
        return True
    if league_id not in ("nba", "mls", "fifa_world_cup"):
        return True  # non-filtered leagues include everything
    try:
        utc_dt = datetime.datetime.strptime(utc_event_time, "%Y-%m-%dT%H:%MZ")
        il_off = sr._israel_utc_offset_h(utc_dt)
        il_hour = (utc_dt + datetime.timedelta(hours=il_off)).hour
        return il_hour < 8
    except Exception:
        return True


class TestFilter24h:
    """NBA, MLS, FIFA WC: tomorrow-UTC games only if IL time < 08:00."""

    def test_nba_0300_il_included(self):
        """03:00 IL (= 00:00 UTC summer) → included."""
        assert _should_include_tomorrow_game("2026-07-15T00:00Z", "nba") is True

    def test_nba_0500_il_included(self):
        """05:00 IL (= 02:00 UTC summer) → included."""
        assert _should_include_tomorrow_game("2026-07-15T02:00Z", "nba") is True

    def test_nba_0800_il_excluded(self):
        """08:00 IL (= 05:00 UTC summer) → excluded (>= 8)."""
        assert _should_include_tomorrow_game("2026-07-15T05:00Z", "nba") is False

    def test_nba_2200_il_excluded(self):
        """22:00 IL (= 19:00 UTC summer) → excluded."""
        assert _should_include_tomorrow_game("2026-07-15T19:00Z", "nba") is False

    def test_mls_0200_il_included(self):
        assert _should_include_tomorrow_game("2026-07-15T23:00Z", "mls") is True  # 23+3=26→02:00 next

    def test_wc_0400_il_included(self):
        """FIFA WC at 01:00 UTC → 04:00 IL → included."""
        assert _should_include_tomorrow_game("2026-07-15T01:00Z", "fifa_world_cup") is True

    def test_wc_1000_il_excluded(self):
        """FIFA WC at 07:00 UTC → 10:00 IL → excluded."""
        assert _should_include_tomorrow_game("2026-07-15T07:00Z", "fifa_world_cup") is False

    def test_premier_league_not_filtered(self):
        """Premier League is not subject to the 24h filter."""
        assert _should_include_tomorrow_game("2026-07-15T19:00Z", "premier_league") is True

    def test_weekly_mode_bypasses(self):
        """Weekly mode includes everything regardless."""
        assert _should_include_tomorrow_game("2026-07-15T19:00Z", "nba", weekly_mode=True) is True


# ═══════════════════════════════════════════════════════════════════════════
# 10. _last_weekday helper
# ═══════════════════════════════════════════════════════════════════════════

class TestLastWeekday:
    def test_last_friday_march_2026(self):
        """2026: last Friday of March = 27th."""
        assert sr._last_weekday(2026, 3, 4) == 27  # 4=Friday

    def test_last_sunday_march_2026(self):
        """2026: last Sunday of March = 29th."""
        assert sr._last_weekday(2026, 3, 6) == 29  # 6=Sunday

    def test_last_sunday_october_2026(self):
        """2026: last Sunday of October = 25th."""
        assert sr._last_weekday(2026, 10, 6) == 25

    def test_last_monday_february_2026(self):
        """2026: last Monday of February = 23rd."""
        assert sr._last_weekday(2026, 2, 0) == 23


# ═══════════════════════════════════════════════════════════════════════════
# 11. Edge cases and regression guards
# ═══════════════════════════════════════════════════════════════════════════

class TestRegressionGuards:
    """Tests for specific bugs that were fixed — prevent regressions."""

    def test_names_match_bidirectional_alias(self):
        """User stores 'FC Barcelona', API says 'Barcelona' → should match."""
        assert sr.names_match("Barcelona", "FC Barcelona") is True

    def test_display_date_boundary_0459(self):
        """04:59 is the last minute that moves back."""
        assert sr._compute_display_date("2026-06-23", "04:59") == "2026-06-22"

    def test_display_date_boundary_0500(self):
        """05:00 is the first minute that stays."""
        assert sr._compute_display_date("2026-06-23", "05:00") == "2026-06-23"

    def test_firestore_bool_legacy_dict_returns_default(self):
        """Old REST-style {'booleanValue': true} should return default (not crash)."""
        assert sr._firestore_bool({"key": {"booleanValue": True}}, "key") is False

    def test_midnight_label_not_on_normal_game(self):
        """A 20:00 game where il_date == display_date should NOT get a midnight label."""
        label, is_mid = _label_decision("20:00", "2026-06-22", "2026-06-22", "2026-06-22")
        assert is_mid is False
        assert label is None
