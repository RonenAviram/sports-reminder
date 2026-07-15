"""
Unit tests for welcome_email_function/main.py — Welcome Email (Stadium Lights v6).
Tests _build_welcome_html(), _build_welcome_plain(), _has_teams(), _has_players().
"""
import sys
import os
import unittest

# Add parent dir to path so we can import welcome_email_function
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock out dependencies that aren't available in test env
import types
mock_ff = types.ModuleType("functions_framework")
mock_ff.cloud_event = lambda f: f
sys.modules["functions_framework"] = mock_ff

mock_ce = types.ModuleType("cloudevents")
mock_ce_http = types.ModuleType("cloudevents.http")
mock_ce_http.CloudEvent = object
mock_ce.http = mock_ce_http
sys.modules["cloudevents"] = mock_ce
sys.modules["cloudevents.http"] = mock_ce_http

mock_firestore = types.ModuleType("google.cloud.firestore")
mock_firestore.Client = lambda: None
mock_gc = types.ModuleType("google.cloud")
mock_gc.firestore = mock_firestore
mock_g = types.ModuleType("google")
mock_g.cloud = mock_gc
sys.modules["google"] = mock_g
sys.modules["google.cloud"] = mock_gc
sys.modules["google.cloud.firestore"] = mock_firestore

from welcome_email_function.main import (
    _build_welcome_html,
    _build_welcome_plain,
    _has_teams,
    _has_players,
)


class TestBuildWelcomeHtml(unittest.TestCase):
    """Tests for _build_welcome_html() — Stadium Lights design."""

    def setUp(self):
        self.html = _build_welcome_html()

    # --- Structure ---
    def test_is_valid_html(self):
        self.assertIn("<!DOCTYPE html>", self.html)
        self.assertIn("</html>", self.html)
        self.assertIn("<body", self.html)
        self.assertIn("</body>", self.html)

    def test_has_meta_charset_utf8(self):
        self.assertIn('charset="utf-8"', self.html)

    def test_has_viewport_meta(self):
        self.assertIn('name="viewport"', self.html)

    # --- Stadium Lights colors ---
    def test_navy_background(self):
        self.assertIn("#0f172a", self.html)

    def test_amber_accent(self):
        self.assertIn("#f59e0b", self.html)

    def test_dark_card_background(self):
        self.assertIn("#1e293b", self.html)

    def test_muted_text_color(self):
        self.assertIn("#94a3b8", self.html)

    def test_footer_text_color(self):
        self.assertIn("#64748b", self.html)

    # --- Header ---
    def test_sports_reminder_header(self):
        self.assertIn("SPORTS REMINDER", self.html)

    def test_welcome_pill(self):
        self.assertIn("WELCOME", self.html)

    # --- Hero ---
    def test_hero_title(self):
        self.assertIn("You're all set!", self.html)

    def test_hero_subtitle(self):
        self.assertIn("Your preferences have been saved.", self.html)
        self.assertIn("Here's what's coming.", self.html)

    # --- Feature cards content ---
    def test_daily_card(self):
        self.assertIn("Daily matches every morning", self.html)
        self.assertIn("add-to-calendar links", self.html)

    def test_stats_card(self):
        self.assertIn("Player stats recap", self.html)
        self.assertIn("points, rebounds, assists", self.html)

    def test_weekly_card(self):
        self.assertIn("Weekly preview on Saturday", self.html)
        self.assertIn("7-day lookahead", self.html)

    # --- Note ---
    def test_no_matches_note(self):
        self.assertIn("No matches? No email.", self.html)
        self.assertIn("We only write when there's something to watch.", self.html)

    # --- Links ---
    def test_whatsapp_link(self):
        self.assertIn("https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT", self.html)
        self.assertIn("#25D366", self.html)  # WhatsApp green

    def test_edit_teams_link(self):
        self.assertIn("https://app.sportsreminder.pro/?utm_source=email&utm_medium=welcome", self.html)
        self.assertIn("Edit your teams", self.html)

    def test_unsubscribe_link(self):
        self.assertIn("utm_medium=unsubscribe", self.html)
        self.assertIn("Manage preferences", self.html)

    # --- No old design remnants ---
    def test_no_old_green_check(self):
        # Old design had a green check icon
        self.assertNotIn("&#10003;", self.html)

    def test_no_old_white_background(self):
        # Background should be navy, not white (#ffffff as bg)
        self.assertNotIn('background-color:#ffffff', self.html)
        self.assertNotIn('background-color: #ffffff', self.html)
        self.assertNotIn('background-color:white', self.html)

    def test_no_specific_times(self):
        # User requested no specific times like "9:00 AM"
        self.assertNotIn("9:00", self.html)
        self.assertNotIn("7:00", self.html)

    # --- Email client compatibility ---
    def test_table_based_layout(self):
        self.assertIn('role="presentation"', self.html)

    def test_no_flexbox(self):
        self.assertNotIn("display:flex", self.html)
        self.assertNotIn("display: flex", self.html)

    def test_no_css_grid(self):
        self.assertNotIn("display:grid", self.html)
        self.assertNotIn("display: grid", self.html)

    def test_inline_styles(self):
        # All styling should be inline for email clients
        self.assertNotIn("<style>", self.html)
        self.assertNotIn("<link rel=\"stylesheet\"", self.html)

    # --- Domain ---
    def test_uses_custom_domain(self):
        self.assertIn("app.sportsreminder.pro", self.html)
        self.assertNotIn("vercel.app", self.html)


