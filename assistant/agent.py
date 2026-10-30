"""Agent texte → SQL : un LLM interroge les données AeroGlobe via un outil `run_sql`.

Deux fournisseurs interchangeables : Google Gemini (offre gratuite) et Claude (Anthropic).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from assistant.db import UnsafeQueryError, describe_schema, run_query

CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_EFFORT = "medium"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
# Modèle de secours si le premier est saturé (fréquent sur l'offre gratuite)
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
GEMINI_TIMEOUT_MS = 60_000
MAX_TOOL_TURNS = 8
PREVIEW_ROWS = 40
CHART_TYPES = ["bar", "horizontal_bar", "line", "pie", "none"]

SYSTEM_TEMPLATE = """Tu es l'assistant data d'AeroGlobe Airways, une compagnie aérienne fictive \
(données synthétiques du 01/01/2022 au 25/09/2026 ; l'année 2026 est incomplète). \
Tu réponds en français aux questions des équipes métier en interrogeant une base DuckDB.

Méthode :
- Utilise l'outil run_sql pour obtenir les chiffres. N'invente jamais une valeur : chaque nombre \
de ta réponse doit venir d'un résultat de requête.
- Explore si besoin (valeurs distinctes, volumes), puis vérifie ton résultat avant de conclure. \
Si une requête échoue, lis l'erreur et corrige-la.
- Dialecte DuckDB. Les dates sont de type DATE (DateVenteID, DateVolID, DateEnqueteID, DateID). \
Utilise year(), month(), date_trunc().
- Le chiffre d'affaires se calcule sur fait_ventes.MontantNet des billets au statut 'Payé', \
sauf si la question demande autre chose.
- Pour les retards, utilise la vue v_operations (RetardDepartMin, RetardArriveeMin, TauxRemplissage). \
Les retards valent NULL pour les vols annulés (et l'arrivée des vols déroutés). Un vol est 'Retardé' \
à partir de 15 minutes de retard au départ. \
Pour les routes lisibles (ex. 'CDG → JFK'), joins v_routes sur VolID.
- fait_satisfaction.VolID est vide pour les réclamations générales : exclus-les quand tu analyses \
par vol ou par route, et dis-le.
- Si une comparaison inclut 2026, précise que l'année est incomplète.
- Si la question sort du périmètre des données, dis-le simplement.

{final_instructions}
- answer : 2 à 4 phrases claires, avec les chiffres clés (arrondis, séparateur de milliers français).
- sql : la requête finale dont le résultat illustre la réponse (celle qui sera affichée en tableau).
- chart : le graphique le plus utile pour ce résultat (type "none" si un tableau suffit), \
avec x et y = noms exacts de colonnes du résultat de la requête sql.

Schéma de la base :
{schema}
"""

RUN_SQL_DESCRIPTION = (
    "Exécute une requête SQL en lecture seule (SELECT ou WITH) sur la base DuckDB AeroGlobe "
    f"et renvoie au plus {PREVIEW_ROWS} lignes du résultat, avec le nombre total de lignes. "
    "Une seule instruction par appel."
)
RUN_SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "Requête SQL DuckDB (SELECT ou WITH)."},
        "objectif": {"type": "string", "description": "Ce que la requête doit vérifier, en quelques mots."},
    },
    "required": ["sql", "objectif"],
    "additionalProperties": False,
}
CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": CHART_TYPES},
        "x": {"type": "string"},
        "y": {"type": "string"},
        "title": {"type": "string"},
    },
    "required": ["type", "x", "y", "title"],
    "additionalProperties": False,
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "sql": {"type": "string"}, "chart": CHART_SCHEMA},
    "required": ["answer", "sql", "chart"],
    "additionalProperties": False,
}


@dataclass
class Step:
    objectif: str
    sql: str
    ok: bool
    detail: str


@dataclass
class AgentResult:
    answer: str
    sql: str = ""
    data: pd.DataFrame | None = None
    chart: dict | None = None
    steps: list[Step] = field(default_factory=list)
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def _format_preview(df: pd.DataFrame, total: int) -> str:
    head = df.head(PREVIEW_ROWS)
    note = f"{total} ligne(s) au total" + (f", {len(head)} affichées" if total > len(head) else "")
    return f"{note}\n{head.to_csv(index=False)}"


class BaseAgent:
    """Logique commune : prompt, exécution des requêtes, mise en forme du résultat final."""

    provider = ""
    model = ""
    final_instructions = ""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self.system = SYSTEM_TEMPLATE.format(
            final_instructions=self.final_instructions, schema=describe_schema(con)
        )
        self.messages: list = []

    def execute_sql(self, args: dict, result: AgentResult) -> tuple[bool, str]:
        sql, objectif = args.get("sql", ""), args.get("objectif", "")
        try:
            df = run_query(self.con, sql)
        except (UnsafeQueryError, duckdb.Error) as exc:
            result.steps.append(Step(objectif, sql, False, str(exc)))
            return False, f"Erreur : {exc}"
        result.steps.append(Step(objectif, sql, True, f"{len(df)} ligne(s)"))
        return True, _format_preview(df, len(df))

    def finalize(self, payload: dict, result: AgentResult) -> AgentResult:
        result.answer = payload.get("answer", "") or "Pas de réponse."
        result.sql = payload.get("sql", "")
        chart = payload.get("chart") or {}
        result.chart = chart if chart.get("type") not in (None, "", "none") else None
        if result.sql:
            try:
                result.data = run_query(self.con, result.sql)
            except (UnsafeQueryError, duckdb.Error) as exc:
                result.error = f"La requête finale n'a pas pu être rejouée : {exc}"
        return result

    def ask(self, question: str) -> AgentResult:
        start = len(self.messages)
        try:
            result = self._ask(question)
        except Exception:
            # Un tour interrompu par une erreur d'API ne doit pas rester dans l'historique.
            del self.messages[start:]
            raise
        if self.messages and self._ends_with_user():
            # Tour inachevé (trop d'étapes) : on le retire pour garder un historique valide.
            del self.messages[start:]
        return result

    def _ask(self, question: str) -> AgentResult:
        raise NotImplementedError

    def _ends_with_user(self) -> bool:
        raise NotImplementedError

    def reset(self) -> None:
        self.messages = []


class ClaudeAgent(BaseAgent):
    """Claude : outil strict `run_sql` + réponse finale en JSON structuré."""

    provider = "Claude (Anthropic)"
    model = CLAUDE_MODEL
    final_instructions = "Quand tu as la réponse, termine par le JSON demandé :"

    def __init__(self, con, client=None):
        super().__init__(con)
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.tool = {
            "name": "run_sql",
            "description": RUN_SQL_DESCRIPTION,
            "strict": True,
            "input_schema": RUN_SQL_SCHEMA,
        }

    def _call(self):
        return self.client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=self.system,
            tools=[self.tool],
            messages=self.messages,
            output_config={"effort": CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    def _ends_with_user(self) -> bool:
        return self.messages[-1]["role"] == "user"

    def _ask(self, question: str) -> AgentResult:
        result = AgentResult(answer="")
        self.messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_TURNS + 1):
            response = self._call()
            result.input_tokens += response.usage.input_tokens
            result.output_tokens += response.usage.output_tokens
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                result.error = "La demande a été refusée par le modèle."
                result.answer = "Je ne peux pas répondre à cette demande."
                return result

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    ok, content = self.execute_sql(block.input, result)
                    tool_result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                    if not ok:
                        tool_result["is_error"] = True
                    tool_results.append(tool_result)
                self.messages.append({"role": "user", "content": tool_results})
                continue

            if response.stop_reason == "max_tokens":
                result.error = "Réponse tronquée (limite de longueur atteinte)."
                result.answer = "La réponse était trop longue, reformule la question plus précisément."
                return result

            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                return self.finalize(json.loads(text), result)
            except json.JSONDecodeError:
                result.answer = text or "Pas de réponse."
                return result

        result.error = "Trop d'étapes sans réponse finale."
        result.answer = "Je n'ai pas réussi à conclure, essaie de reformuler la question."
        return result


class GeminiAgent(BaseAgent):
    """Gemini : outils `run_sql` et `repondre`, l'agent doit appeler un outil à chaque tour."""

    provider = "Google Gemini"
    model = GEMINI_MODEL
    final_instructions = "Quand tu as la réponse, appelle l'outil repondre avec :"

    def __init__(self, con, client=None):
        super().__init__(con)
        from google.genai import types

        if client is None:
            from google import genai

            client = genai.Client(
                api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
                http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
            )
        self.client = client
        self.model = GEMINI_MODEL
        self.types = types
        tools = types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="run_sql", description=RUN_SQL_DESCRIPTION, parameters_json_schema=RUN_SQL_SCHEMA
            ),
            types.FunctionDeclaration(
                name="repondre",
                description="Donne la réponse finale à l'utilisateur. À appeler une seule fois, à la fin.",
                parameters_json_schema=ANSWER_SCHEMA,
            ),
        ])
        self.config = types.GenerateContentConfig(
            system_instruction=self.system,
            tools=[tools],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
        )

    def _ends_with_user(self) -> bool:
        return self.messages[-1].role == "user"

    def _generate(self):
        """Appelle le modèle ; s'il est saturé, bascule (pour la suite) sur le modèle de secours."""
        from google.genai import errors

        try:
            return self.client.models.generate_content(model=self.model, contents=self.messages, config=self.config)
        except (errors.ServerError, errors.ClientError) as exc:
            saturated = isinstance(exc, errors.ServerError) or exc.code == 429
            if not saturated or self.model == GEMINI_FALLBACK_MODEL:
                raise
            self.model = GEMINI_FALLBACK_MODEL
            return self.client.models.generate_content(model=self.model, contents=self.messages, config=self.config)

    def _ask(self, question: str) -> AgentResult:
        types = self.types
        result = AgentResult(answer="")
        self.messages.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))

        for _ in range(MAX_TOOL_TURNS + 1):
            response = self._generate()
            usage = response.usage_metadata
            if usage is not None:
                result.input_tokens += usage.prompt_token_count or 0
                result.output_tokens += usage.candidates_token_count or 0

            candidate = response.candidates[0] if response.candidates else None
            if candidate is None or candidate.content is None:
                result.error = "Le modèle n'a pas renvoyé de réponse (filtrage ou erreur)."
                result.answer = "Je ne peux pas répondre à cette demande."
                return result
            self.messages.append(candidate.content)

            calls = [p.function_call for p in candidate.content.parts or [] if p.function_call]
            if not calls:
                result.answer = response.text or "Pas de réponse."
                return result

            final = next((c for c in calls if c.name == "repondre"), None)
            if final is not None:
                return self.finalize(dict(final.args or {}), result)

            parts = []
            for call in calls:
                ok, content = self.execute_sql(dict(call.args or {}), result)
                key = "output" if ok else "error"
                part = types.Part.from_function_response(name=call.name, response={key: content})
                if call.id:
                    part.function_response.id = call.id
                parts.append(part)
            self.messages.append(types.Content(role="user", parts=parts))

        result.error = "Trop d'étapes sans réponse finale."
        result.answer = "Je n'ai pas réussi à conclure, essaie de reformuler la question."
        return result


def available_provider() -> str | None:
    """Fournisseur choisi : LLM_PROVIDER s'il est défini, sinon la première clé trouvée."""
    wanted = (os.getenv("LLM_PROVIDER") or "").lower()
    has_gemini = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    has_claude = bool(os.getenv("ANTHROPIC_API_KEY"))
    if wanted in ("gemini", "claude"):
        return wanted
    if has_gemini:
        return "gemini"
    if has_claude:
        return "claude"
    return None


def make_agent(con: duckdb.DuckDBPyConnection, provider: str) -> BaseAgent:
    return GeminiAgent(con) if provider == "gemini" else ClaudeAgent(con)


# Compatibilité avec l'ancien nom
AeroGlobeAgent = ClaudeAgent
