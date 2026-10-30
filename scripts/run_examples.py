"""Lance les questions d'exemple avec le vrai modèle et les enregistre pour le mode démo."""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app_examples import EXAMPLES  # noqa: E402
from assistant import demo  # noqa: E402
from assistant.agent import available_provider, make_agent  # noqa: E402
from assistant.db import connect  # noqa: E402

if __name__ == "__main__":
    questions = sys.argv[1:] or EXAMPLES
    con = connect()
    provider = available_provider()
    for q in questions:
        agent = make_agent(con, provider)
        t0 = time.time()
        try:
            res = agent.ask(q)
        except Exception as exc:
            print(f"\n### {q}\nERREUR API : {exc}")
            continue
        print(f"\n### {q}  ({agent.model}, {time.time() - t0:.0f}s, {res.input_tokens}+{res.output_tokens} tokens)")
        for i, s in enumerate(res.steps, 1):
            print(f"  {i}. {'OK ' if s.ok else 'ERR'} {s.objectif} -> {s.detail[:120]}")
        print("RÉPONSE :", res.answer)
        print("SQL :", " ".join(res.sql.split())[:300])
        print("GRAPHIQUE :", res.chart, "| lignes :", None if res.data is None else len(res.data))
        if res.data is not None:
            print(res.data.head(8).to_string())
        if res.error:
            print("ERREUR :", res.error)
        demo.record(q, res, agent.provider, agent.model)
