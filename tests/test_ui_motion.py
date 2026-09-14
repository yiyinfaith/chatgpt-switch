from pathlib import Path
import unittest


class UiMotionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        self.css = (self.root / "ui/motion.css").read_text(encoding="utf-8")
        self.html = (self.root / "ui/index.html").read_text(encoding="utf-8")
        self.server = (self.root / "switcher/server.py").read_text(encoding="utf-8")

    def test_motion_stylesheet_is_loaded_and_served(self):
        self.assertIn('href="/motion.css"', self.html)
        self.assertIn('"/motion.css": "motion.css"', self.server)

    def test_primary_modes_and_all_dialog_surfaces_are_covered(self):
        for selector in ("button", "summary", "select", "input[type=\"text\"]",
                         ".mode-card", ".clock-panel", ".status", ".profiles-panel",
                         ".setting-link", ".shortcut-card", ".close-option",
                         ".startup-option", ".component", ".update-row", ".detail-item",
                         ".setup-profile", ".segmented"):
            self.assertIn(selector, self.css)
        self.assertIn(".mode-card > .primary:hover", self.css)
        self.assertIn("prefers-reduced-motion", self.css)


if __name__ == "__main__":
    unittest.main()
