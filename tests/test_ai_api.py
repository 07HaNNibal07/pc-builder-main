"""AI API tests. The LLM is faked; deterministic tools use the real catalogue."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from api.ai import agent
from api.ai.llm import ProviderNotConfigured
from api.ai.tools import recommend_pc
from api.main import app


class FakeModel:
    """Small tool-calling double: it never makes a network request."""

    def bind_tools(self, _tools):
        return self

    def invoke(self, messages):
        user_messages = [m for m in messages if isinstance(m, HumanMessage)]
        last = str(user_messages[-1].content).lower()
        has_tool_data = any(isinstance(m, ToolMessage) for m in messages)
        if "сколько" in last:
            return AIMessage(content="В текущей сессии есть результат подбора; FPS нужно брать из его таблицы.") if has_tool_data \
                else AIMessage(content="В этой сессии ещё нет сборки, поэтому точный FPS назвать нельзя.")
        if has_tool_data:
            return AIMessage(content="Подборщик вернул реальные варианты сборки.")
        if "подбери" in last or "собери" in last:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "recommend_pc",
                    "args": {"budget": 120000, "task": "game", "resolution": "1440p"},
                    "id": "test-recommend", "type": "tool_call",
                }],
            )
        return AIMessage(content="Уточните бюджет и задачу.")


class AIAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.model = patch.object(agent, "create_chat_model", return_value=FakeModel())
        self.model.start()

    def tearDown(self):
        self.model.stop()

    def test_openapi_and_validation(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/api/ai/chat", paths)
        bad = self.client.post("/api/ai/chat", json={"message": "", "session_id": "bad session"})
        self.assertEqual(bad.status_code, 422)

    def test_tool_call_uses_real_selector_data(self):
        response = self.client.post("/api/ai/chat", json={
            "message": "Подбери игровой ПК за 120 тысяч для 1440p",
            "session_id": "ai-tool-real-data",
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["intent"], "recommend_pc")
        self.assertTrue(body["tool_calls"][0]["success"])
        self.assertTrue(body["builds"])
        self.assertIn("parts", body["builds"][0])

    def test_session_context_is_isolated(self):
        sid = "ai-context-a"
        self.client.post("/api/ai/chat", json={"message": "Подбери ПК", "session_id": sid})
        remembered = self.client.post("/api/ai/chat", json={"message": "А сколько FPS?", "session_id": sid})
        fresh = self.client.post("/api/ai/chat", json={"message": "А сколько FPS?", "session_id": "ai-context-b"})
        self.assertIn("есть результат", remembered.json()["answer"])
        self.assertIn("ещё нет", fresh.json()["answer"])

    def test_provider_and_timeout_errors_are_safe(self):
        with patch.object(agent, "create_chat_model", side_effect=ProviderNotConfigured("no key")):
            missing = self.client.post("/api/ai/chat", json={"message": "привет", "session_id": "no-provider"})
        self.assertEqual(missing.status_code, 503)
        with patch.object(agent, "create_chat_model", side_effect=TimeoutError("secret endpoint")):
            timed_out = self.client.post("/api/ai/chat", json={"message": "привет", "session_id": "timeout"})
        self.assertEqual(timed_out.status_code, 502)
        self.assertNotIn("secret endpoint", timed_out.text)

    def test_core_endpoints_and_optional_rag_stay_available(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/ai/health").status_code, 200)


class DeterministicTool(unittest.TestCase):
    def test_recommendation_wrapper_returns_catalogue_builds(self):
        result = json.loads(recommend_pc.invoke({"budget": 120000, "task": "game", "resolution": "1440p"}))
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["variants"])


if __name__ == "__main__":
    unittest.main()
