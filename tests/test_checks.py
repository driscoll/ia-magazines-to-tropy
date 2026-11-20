"""Tests for the record checks. Run with: uv run python -m unittest discover tests"""
import unittest

from ia_magazines_to_tropy.checks import problems


def record(**fields):
    """A minimal record; each test sets only the fields it checks."""
    return {"cover_text_reading_order": [], **fields}


def date_problems(as_printed, year, month):
    o = record(issue_date={"as_printed": as_printed, "year": year, "month": month})
    return [p for p in problems(o) if p.startswith("issue_date")]


class IssueDate(unittest.TestCase):
    def test_full_month_name(self):
        self.assertEqual(date_problems("August 1995", 1995, 8), [])

    def test_abbreviated_month(self):
        self.assertEqual(date_problems("1995 AUG", 1995, 8), [])
        self.assertEqual(date_problems("Sept. 1987", 1987, 9), [])
        self.assertEqual(date_problems("Dec 1983", 1983, 12), [])

    def test_month_disagrees(self):
        self.assertEqual(date_problems("July 1995", 1995, 8), ["issue_date.month disagrees with as_printed"])
        self.assertEqual(date_problems("JUL 1995", 1995, 8), ["issue_date.month disagrees with as_printed"])

    def test_year_not_printed(self):
        self.assertEqual(date_problems("August", 1995, 8), ["issue_date.year not in as_printed"])

    def test_no_month_parsed(self):
        self.assertEqual(date_problems("Summer 1995", 1995, None), [])


class OtherChecks(unittest.TestCase):
    def test_entity_not_in_cover_text(self):
        o = record(cover_text_reading_order=["BYTE", "Inside the 68000"],
                   named_entities=[{"name_as_printed": "Motorola", "evidence": "cover_text"}])
        self.assertIn("entity not in cover text: Motorola", problems(o))

    def test_price_agrees(self):
        o = record(prices=[{"as_printed": "$2.95", "amount": 2.95}])
        self.assertEqual(problems(o), [])

    def test_repeated_cover_text(self):
        o = record(cover_text_reading_order=["BYTE", "BYTE"])
        self.assertIn("repeated cover text, possibly artwork text: BYTE", problems(o))

    def test_uncertain_text_needs_a_note(self):
        o = record(cover_text_reading_order=["THE [?] ISSUE"])
        self.assertIn("[?] in cover text but no uncertainty note", problems(o))

    def test_duplicate_topics(self):
        self.assertIn("duplicate items in topics", problems(record(topics=["games", "games"])))


if __name__ == "__main__":
    unittest.main()
