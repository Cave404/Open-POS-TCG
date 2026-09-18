"""
OpenPOS-TCG Addon
File: tests/test_ui_modernization_and_cfd.py
Addon ID: tcg_pos

Test Suite verifying the modernized UI architecture:
  1. Addon manifest configuration: default_pos_route and ui_extensions.
  2. Register & Intake template structure and zero dead-end navigation.
  3. Defensive CSS scoping under .tcg-register-container and .tcg-intake-container.
  4. CFD DOM CustomEvents contract (openpos:cart-update, openpos:transaction-settled).
  5. Web Audio API zero-latency synthesized frequencies.
  6. Keyboard shortcut listeners (F2, F4, F8, Esc).
  7. Buylist rapid entry matrix and trade-in bonus calculations.
"""

import json
from pathlib import Path
import re
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestUIModernizationAndCFD(unittest.TestCase):
    """Verifies workstation templates, CFD event emitters, and styling isolation."""

    def test_01_manifest_pos_takeover_and_ui_extensions(self):
        """Verify manifest.json contains default_pos_route and ui_extensions hook configuration."""
        manifest_path = PROJECT_ROOT / "manifest.json"
        self.assertTrue(manifest_path.exists(), "manifest.json missing!")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertEqual(manifest.get("id"), "tcg_pos")
        self.assertEqual(manifest.get("default_pos_route"), "/tcg/register",
                         "Manifest missing 'default_pos_route': '/tcg/register'!")

        ui_ext = manifest.get("ui_extensions")
        self.assertIsInstance(ui_ext, dict, "Manifest missing 'ui_extensions' configuration!")
        self.assertEqual(ui_ext.get("mode"), "canvas_replace")
        self.assertEqual(ui_ext.get("standalone_route"), "/tcg/register")
        self.assertIn("slots", ui_ext)

        slots = ui_ext["slots"]
        header_slots = [s for s in slots if s.get("slot") == "pos:header_actions"]
        self.assertTrue(len(header_slots) > 0, "Missing 'pos:header_actions' slot in manifest!")
        self.assertEqual(header_slots[0].get("template"), "tcg_pos/header_widget.html")

    def test_02_header_widget_and_canvas_templates_exist(self):
        """Verify header_widget.html and canvas.html exist and provide clean workstation links."""
        hw_path = PROJECT_ROOT / "templates" / "tcg_pos" / "header_widget.html"
        canvas_path = PROJECT_ROOT / "templates" / "tcg_pos" / "canvas.html"

        self.assertTrue(hw_path.exists(), "header_widget.html missing!")
        self.assertTrue(canvas_path.exists(), "canvas.html missing!")

        with open(hw_path, "r", encoding="utf-8") as f:
            hw_html = f.read()

        self.assertIn("/tcg/register", hw_html, "header_widget.html missing link to /tcg/register!")
        self.assertIn("/tcg/intake", hw_html, "header_widget.html missing link to /tcg/intake!")

    def test_03_defensive_css_scoping(self):
        """Verify all register and intake stylesheets are scoped to prevent bleeding into OpenPOS host theme."""
        reg_css_path = PROJECT_ROOT / "static" / "tcg_pos" / "css" / "register.css"
        intake_css_path = PROJECT_ROOT / "static" / "tcg_pos" / "css" / "intake.css"

        self.assertTrue(reg_css_path.exists(), "register.css missing!")
        self.assertTrue(intake_css_path.exists(), "intake.css missing!")

        with open(reg_css_path, "r", encoding="utf-8") as f:
            reg_css = f.read()

        with open(intake_css_path, "r", encoding="utf-8") as f:
            intake_css = f.read()

        self.assertIn(".tcg-register-container", reg_css, "register.css not scoped under .tcg-register-container!")
        self.assertIn(".tcg-intake-container", intake_css, "intake.css not scoped under .tcg-intake-container!")

    def test_04_cfd_event_emitters_in_register_js(self):
        """Verify register.js emits openpos:cart-update and openpos:transaction-settled with full payload."""
        reg_js_path = PROJECT_ROOT / "static" / "tcg_pos" / "js" / "register.js"
        self.assertTrue(reg_js_path.exists(), "register.js missing!")

        with open(reg_js_path, "r", encoding="utf-8") as f:
            js_code = f.read()

        # CFD Cart Update CustomEvent
        self.assertIn("openpos:cart-update", js_code, "CustomEvent 'openpos:cart-update' missing from register.js!")
        self.assertIn("line_total", js_code, "detail.items line_total missing from cart update payload!")
        self.assertIn("grand_total", js_code, "detail.grand_total missing from cart update payload!")

        # CFD Transaction Settled CustomEvent
        self.assertIn("openpos:transaction-settled", js_code, "CustomEvent 'openpos:transaction-settled' missing!")
        self.assertIn("transaction_number", js_code, "detail.transaction_number missing from settled event!")
        self.assertIn("change_due", js_code, "detail.change_due missing from settled event!")

    def test_05_synthesized_web_audio_and_keyboard_shortcuts(self):
        """Verify register.js defines zero-lag Web Audio oscillators and keyboard navigation listeners."""
        reg_js_path = PROJECT_ROOT / "static" / "tcg_pos" / "js" / "register.js"
        with open(reg_js_path, "r", encoding="utf-8") as f:
            js_code = f.read()

        # Web Audio API
        self.assertIn("AudioContext", js_code, "AudioContext missing from register.js!")
        self.assertIn("850", js_code, "Scan success beep frequency 850 Hz missing!")
        self.assertIn("150", js_code, "Error beep frequency 150 Hz missing!")
        self.assertIn("playChime", js_code, "playChime helper missing!")

        # Keyboard shortcuts
        self.assertIn('"F2"', js_code, "F2 shortcut missing from register.js!")
        self.assertIn('"F4"', js_code, "F4 customer shortcut missing from register.js!")
        self.assertIn('"F8"', js_code, "F8 checkout shortcut missing from register.js!")
        self.assertIn('"Escape"', js_code, "Escape shortcut missing from register.js!")

    def test_06_intake_rapid_entry_and_buylist_matrix(self):
        """Verify intake view provides rapid entry inputs, condition grading buttons, and trade-in comparisons."""
        intake_tmpl_path = PROJECT_ROOT / "templates" / "tcg_pos" / "intake.html"
        intake_js_path = PROJECT_ROOT / "static" / "tcg_pos" / "js" / "intake.js"

        with open(intake_tmpl_path, "r", encoding="utf-8") as f:
            html = f.read()

        with open(intake_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Rapid Entry Inputs in template
        self.assertIn("quickSetCodeInput", html, "quickSetCodeInput missing in intake.html!")
        self.assertIn("quickCollectorNumInput", html, "quickCollectorNumInput missing in intake.html!")
        self.assertIn("intake-game-select", html, "intake-game-select missing in intake.html!")

        # Condition buttons (NM, LP, MP, HP, DMG)
        for cond in ["cond-nm", "cond-lp", "cond-mp", "cond-hp", "cond-dmg"]:
            self.assertIn(cond, html, f"Condition class '{cond}' missing in intake.html!")

        # Running payout totals comparison
        self.assertIn("summary-total-cost", html, "summary-total-cost missing in intake.html!")
        self.assertIn("summary-total-credit-payout", html, "summary-total-credit-payout missing in intake.html!")
        self.assertIn("btn-commit-cash", html, "btn-commit-cash missing in intake.html!")
        self.assertIn("btn-commit-credit", html, "btn-commit-credit missing in intake.html!")

        # Hotkeys in JS
        self.assertIn('"q"', js.lower(), "Q condition hotkey missing in intake.js!")
        self.assertIn('"w"', js.lower(), "W condition hotkey missing in intake.js!")
        self.assertIn('"e"', js.lower(), "E condition hotkey missing in intake.js!")
        self.assertIn('"r"', js.lower(), "R condition hotkey missing in intake.js!")
        self.assertIn('"t"', js.lower(), "T condition hotkey missing in intake.js!")


if __name__ == "__main__":
    unittest.main()
