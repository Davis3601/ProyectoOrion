"""
Script: verifica la calidad de los calendarios descargados en la tabla games.

Usa get_datastore().load_games() para todos los checks, lo que también valida
que el repositorio funciona correctamente.
"""
from datetime import date

import pandas as pd

from nba_predictor.storage import get_datastore

_BUBBLE_START = date(2020, 7, 30)
_BUBBLE_END = date(2020, 8, 14)

# Temporadas que no completan 82 juegos por razones conocidas
_KNOWN_SHORT_SEASONS = {"2019-20", "2020-21"}

SEP = "-" * 60


def _check_game_counts(df: pd.DataFrame) -> int:
    """
    Verifica que cada temporada tenga el número correcto de partidos.

    Una temporada regular completa de 82 juegos tiene 1230 partidos (30 equipos × 82 / 2).
    Exceptuamos las temporadas COVID conocidas para no dar falsos positivos.
    """
    print(f"\n{SEP}")
    print("CHECK 1 — Conteo de partidos por temporada y tipo")
    print(SEP)

    counts = (
        df.groupby(["season", "season_type"])
        .size()
        .reset_index(name="n_games")
        .sort_values(["season", "season_type"])
    )

    warnings = 0
    for _, row in counts.iterrows():
        flag = ""
        if row["season_type"] == "Regular Season":
            n = row["n_games"]
            if row["season"] in _KNOWN_SHORT_SEASONS:
                flag = "  (temporada corta conocida)"
            elif not (1180 <= n <= 1260):
                flag = "  ⚠ FUERA DE RANGO [esperado 1180-1260]"
                warnings += 1
        print(f"  {row['season']:<10} {row['season_type']:<20} {row['n_games']:>5}{flag}")

    return warnings


def _check_home_win_rate(df: pd.DataFrame) -> int:
    """
    Verifica que la tasa de victoria local esté en rango histórico.

    El umbral mínimo es 0.50 en lugar del 0.53 usual para acomodar la burbuja 2020,
    donde no había ventaja de cancha real.
    """
    print(f"\n{SEP}")
    print("CHECK 2 — Tasa de victoria local (temporada regular, partidos jugados)")
    print(SEP)

    rs = df[df["season_type"] == "Regular Season"].copy()
    # home_won es NaN para partidos futuros (2025-26 no jugados aún)
    played = rs[rs["home_won"].notna()]

    by_season = played.groupby("season")["home_won"].mean().sort_index()

    warnings = 0
    for season, rate in by_season.items():
        flag = ""
        if rate < 0.50 or rate > 0.63:
            flag = "  ⚠ FUERA DE RANGO [esperado 0.50–0.63]"
            warnings += 1
        print(f"  {season:<10} {rate:.1%}{flag}")

    if not played.empty:
        overall = played["home_won"].mean()
        print(f"\n  {'TOTAL':<10} {overall:.1%}  (rango histórico ~57–59%)")

    return warnings


def _check_covid_bubble(df: pd.DataFrame) -> int:
    """
    Verifica que los partidos de la burbuja estén marcados con neutral_site=1.

    También confirma que ningún partido fuera de la ventana esté incorrectamente marcado.
    """
    print(f"\n{SEP}")
    print("CHECK 3 — Burbuja COVID (2020-07-30 a 2020-08-14)")
    print(SEP)

    warnings = 0

    bubble_mask = (df["game_date"] >= _BUBBLE_START) & (df["game_date"] <= _BUBBLE_END)
    bubble_games = df[bubble_mask]

    n_bubble = len(bubble_games)
    n_marked = int(bubble_games["neutral_site"].sum())

    ok = n_bubble == n_marked and n_bubble > 0
    status = "OK" if ok else "⚠ PROBLEMA"
    if not ok:
        warnings += 1

    print(f"  Partidos en ventana burbuja : {n_bubble:>4}  (esperados ~88)")
    print(f"  Con neutral_site = 1        : {n_marked:>4}  [{status}]")

    outside_wrong = int(df[~bubble_mask]["neutral_site"].sum())
    if outside_wrong > 0:
        print(f"  ⚠ Partidos fuera de burbuja con neutral_site=1: {outside_wrong}")
        warnings += 1
    else:
        print(f"  Partidos fuera de burbuja con neutral_site=1  : {outside_wrong:>4}  [OK]")

    return warnings


def _check_summary(df: pd.DataFrame) -> None:
    """Tabla combinada con las métricas principales por temporada regular."""
    print(f"\n{SEP}")
    print("CHECK 4 — Resumen global por temporada (temporada regular)")
    print(SEP)

    rs = df[df["season_type"] == "Regular Season"]

    summary = (
        rs.groupby("season")
        .agg(
            n_games=("game_id", "count"),
            home_win_rate=("home_won", "mean"),
            n_neutral=("neutral_site", "sum"),
            n_played=("home_pts", lambda x: x.notna().sum()),
        )
        .reset_index()
        .sort_values("season")
    )

    header = f"  {'Temporada':<10} {'Partidos':>10} {'%Local':>8} {'Neutral':>8} {'Jugados':>8}"
    print(header)
    print(f"  {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")

    for _, row in summary.iterrows():
        win_rate = f"{row['home_win_rate']:.1%}" if pd.notna(row["home_win_rate"]) else "N/A"
        print(
            f"  {row['season']:<10} {row['n_games']:>10} {win_rate:>8} "
            f"{int(row['n_neutral']):>8} {int(row['n_played']):>8}"
        )


def main() -> None:
    store = get_datastore()
    df = store.load_games()

    print(f"Total de registros en games: {len(df)}")
    print(f"Temporadas presentes: {sorted(df['season'].unique())}")

    total_warnings = 0
    total_warnings += _check_game_counts(df)
    total_warnings += _check_home_win_rate(df)
    total_warnings += _check_covid_bubble(df)
    _check_summary(df)

    print(f"\n{SEP}")
    if total_warnings == 0:
        print("RESULTADO: todos los checks pasaron sin advertencias.")
    else:
        print(f"RESULTADO: {total_warnings} advertencia(s) — revisar los checks marcados con ⚠.")
    print(SEP)


if __name__ == "__main__":
    main()
