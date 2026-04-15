import unittest

from signals import compute_signals, extract_visible_text, signal_priority_hint


class TestSignals(unittest.TestCase):
    def test_booking_present_turns_off_no_booking(self) -> None:
        html = '<html><body><a href="https://calendly.com/acme">Book</a></body></html>'
        text = extract_visible_text(html)
        s = compute_signals(html, text)
        self.assertFalse(s.flags["no_booking_system"])

    def test_call_only_intake(self) -> None:
        html = "<html><body><p>Call us to schedule an appointment.</p></body></html>"
        text = extract_visible_text(html)
        s = compute_signals(html, text)
        self.assertTrue(s.flags["no_booking_system"])
        self.assertTrue(s.flags["call_only_intake"])

    def test_clunky_form(self) -> None:
        inputs = "".join(f'<input name="f{i}">' for i in range(8))
        html = f"<html><body><form>{inputs}</form></body></html>"
        text = extract_visible_text(html)
        s = compute_signals(html, text)
        self.assertTrue(s.flags["clunky_or_long_form"])

    def test_priority_hint(self) -> None:
        flags = {
            "call_only_intake": True,
            "no_booking_system": True,
            "no_instant_response": True,
            "weak_or_unclear_cta": False,
            "clunky_or_long_form": False,
            "weak_lead_capture": False,
        }
        h = signal_priority_hint(flags)
        self.assertIn("call_only_intake", h)


if __name__ == "__main__":
    unittest.main()
