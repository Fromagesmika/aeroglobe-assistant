"""Teste la boucle de l'agent avec un faux client (aucun appel réseau)."""

import json
from types import SimpleNamespace as NS

import pytest

from assistant.agent import AeroGlobeAgent
from assistant.db import connect


def _usage():
    return NS(input_tokens=100, output_tokens=20)


def tool_use(sql, id_="tu_1"):
    block = NS(type="tool_use", id=id_, name="run_sql", input={"sql": sql, "objectif": "test"})
    return NS(stop_reason="tool_use", content=[block], usage=_usage())


def final(payload):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(payload))], usage=_usage())


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture(scope="module")
def con():
    return connect()


def test_tool_loop_then_answer(con):
    sql = "SELECT NomClasse, COUNT(*) AS n FROM fait_ventes JOIN dim_classe USING (ClasseID) GROUP BY 1"
    client = FakeClient([
        tool_use("SELEC broken"),
        tool_use(sql, "tu_2"),
        final({"answer": "Trois classes.", "sql": sql,
               "chart": {"type": "bar", "x": "NomClasse", "y": "n", "title": "Billets par classe"}}),
    ])
    agent = AeroGlobeAgent(con, client=client)
    res = agent.ask("Combien de billets par classe ?")

    assert res.answer == "Trois classes."
    assert [s.ok for s in res.steps] == [False, True]
    assert len(res.data) == 3 and res.chart["x"] == "NomClasse"
    # L'erreur SQL a bien été renvoyée au modèle comme tool_result en erreur
    assert agent.messages[2]["content"][0]["is_error"] is True
    # Historique : user, assistant, user(tool), assistant, user(tool), assistant
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "user", "assistant", "user", "assistant"]


def test_unsafe_query_is_refused_not_run(con):
    client = FakeClient([
        tool_use("DROP TABLE fait_ventes"),
        final({"answer": "ok", "sql": "SELECT COUNT(*) AS n FROM fait_ventes",
               "chart": {"type": "none", "x": "", "y": "", "title": ""}}),
    ])
    res = AeroGlobeAgent(con, client=client).ask("Supprime tout")
    assert res.steps[0].ok is False
    assert res.data["n"][0] == 450_000
    assert res.chart is None


def test_refusal(con):
    client = FakeClient([NS(stop_reason="refusal", content=[], usage=_usage())])
    res = AeroGlobeAgent(con, client=client).ask("?")
    assert res.error


def test_api_error_rolls_back_history(con):
    class Boom(FakeClient):
        def _create(self, **kwargs):
            raise RuntimeError("réseau")

    agent = AeroGlobeAgent(con, client=Boom([]))
    with pytest.raises(RuntimeError):
        agent.ask("Question")
    assert agent.messages == []


def test_request_shape(con):
    client = FakeClient([final({"answer": "a", "sql": "", "chart": {"type": "none", "x": "", "y": "", "title": ""}})])
    AeroGlobeAgent(con, client=client).ask("Q")
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["tools"][0]["strict"] is True
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"
