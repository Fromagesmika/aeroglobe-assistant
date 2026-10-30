"""Agent Gemini (faux client), mode démo et choix du fournisseur."""

from types import SimpleNamespace as NS

import pytest
from google.genai import types

from assistant import demo
from assistant.agent import GeminiAgent, available_provider
from assistant.db import connect

SQL = "SELECT NomCanal, COUNT(*) AS n FROM fait_ventes JOIN dim_canal USING (CanalID) GROUP BY 1"


def call(name, args, id_="c1"):
    content = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args, id=id_))])
    return NS(candidates=[NS(content=content)], usage_metadata=NS(prompt_token_count=50, candidates_token_count=10), text=None)


class FakeGemini:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.models = NS(generate_content=self._gen)

    def _gen(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture(scope="module")
def con():
    return connect()


def answer_args():
    return {"answer": "Quatre canaux.", "sql": SQL,
            "chart": {"type": "pie", "x": "NomCanal", "y": "n", "title": "Billets par canal"}}


def test_gemini_loop(con):
    client = FakeGemini([
        call("run_sql", {"sql": "SELEC oops", "objectif": "essai"}),
        call("run_sql", {"sql": SQL, "objectif": "compter"}, "c2"),
        call("repondre", answer_args(), "c3"),
    ])
    agent = GeminiAgent(con, client=client)
    res = agent.ask("Billets par canal ?")

    assert res.answer == "Quatre canaux."
    assert [s.ok for s in res.steps] == [False, True]
    assert len(res.data) == 4 and res.chart["type"] == "pie"
    error_part = agent.messages[2].parts[0].function_response
    assert "error" in error_part.response and error_part.id == "c1"
    assert [m.role for m in agent.messages] == ["user", "model", "user", "model", "user", "model"]
    assert client.calls[0]["config"].tool_config.function_calling_config.mode == "ANY"


def test_gemini_falls_back_when_saturated(con):
    from google.genai import errors

    from assistant.agent import GEMINI_FALLBACK_MODEL

    class Saturated(FakeGemini):
        def _gen(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["model"] != GEMINI_FALLBACK_MODEL:
                raise errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})
            return self.responses.pop(0)

    client = Saturated([call("repondre", answer_args())])
    agent = GeminiAgent(con, client=client)
    res = agent.ask("Billets par canal ?")
    assert res.answer == "Quatre canaux."
    assert agent.model == GEMINI_FALLBACK_MODEL
    assert [c["model"] for c in client.calls][-1] == GEMINI_FALLBACK_MODEL


def test_gemini_empty_candidate(con):
    client = FakeGemini([NS(candidates=[], usage_metadata=None, text=None)])
    res = GeminiAgent(con, client=client).ask("?")
    assert res.error


def test_demo_record_and_replay(con, tmp_path):
    client = FakeGemini([call("repondre", answer_args())])
    res = GeminiAgent(con, client=client).ask("Billets par canal ?")
    path = tmp_path / "reponses.json"
    demo.record("Billets par canal ?", res, "Google Gemini", "gemini-test", path=path)

    replayed = demo.replay(con, "Billets par canal ?", path=path)
    assert replayed.answer == "Quatre canaux."
    assert len(replayed.data) == 4
    assert demo.replay(con, "Autre question", path=path) is None


def test_demo_skips_failed_answers(tmp_path):
    from assistant.agent import AgentResult

    path = tmp_path / "reponses.json"
    demo.record("Q", AgentResult(answer="x", error="boom"), "p", "m", path=path)
    assert not path.exists()


@pytest.mark.parametrize("env, expected", [
    ({}, None),
    ({"GEMINI_API_KEY": "k"}, "gemini"),
    ({"ANTHROPIC_API_KEY": "k"}, "claude"),
    ({"GEMINI_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}, "gemini"),
    ({"GEMINI_API_KEY": "k", "ANTHROPIC_API_KEY": "k", "LLM_PROVIDER": "claude"}, "claude"),
])
def test_provider_choice(monkeypatch, env, expected):
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert available_provider() == expected
