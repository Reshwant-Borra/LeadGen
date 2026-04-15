import unittest
from unittest.mock import MagicMock

from discover_query import (
    AUTO_DISCOVER_NUM_QUERIES,
    _similar_query,
    invent_n_distinct_places_queries,
    is_coffee_cafe_food_default_query,
    niches_for_random_draw,
    random_places_query,
)


class TestDiscoverQuery(unittest.TestCase):
    def test_random_query_shape(self) -> None:
        q = random_places_query()
        self.assertIn(" in ", q)
        self.assertGreater(len(q), 5)

    def test_random_variation(self) -> None:
        s = {random_places_query() for _ in range(40)}
        self.assertGreater(len(s), 1, "expected some variety across draws")

    def test_random_pool_excludes_food_defaults(self) -> None:
        for n in niches_for_random_draw():
            low = n.lower()
            self.assertNotIn("coffee", low)
            self.assertNotRegex(low, r"^restaurants?$")
            self.assertNotRegex(low, r"^bakeries$")

    def test_random_never_emits_coffee_shops_phrase(self) -> None:
        for _ in range(80):
            q = random_places_query().lower()
            self.assertNotIn("coffee shop", q)
            self.assertNotRegex(q, r"\bcafé\b")
            self.assertNotRegex(q, r"\bcafe\b")

    def test_food_default_detector(self) -> None:
        self.assertTrue(is_coffee_cafe_food_default_query("coffee shops in Austin TX"))
        self.assertTrue(is_coffee_cafe_food_default_query("Coffee-shop in Asheville NC"))
        self.assertTrue(is_coffee_cafe_food_default_query("Best café near Miami"))
        self.assertTrue(is_coffee_cafe_food_default_query("Italian restaurants in Boston"))
        self.assertFalse(is_coffee_cafe_food_default_query("HVAC companies in Dallas TX"))
        self.assertFalse(is_coffee_cafe_food_default_query("dental offices in Tampa FL"))

    def test_similar_query_detects_light_rephrase(self) -> None:
        self.assertTrue(
            _similar_query(
                "coffee shops in Asheville North Carolina",
                "Coffee shops in Asheville NC",
            )
        )
        self.assertFalse(_similar_query("HVAC companies in Dallas TX", "dental offices in Tampa FL"))

    def test_invent_n_distinct_parses_llm_json(self) -> None:
        cities = [
            "Dallas TX",
            "Tampa FL",
            "Denver CO",
            "Atlanta GA",
            "Seattle WA",
            "Phoenix AZ",
            "Boston MA",
            "Chicago IL",
            "Miami FL",
            "Portland OR",
            "Austin TX",
            "Nashville TN",
        ]
        queries = [
            f"HVAC companies in {cities[i % len(cities)]}" for i in range(AUTO_DISCOVER_NUM_QUERIES)
        ]
        payload = {"queries": queries}
        import json as _json

        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content=_json.dumps(payload)),
                )
            ]
        )
        out = invent_n_distinct_places_queries(client, "gpt-4o-mini", n=AUTO_DISCOVER_NUM_QUERIES)
        self.assertEqual(len(out), AUTO_DISCOVER_NUM_QUERIES)
        self.assertEqual(out, queries)


if __name__ == "__main__":
    unittest.main()
