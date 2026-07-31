import unittest
from unittest.mock import patch, MagicMock

from framework.utils.response_formatter import ResponseFormatter, ParseResult

class FakeLitellmResponse:
    """Simple object mimicking the structure returned by litellm.completion."""
    def __init__(self, content: str):
        self.choices = [MagicMock(message=MagicMock(content=content))]

class TestResponseFormatter(unittest.TestCase):
    def setUp(self):
        # Use a dummy model name; the actual value is irrelevant for the mock.
        self.formatter = ResponseFormatter(model="dummy-model", api_key="dummy-key", verbose=0)
        # Simple schema string (the workflow supplies a larger schema, but for testing a tiny one works).
        self.schema = "{\n  \"action\": <string>,\n  \"payload\": {\n    \"message\": <string>\n  }\n}"

    @patch("framework.utils.response_formatter.litellm")
    def test_successful_json_response(self, mock_litellm):
        # Mock the litellm.completion call to return a well‑formed JSON string.
        mock_litellm.completion.return_value = FakeLitellmResponse('{"action": "send_message", "payload": {"message": "Hello"}}')
        result = self.formatter.format(user_prompt="test", schema_string=self.schema)
        # Verify that the formatter reports success and parses the payload correctly.
        self.assertTrue(result.success)
        self.assertIsInstance(result.payload, dict)
        self.assertEqual(result.payload["action"], "send_message")
        self.assertEqual(result.payload["payload"]["message"], "Hello")

    @patch("framework.utils.response_formatter.litellm")
    def test_malformed_json_then_correction(self, mock_litellm):
        # First call returns malformed JSON, second call (corrective) returns valid JSON.
        # The formatter will attempt the first call, fail parsing, then send a corrective prompt.
        first_response = FakeLitellmResponse('Just some text, not JSON')
        second_response = FakeLitellmResponse('{"action": "send_message", "payload": {"message": "Corrected"}}')
        # Configure side_effect to return first then second response.
        mock_litellm.completion.side_effect = [first_response, second_response]
        result = self.formatter.format(user_prompt="test", schema_string=self.schema)
        self.assertTrue(result.success)
        self.assertEqual(result.payload["payload"]["message"], "Corrected")

    @patch("framework.utils.response_formatter.litellm")
    def test_failure_after_corrections(self, mock_litellm):
        # Both initial and corrective calls return invalid JSON.
        mock_litellm.completion.return_value = FakeLitellmResponse('invalid')
        result = self.formatter.format(user_prompt="test", schema_string=self.schema)
        self.assertFalse(result.success)
        self.assertIsNone(result.payload)
        self.assertIsNotNone(result.error)

if __name__ == "__main__":
    unittest.main()