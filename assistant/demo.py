"""Mode démo : rejoue des réponses enregistrées lors d'exécutions réelles de l'agent.

Permet de montrer l'appli sans clé API. L'interface signale clairement ce mode.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb

from assistant.agent import AgentResult, Step
from assistant.db import ROOT, UnsafeQueryError, run_query

DEMO_FILE = ROOT / "data" / "demo" / "reponses.json"


def load(path: Path = DEMO_FILE) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def record(question: str, result: AgentResult, provider: str, model: str, path: Path = DEMO_FILE) -> None:
    """Enregistre une réponse réussie (sans les données, rejouées depuis la base)."""
    if result.error or not result.sql:
        return
    entries = load(path)
    entries[question] = {
        "answer": result.answer,
        "sql": result.sql,
        "chart": result.chart,
        "steps": [s.__dict__ for s in result.steps],
        "provider": provider,
        "model": model,
        "date": date.today().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def replay(con: duckdb.DuckDBPyConnection, question: str, path: Path = DEMO_FILE) -> AgentResult | None:
    entry = load(path).get(question)
    if entry is None:
        return None
    result = AgentResult(
        answer=entry["answer"],
        sql=entry["sql"],
        chart=entry.get("chart"),
        steps=[Step(**s) for s in entry.get("steps", [])],
    )
    try:
        result.data = run_query(con, entry["sql"])
    except (UnsafeQueryError, duckdb.Error) as exc:
        result.error = f"La requête enregistrée n'a pas pu être rejouée : {exc}"
    return result
