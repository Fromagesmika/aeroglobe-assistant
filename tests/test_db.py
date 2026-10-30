import pytest

from assistant.db import UnsafeQueryError, connect, describe_schema, run_query, validate_sql


@pytest.fixture(scope="module")
def con():
    return connect()


def test_row_counts(con):
    assert run_query(con, "SELECT COUNT(*) AS n FROM fait_ventes")["n"][0] == 450_000
    assert run_query(con, "SELECT COUNT(*) AS n FROM fait_operations")["n"][0] == 46_930
    assert run_query(con, "SELECT COUNT(*) AS n FROM fait_satisfaction")["n"][0] == 33_057


def test_dates_are_typed(con):
    df = run_query(con, "SELECT typeof(DateVenteID) AS t FROM fait_ventes LIMIT 1")
    assert df["t"][0] == "DATE"


def test_routes_view(con):
    df = run_query(con, "SELECT Route FROM v_routes WHERE VolID = 1")
    assert df["Route"][0] == "CDG → ORY"


def test_delay_minutes(con):
    # Premier vol du jeu : prévu 08:46, parti 08:58 -> 12 minutes
    df = run_query(con, "SELECT RetardDepartMin FROM v_operations WHERE OperationID = 1")
    assert df["RetardDepartMin"][0] == 12


def test_delays_are_plausible(con):
    df = run_query(con, "SELECT MIN(RetardDepartMin) AS mn, MAX(RetardDepartMin) AS mx FROM v_operations")
    assert -720 <= df["mn"][0] <= df["mx"][0] <= 720


def test_cancelled_flights_have_no_delay(con):
    df = run_query(con, "SELECT COUNT(*) AS n FROM v_operations WHERE StatutVol = 'Annulé' AND RetardDepartMin IS NOT NULL")
    assert df["n"][0] == 0


def test_row_limit(con):
    assert len(run_query(con, "SELECT * FROM fait_ventes", max_rows=10)) == 10


@pytest.mark.parametrize("sql", [
    "DROP TABLE fait_ventes",
    "SELECT 1; DROP TABLE fait_ventes",
    "DELETE FROM dim_client",
    "SELECT * FROM read_csv_auto('C:/Windows/win.ini')",
    "COPY fait_ventes TO 'out.csv'",
    "ATTACH 'x.db'",
    "",
])
def test_guard_rejects(sql):
    with pytest.raises(UnsafeQueryError):
        validate_sql(sql)


def test_guard_accepts_cte_and_comments():
    sql = "-- top routes\nWITH t AS (SELECT VolID FROM dim_vol) SELECT * FROM t;"
    assert validate_sql(sql).startswith("WITH")


def test_external_access_disabled(con):
    with pytest.raises(Exception):
        con.execute("SELECT * FROM read_csv_auto('data/raw/Dim_Canal.csv')").fetchall()


def test_schema_description_lists_values(con):
    text = describe_schema(con)
    assert "v_operations" in text and "Platinum" in text and "Retardé" in text
