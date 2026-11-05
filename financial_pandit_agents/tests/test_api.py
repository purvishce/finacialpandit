"""Verify HTTP validation and crew integration without model requests."""

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from financial_pandit_agents import api


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.runner = patch.object(api, "run_assignment").start()
        self.addCleanup(patch.stopall)

    def test_health_does_not_run_crew(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.runner.assert_not_called()

    def test_model_discovery(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["id"], "financial-pandit")
        self.runner.assert_not_called()

    def test_chat_completion_preserves_history(self):
        self.runner.return_value = "You spent $100."
        response = self.client.post("/v1/chat/completions", json={
            "model": "financial-pandit",
            "messages": [
                {"role": "system", "content": "Use dollars."},
                {"role": "user", "content": "September?"},
                {"role": "assistant", "content": "$100."},
                {"role": "user", "content": [{"type": "text", "text": " Compare August "}]},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertTrue(body["id"].startswith("chatcmpl-"))
        self.assertEqual(body["choices"][0]["message"], {
            "role": "assistant", "content": "You spent $100.",
        })
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(self.runner.call_args.kwargs["question"], "Compare August")
        self.assertEqual(json.loads(self.runner.call_args.kwargs["session_context"]), [
            {"role": "system", "content": "Use dollars."},
            {"role": "user", "content": "September?"},
            {"role": "assistant", "content": "$100."},
        ])

    def test_stream_has_openai_deltas_and_done(self):
        self.runner.return_value = "Spending: $100.\nRésumé"
        response = self.client.post("/v1/chat/completions", json={
            "model": "financial-pandit", "stream": True,
            "messages": [{"role": "user", "content": "Spending?"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1], "[DONE]")
        chunks = [json.loads(event) for event in events[:-1]]
        self.assertEqual(len({chunk["id"] for chunk in chunks}), 1)
        self.assertTrue(all(chunk["object"] == "chat.completion.chunk" for chunk in chunks))
        self.assertEqual(chunks[0]["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(chunks[1]["choices"][0]["delta"]["content"], self.runner.return_value)
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.runner.assert_called_once_with(question="Spending?", session_context="")

    def test_invalid_chat_requests_never_call_crew(self):
        for payload, status in [
            ({"messages": []}, 400),
            ({"messages": [{"role": "user", "content": " "}]}, 400),
            ({"messages": [{"role": "assistant", "content": "Hello"}]}, 400),
            ({"messages": [{"role": "user", "content": [{"type": "image_url"}]}]}, 400),
            ({"messages": [{"role": "user", "content": "Hello"}], "n": 2}, 400),
            ({"model": "missing", "messages": [{"role": "user", "content": "Hello"}]}, 404),
        ]:
            with self.subTest(payload=payload):
                response = self.client.post("/v1/chat/completions", json=payload)
                self.assertEqual(response.status_code, status)
                self.assertIn("error", response.json())
        self.runner.assert_not_called()

    def test_chat_failure_hides_exception(self):
        self.runner.side_effect = RuntimeError("private financial details")
        for stream in (False, True):
            with self.subTest(stream=stream), self.assertLogs(api.logger, level="ERROR"):
                response = self.client.post("/v1/chat/completions", json={
                    "stream": stream,
                    "messages": [{"role": "user", "content": "Spending?"}],
                })
            self.assertNotIn("private financial details", response.text)
            if stream:
                self.assertIn('"code":"internal_error"', response.text)
                self.assertTrue(response.text.endswith("data: [DONE]\n\n"))
            else:
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()["error"]["code"], "internal_error")

    def test_ask_forwards_question_and_context(self):
        self.runner.return_value = "Spending report"
        response = self.client.post("/ask", json={
            "question": " Compare August and September ",
            "session_context": "Earlier results",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"answer": "Spending report"})
        self.runner.assert_called_once_with(
            question="Compare August and September",
            session_context="Earlier results",
        )

    def test_context_defaults_to_empty_string(self):
        self.runner.return_value = "Report"
        response = self.client.post("/ask", json={"question": "Spending?"})
        self.assertEqual(response.status_code, 200)
        self.runner.assert_called_once_with(question="Spending?", session_context="")

    def test_invalid_requests_do_not_run_crew(self):
        for payload in ({}, {"question": ""}, {"question": "   "},
                        {"question": 123}, {"question": "Spending?", "session_context": None}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/ask", json=payload).status_code, 422)
        self.runner.assert_not_called()

    def test_failure_returns_generic_error(self):
        self.runner.side_effect = RuntimeError("private financial details")
        with self.assertLogs(api.logger, level="ERROR"):
            response = self.client.post("/ask", json={"question": "Spending?"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {
            "detail": "Unable to complete the financial analysis.",
        })


if __name__ == "__main__":
    unittest.main()