class TestBuildWelcomePlain(unittest.TestCase):
    """Tests for _build_welcome_plain() — plain text fallback."""

    def setUp(self):
        self.text = _build_welcome_plain()

    def test_hero(self):
        self.assertIn("You're all set!", self.text)

    def test_daily_feature(self):
        self.assertIn("Daily matches every morning", self.text)

    def test_stats_feature(self):
        self.assertIn("Player stats recap", self.text)

    def test_weekly_feature(self):
        self.assertIn("Weekly preview on Saturday", self.text)

    def test_no_email_note(self):
        self.assertIn("No matches? No email.", self.text)

    def test_whatsapp_link(self):
        self.assertIn("https://chat.whatsapp.com/", self.text)

    def test_domain_link(self):
        self.assertIn("https://app.sportsreminder.pro", self.text)

    def test_no_html_tags(self):
        self.assertNotIn("<div", self.text)
        self.assertNotIn("<table", self.text)
        self.assertNotIn("<td", self.text)


class TestHasTeams(unittest.TestCase):
    """Tests for _has_teams() helper."""

    def test_empty_list(self):
        self.assertFalse(_has_teams({"teams": []}))

    def test_no_teams_key(self):
        self.assertFalse(_has_teams({}))

    def test_teams_not_list(self):
        self.assertFalse(_has_teams({"teams": "something"}))

    def test_has_one_team(self):
        self.assertTrue(_has_teams({"teams": ["Arsenal"]}))

    def test_has_multiple_teams(self):
        self.assertTrue(_has_teams({"teams": ["Arsenal", "Barcelona", "Maccabi Tel Aviv"]}))


class TestHasPlayers(unittest.TestCase):
    """Tests for _has_players() helper."""

    def test_no_players_key(self):
        self.assertFalse(_has_players({}))

    def test_empty_dict(self):
        self.assertFalse(_has_players({"tracked_players": {}}))

    def test_not_dict(self):
        self.assertFalse(_has_players({"tracked_players": "bad"}))

    def test_all_disabled(self):
        self.assertFalse(_has_players({"tracked_players": {"123": False, "456": {"enabled": False}}}))

    def test_boolean_true(self):
        self.assertTrue(_has_players({"tracked_players": {"123": True}}))

    def test_dict_enabled(self):
        self.assertTrue(_has_players({"tracked_players": {"123": {"enabled": True}}}))

    def test_mixed_formats(self):
        self.assertTrue(_has_players({"tracked_players": {"123": False, "456": True}}))


if __name__ == "__main__":
    unittest.main()
