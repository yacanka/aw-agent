import unittest

from jira_fields import validate_fields, value_error


class FieldTests(unittest.TestCase):
    def test_standard_types(self):
        for value, schema in (
            ("text", {"type": "string"}),
            (1.5, {"type": "number"}),
            ("2026-10-09", {"type": "date"}),
            ("2026-10-09T12:30:00.000+0300", {"type": "datetime"}),
            ({"name": "synthetic-user"}, {"type": "user"}),
            ([{"id": "10"}], {"type": "array", "items": "option"}),
            (["one", "two"], {"type": "array", "items": "string"}),
        ):
            with self.subTest(schema=schema):
                self.assertIsNone(value_error(value, schema, []))

    def test_invalid_values(self):
        for value, schema in (
            (True, {"type": "number"}),
            (float("nan"), {"type": "number"}),
            ("2026-02-30", {"type": "date"}),
            ("2026-10-09T12:30:00", {"type": "datetime"}),
            ({"accountId": "cloud-id"}, {"type": "user"}),
            ("10", {"type": "option"}),
            ({"id": "10"}, {"type": "unknown"}),
        ):
            with self.subTest(schema=schema):
                self.assertIsNotNone(value_error(value, schema, []))

    def test_plugins_blocked_and_missing_default_omitted(self):
        metadata = {
            "customfield_1": {
                "required": True,
                "hasDefaultValue": True,
                "schema": {"type": "string", "custom": "vendor:field"},
            }
        }
        self.assertEqual(validate_fields({}, metadata), {})
        self.assertIn("customfield_1", validate_fields({"customfield_1": "guess"}, metadata))
