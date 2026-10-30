"""Interface Streamlit : poser une question en français sur les données AeroGlobe."""

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from assistant import demo  # noqa: E402
from assistant.agent import ClaudeAgent, GeminiAgent, available_provider, make_agent  # noqa: E402
from assistant.charts import build_chart  # noqa: E402
from assistant.db import connect  # noqa: E402
from app_examples import EXAMPLES  # noqa: E402

st.set_page_config(page_title="AeroGlobe · Assistant data", page_icon="✈️", layout="wide")


@st.cache_resource
def get_connection():
    return connect()


PROVIDER = available_provider()
# ?demo=1 force le mode démo ; ?q=... pose une question au chargement (pratique pour partager un lien)
DEMO_MODE = PROVIDER is None or st.query_params.get("demo") == "1"
recorded = demo.load()
SHOW_DETAILS = st.query_params.get("details") == "1"  # ?details=1 déplie SQL et raisonnement
if "q" in st.query_params and "auto_asked" not in st.session_state:
    st.session_state.auto_asked = True
    st.session_state.pending = st.query_params["q"]


def get_agent():
    if "agent" not in st.session_state:
        st.session_state.agent = make_agent(get_connection(), PROVIDER)
    return st.session_state.agent


def render_result(res) -> None:
    if res.error:
        st.warning(res.error)
    st.markdown(res.answer)
    fig = build_chart(res.data, res.chart)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    if res.data is not None and not res.data.empty:
        with st.expander(f"Données ({len(res.data)} ligne(s))"):
            st.dataframe(res.data, use_container_width=True, hide_index=True)
    if res.sql:
        with st.expander("Requête SQL", expanded=SHOW_DETAILS):
            st.code(res.sql, language="sql")
    if res.steps:
        with st.expander(f"Raisonnement de l'agent ({len(res.steps)} requête(s))", expanded=SHOW_DETAILS):
            for i, step in enumerate(res.steps, 1):
                status = "✅" if step.ok else "⚠️"
                st.markdown(f"**{i}. {step.objectif}** {status} — {step.detail}")
                st.code(step.sql, language="sql")


st.session_state.setdefault("history", [])

# --- Barre latérale ---------------------------------------------------------
with st.sidebar:
    st.markdown("### ✈️ AeroGlobe Airways")
    st.caption(
        "Assistant IA qui répond en français aux questions sur les données de la compagnie "
        "(450 000 billets, 47 000 vols, 33 000 avis clients). Données fictives."
    )
    st.markdown("**Exemples de questions**")
    examples = list(recorded) if DEMO_MODE else EXAMPLES
    for q in examples:
        if st.button(q, use_container_width=True):
            st.session_state.pending = q
    st.divider()
    if st.button("Nouvelle conversation", use_container_width=True):
        st.session_state.pop("agent", None)
        st.session_state.history = []
        st.rerun()
    if DEMO_MODE:
        st.caption("Mode démo · réponses enregistrées · Base : DuckDB (lecture seule)")
    else:
        agent_cls = GeminiAgent if PROVIDER == "gemini" else ClaudeAgent
        current = st.session_state.get("agent")
        model = current.model if current else agent_cls.model
        st.caption(f"{agent_cls.provider} · {model} · Base : DuckDB (lecture seule)")
    st.caption("Projet réalisé par Michaël Lopez · ECE Paris")

# --- Contenu principal ------------------------------------------------------
st.title("Pose ta question aux données AeroGlobe")
st.caption("L'agent écrit et exécute lui-même les requêtes SQL, vérifie ses résultats, puis répond.")

if DEMO_MODE:
    if not recorded:
        st.error("Aucune clé API trouvée : ajoute GEMINI_API_KEY (gratuit) ou ANTHROPIC_API_KEY dans le fichier .env.")
        st.stop()
    st.info(
        "**Mode démo** : aucune clé API configurée. Les réponses ci-dessous ont été produites par l'agent "
        "lors d'une exécution réelle, puis enregistrées. Les tableaux et graphiques sont recalculés sur la base."
    )

for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant", avatar="✈️"):
        render_result(turn["result"])

placeholder = "Choisis une question dans la barre latérale" if DEMO_MODE else \
    "Ex. : Quelle route a le meilleur taux de remplissage en 2025 ?"
question = st.chat_input(placeholder, disabled=DEMO_MODE)
question = question or st.session_state.pop("pending", None)

if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant", avatar="✈️"):
        if DEMO_MODE:
            result = demo.replay(get_connection(), question)
        else:
            agent = get_agent()
            with st.spinner("L'agent interroge la base…"):
                try:
                    result = agent.ask(question)
                except Exception as exc:  # erreurs réseau, quota, clé invalide…
                    st.error(f"Erreur de l'API : {exc}")
                    st.stop()
            demo.record(question, result, agent.provider, agent.model)
        render_result(result)
    st.session_state.history.append({"question": question, "result": result})
