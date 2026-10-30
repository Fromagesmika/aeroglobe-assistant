"""Transforme la proposition de graphique de l'agent en figure Plotly."""

from __future__ import annotations

import pandas as pd
import plotly.express as px

NAVY = "#0B2545"
ACCENT = "#E4572E"
GREY = "#8DA9C4"


def _value_format(values: pd.Series) -> str:
    """Format d3 des étiquettes : entiers avec espaces, petites valeurs avec 1 à 2 décimales."""
    biggest = values.abs().max()
    if biggest >= 1000:
        return ",.0f"
    if biggest >= 100:
        return ",.1f"
    return ",.2f"


def build_chart(df: pd.DataFrame | None, spec: dict | None):
    """Renvoie une figure Plotly, ou None si le graphique ne colle pas aux données."""
    if df is None or df.empty or not spec:
        return None
    x, y, kind = spec.get("x"), spec.get("y"), spec.get("type")
    if x not in df.columns or y not in df.columns:
        return None
    if not pd.api.types.is_numeric_dtype(df[y]):
        return None

    data = df.head(30).copy()
    data[x] = data[x].astype(str)
    title = spec.get("title") or ""
    fmt = _value_format(data[y])

    if kind == "line":
        fig = px.line(data, x=x, y=y, markers=True, title=title, color_discrete_sequence=[NAVY])
        fig.update_traces(texttemplate=f"%{{y:{fmt}}}", textposition="top center", mode="lines+markers+text")
    elif kind == "pie":
        fig = px.pie(data, names=x, values=y, title=title,
                     color_discrete_sequence=[NAVY, ACCENT, GREY, "#13315C", "#C9D6E3"])
    else:
        horizontal = kind == "horizontal_bar"
        if horizontal:
            data = data.sort_values(y)
        # La barre la plus haute ressort en couleur d'accent, les autres restent sobres
        colors = [ACCENT if v == data[y].max() else NAVY for v in data[y]]
        fig = px.bar(data, x=y if horizontal else x, y=x if horizontal else y,
                     orientation="h" if horizontal else "v", title=title)
        fig.update_traces(marker_color=colors, texttemplate=f"%{{{'x' if horizontal else 'y'}:{fmt}}}",
                          textposition="outside", cliponaxis=False)

    fig.update_layout(
        margin=dict(l=10, r=30, t=50, b=10),
        plot_bgcolor="white",
        font=dict(family="Inter, Segoe UI, sans-serif", size=13, color=NAVY),
        title_font=dict(size=16),
        separators=", ",
        xaxis_title=None,
        yaxis_title=None,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#E5E9F0")
    return fig
