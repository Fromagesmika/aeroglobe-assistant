"""Couche données : charge le jeu AeroGlobe dans DuckDB (en mémoire, lecture seule)."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PARQUET_DIR = ROOT / "data" / "parquet"

# Fichier source -> nom de table exposé à l'agent
TABLES = {
    "Dim_Date": "dim_date",
    "Dim_Client": "dim_client",
    "Dim_Aeroport": "dim_aeroport",
    "Dim_Avion": "dim_avion",
    "Dim_Vol": "dim_vol",
    "Dim_Classe": "dim_classe",
    "Dim_Canal": "dim_canal",
    "Fait_Ventes_Billets": "fait_ventes",
    "Fait_Operations_Vol": "fait_operations",
    "Fait_Satisfaction_Client": "fait_satisfaction",
}

# Vues métier ajoutées par-dessus les tables brutes
VIEWS_SQL = """
CREATE VIEW v_routes AS
SELECT v.VolID, v.NumeroVol, v.TypeLigne, v.DistanceKM, v.DureeMoyenneMinutes,
       ad.CodeIATA AS CodeDepart, ad.Ville AS VilleDepart, ad.Pays AS PaysDepart,
       aa.CodeIATA AS CodeArrivee, aa.Ville AS VilleArrivee, aa.Pays AS PaysArrivee,
       ad.CodeIATA || ' → ' || aa.CodeIATA AS Route
FROM dim_vol v
JOIN dim_aeroport ad ON ad.AeroportID = v.AeroportDepartID
JOIN dim_aeroport aa ON aa.AeroportID = v.AeroportArriveeID;

CREATE VIEW v_operations AS
SELECT o.*,
       -- Les vols annulés (et l'arrivée des vols déroutés) n'ont pas de retard mesurable
       CASE WHEN o.HeureDepartReelle IS NULL OR o.StatutVol = 'Annulé' THEN NULL ELSE
         ((date_diff('minute', CAST(o.HeureDepartPrevue AS TIME), CAST(o.HeureDepartReelle AS TIME)) + 720 + 1440) % 1440) - 720
       END AS RetardDepartMin,
       CASE WHEN o.HeureArriveeReelle IS NULL OR o.StatutVol IN ('Annulé', 'Dérouté') THEN NULL ELSE
         ((date_diff('minute', CAST(o.HeureArriveePrevue AS TIME), CAST(o.HeureArriveeReelle AS TIME)) + 720 + 1440) % 1440) - 720
       END AS RetardArriveeMin,
       o.NombrePassagers * 1.0 / NULLIF(o.CapaciteDisponible, 0) AS TauxRemplissage
FROM fait_operations o;
"""

# Colonnes dont on montre les valeurs possibles à l'agent
CATEGORICAL = {
    "fait_operations": ["StatutVol"],
    "fait_ventes": ["StatutBillet"],
    "fait_satisfaction": ["TypeRetour", "MotifReclamation", "StatutResolution"],
    "dim_client": ["SegmentFidelite", "PaysResidence"],
    "dim_vol": ["TypeLigne"],
    "dim_classe": ["NomClasse"],
    "dim_canal": ["NomCanal"],
    "dim_avion": ["Modele"],
}

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|copy|attach|detach|install|load|pragma|set|reset|"
    r"export|import|call|checkpoint|vacuum|use|truncate|grant)\b"
    r"|\b(read_\w+|glob|sniff_csv|parquet_\w+)\s*\(",
    re.IGNORECASE,
)

QUERY_TIMEOUT_S = 20
MAX_ROWS = 1000


class UnsafeQueryError(ValueError):
    """Requête refusée par le garde-fou (écriture, accès fichier, plusieurs instructions)."""


def prepare_parquet(force: bool = False) -> None:
    """Convertit les CSV bruts en Parquet (plus léger, typé)."""
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    for src, table in TABLES.items():
        out = PARQUET_DIR / f"{table}.parquet"
        if out.exists() and not force:
            continue
        csv = (RAW_DIR / f"{src}.csv").as_posix()
        con.execute(
            f"COPY (SELECT * FROM read_csv_auto('{csv}', header=true)) "
            f"TO '{out.as_posix()}' (FORMAT parquet)"
        )
    con.close()


def connect() -> duckdb.DuckDBPyConnection:
    """Connexion en mémoire : tables chargées, puis accès externe coupé."""
    prepare_parquet()
    con = duckdb.connect()
    for table in TABLES.values():
        path = (PARQUET_DIR / f"{table}.parquet").as_posix()
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{path}')")
    con.execute(VIEWS_SQL)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def validate_sql(sql: str) -> str:
    """N'accepte qu'une seule requête SELECT / WITH, sans accès fichier ni écriture."""
    cleaned = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL).strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeQueryError("Requête vide.")
    if ";" in cleaned:
        raise UnsafeQueryError("Une seule instruction SQL est autorisée.")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise UnsafeQueryError("Seules les requêtes SELECT (ou WITH ... SELECT) sont autorisées.")
    if FORBIDDEN.search(cleaned):
        raise UnsafeQueryError("La requête contient une instruction ou une fonction non autorisée.")
    return cleaned


def run_query(con: duckdb.DuckDBPyConnection, sql: str, max_rows: int = MAX_ROWS) -> pd.DataFrame:
    """Exécute une requête validée, avec limite de lignes et délai maximal."""
    safe = validate_sql(sql)
    cur = con.cursor()
    timer = threading.Timer(QUERY_TIMEOUT_S, cur.interrupt)
    timer.start()
    try:
        return cur.execute(f"SELECT * FROM ({safe}) AS q LIMIT {max_rows}").df()
    finally:
        timer.cancel()
        cur.close()


def describe_schema(con: duckdb.DuckDBPyConnection) -> str:
    """Description texte du schéma (tables, colonnes, volumes, valeurs possibles)."""
    lines = []
    objects = list(TABLES.values()) + ["v_routes", "v_operations"]
    for name in objects:
        count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = con.execute(f"DESCRIBE {name}").fetchall()
        col_txt = ", ".join(f"{c[0]} {c[1]}" for c in cols)
        lines.append(f"- {name} ({count} lignes) : {col_txt}")
        for col in CATEGORICAL.get(name, []):
            values = [r[0] for r in con.execute(
                f"SELECT DISTINCT {col} FROM {name} WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 20"
            ).fetchall()]
            lines.append(f"    valeurs de {col} : {values}")
    return "\n".join(lines)
