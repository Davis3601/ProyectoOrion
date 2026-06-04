"""
Script: verifica la calidad de los box scores descargados.

Correr obligatoriamente tras la temporada de prueba y tras la descarga masiva.
Acepta --season para validar una temporada específica o valida todas si no se
especifica ninguna.

Uso:
    python scripts/sanity_check_boxscores.py --season 2023-24
    python scripts/sanity_check_boxscores.py
"""
import argparse
import sys

import pandas as pd

from nba_predictor.storage import get_datastore

# Forzar UTF-8 en la consola de Windows para caracteres como ⚠
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "-" * 60


def _check_structural_integrity(
    tgs: pd.DataFrame,
    played_ids: set[str],
    season_label: str,
) -> int:
    """
    CHECK 1: cada game_id en team_game_stats debe tener exactamente 2 filas
    (una por equipo), y el conjunto de game_ids debe coincidir con los partidos
    jugados en la tabla games.
    """
    print(f"\n{SEP}")
    print(f"CHECK 1 — Integridad estructural de team_game_stats  [{season_label}]")
    print(SEP)

    warnings = 0

    if tgs.empty:
        print("  ⚠ No hay datos en team_game_stats para este rango.")
        return 1

    rows_per_game = tgs.groupby("game_id").size()

    wrong = rows_per_game[rows_per_game != 2]
    if wrong.empty:
        print(f"  Partidos con exactamente 2 filas: {len(rows_per_game)}  [OK]")
    else:
        print(f"  ⚠ {len(wrong)} game_id(s) con número incorrecto de filas:")
        for gid, n in wrong.items():
            print(f"    {gid}: {n} filas")
        warnings += 1

    # Cobertura: game_ids en team_game_stats vs. partidos jugados en games
    downloaded_ids = set(rows_per_game.index)
    missing = played_ids - downloaded_ids
    extra = downloaded_ids - played_ids

    print(f"  Partidos jugados (games): {len(played_ids)}")
    print(f"  Partidos en team_game_stats: {len(downloaded_ids)}")

    if missing:
        print(f"  ⚠ {len(missing)} partidos jugados SIN box score descargado")
        warnings += 1
    else:
        print("  Cobertura: 100%  [OK]")

    if extra:
        print(f"  ⚠ {len(extra)} game_ids en team_game_stats que no están en games")
        warnings += 1

    return warnings


def _check_points_crosswalk(
    tgs: pd.DataFrame,
    games: pd.DataFrame,
    season_label: str,
) -> int:
    """
    CHECK 2: los puntos derivados de las stats crudas deben cuadrar exactamente
    con home_pts / away_pts de la tabla games.

    Fórmula (fgm ya incluye los triples):
        pts = (fgm - fg3m) * 2  +  fg3m * 3  +  ftm
            = fgm * 2 + fg3m + ftm
    """
    print(f"\n{SEP}")
    print(f"CHECK 2 — Cruce de puntos (stats crudas vs. tabla games)  [{season_label}]")
    print(SEP)

    warnings = 0

    if tgs.empty or games.empty:
        print("  ⚠ Sin datos suficientes para este check.")
        return 1

    tgs = tgs.copy()
    tgs["pts_derived"] = tgs["fgm"] * 2 + tgs["fg3m"] + tgs["ftm"]

    # Unir con games para comparar
    home_pts = (
        games[["game_id", "home_pts", "away_pts"]]
        .merge(
            tgs[tgs["is_home"] == 1][["game_id", "pts_derived"]],
            on="game_id",
            how="inner",
        )
        .rename(columns={"pts_derived": "home_derived"})
    )
    away_pts = (
        home_pts
        .merge(
            tgs[tgs["is_home"] == 0][["game_id", "pts_derived"]],
            on="game_id",
            how="inner",
        )
        .rename(columns={"pts_derived": "away_derived"})
    )

    home_mismatch = away_pts[away_pts["home_pts"] != away_pts["home_derived"]]
    away_mismatch = away_pts[away_pts["away_pts"] != away_pts["away_derived"]]

    n_checked = len(away_pts)
    print(f"  Partidos verificados: {n_checked}")

    if home_mismatch.empty and away_mismatch.empty:
        print("  Todos los puntos cuadran exactamente.  [OK]")
    else:
        if not home_mismatch.empty:
            print(f"  ⚠ {len(home_mismatch)} partido(s) con puntos LOCAL incorrectos:")
            for _, r in home_mismatch.head(5).iterrows():
                print(f"    {r['game_id']}: esperado {r['home_pts']}, derivado {r['home_derived']}")
            warnings += 1
        if not away_mismatch.empty:
            print(f"  ⚠ {len(away_mismatch)} partido(s) con puntos VISITANTE incorrectos:")
            for _, r in away_mismatch.head(5).iterrows():
                print(f"    {r['game_id']}: esperado {r['away_pts']}, derivado {r['away_derived']}")
            warnings += 1

    return warnings


def _check_players(
    pgs: pd.DataFrame,
    season_label: str,
) -> int:
    """
    CHECK 3: titulares (~10 por partido) y minutos en rango decimal válido.
    """
    print(f"\n{SEP}")
    print(f"CHECK 3 — Titulares y minutos en player_game_stats  [{season_label}]")
    print(SEP)

    warnings = 0

    if pgs.empty:
        print("  ⚠ No hay datos en player_game_stats para este rango.")
        return 1

    # Titulares por partido
    starters_per_game = pgs[pgs["started"] == 1].groupby("game_id").size()
    mean_starters = starters_per_game.mean()
    wrong_starters = starters_per_game[starters_per_game != 10]

    print(f"  Promedio de titulares por partido: {mean_starters:.2f}  (esperado ~10)")
    if not wrong_starters.empty:
        print(f"  ⚠ {len(wrong_starters)} partido(s) con ≠ 10 titulares:")
        for gid, n in wrong_starters.head(5).items():
            print(f"    {gid}: {n} titulares")
        warnings += 1
    else:
        print("  Todos los partidos tienen exactamente 10 titulares.  [OK]")

    # Minutos: rango válido
    # Máximo teórico: 48 min + 5 por cada prórroga. 53 cubre una prórroga.
    bad_minutes = pgs[pgs["minutes"].notna() & ((pgs["minutes"] < 0) | (pgs["minutes"] > 100))]
    if not bad_minutes.empty:
        print(f"  ⚠ {len(bad_minutes)} fila(s) con minutos fuera de rango [0, 100]")
        warnings += 1
    else:
        print("  Minutos en rango válido [0, 100].  [OK]")

    # minutes=NULL son DNP-banca (activados que no jugaron) — ~17% es esperado y correcto.
    # Alertar solo si la tasa es sospechosamente baja (<5%, sugeriría pérdida de filas)
    # o sospechosamente alta (>30%, sugeriría bug de parsing que nullifica minutos válidos).
    n_null_minutes = pgs["minutes"].isna().sum()
    pct_null = n_null_minutes / len(pgs) * 100
    if pct_null > 30:
        print(f"  ⚠ {n_null_minutes} filas con minutes=NULL ({pct_null:.1f}%) — tasa anormalmente alta, revisar parsing")
        warnings += 1
    elif pct_null < 5:
        print(f"  ⚠ {n_null_minutes} filas con minutes=NULL ({pct_null:.1f}%) — tasa anormalmente baja, ¿faltan DNP-banca?")
        warnings += 1
    else:
        print(f"  Filas con minutes=NULL: {n_null_minutes} ({pct_null:.1f}%)  [OK — DNP-banca esperado ~17%]")

    # Jugadores por partido (referencia)
    players_per_game = pgs.groupby("game_id").size()
    print(f"  Jugadores por partido: media={players_per_game.mean():.1f}, "
          f"min={players_per_game.min()}, max={players_per_game.max()}")

    return warnings


def _run_checks(store, seasons: list[str]) -> None:
    season_label = ", ".join(seasons) if len(seasons) <= 3 else f"{seasons[0]}–{seasons[-1]}"

    # Cargar datos de todas las temporadas solicitadas
    tgs_frames = []
    pgs_frames = []
    games_frames = []

    for s in seasons:
        tgs_frames.append(store.load_team_game_stats(season=s))
        pgs_frames.append(store.load_player_game_stats(season=s))
        g = store.load_games(season=s)
        games_frames.append(g[g["home_pts"].notna()])  # solo jugados

    tgs = pd.concat(tgs_frames, ignore_index=True) if tgs_frames else pd.DataFrame()
    pgs = pd.concat(pgs_frames, ignore_index=True) if pgs_frames else pd.DataFrame()
    games_played = pd.concat(games_frames, ignore_index=True) if games_frames else pd.DataFrame()
    played_ids = set(games_played["game_id"]) if not games_played.empty else set()

    print(f"\nTemporadas: {season_label}")
    print(f"Partidos jugados (games):      {len(played_ids)}")
    print(f"Filas en team_game_stats:      {len(tgs)}")
    print(f"Filas en player_game_stats:    {len(pgs)}")

    total_warnings = 0
    total_warnings += _check_structural_integrity(tgs, played_ids, season_label)
    total_warnings += _check_points_crosswalk(tgs, games_played, season_label)
    total_warnings += _check_players(pgs, season_label)

    print(f"\n{SEP}")
    if total_warnings == 0:
        print("RESULTADO: todos los checks pasaron sin advertencias.")
    else:
        print(f"RESULTADO: {total_warnings} advertencia(s) — revisar los checks marcados con ⚠.")
    print(SEP)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verifica la calidad de los box scores descargados."
    )
    parser.add_argument(
        "--season",
        nargs="+",
        metavar="SEASON",
        help="Una o más temporadas a verificar (e.g. --season 2023-24). "
             "Por defecto verifica todas las temporadas con datos.",
    )
    args = parser.parse_args()

    store = get_datastore()

    if args.season:
        seasons = args.season
    else:
        # Todas las temporadas que tengan datos en team_game_stats
        all_games = store.load_games()
        tgs_all = store.load_team_game_stats()
        if tgs_all.empty:
            print("No hay datos en team_game_stats. Ejecuta ingest_full_history.py primero.")
            return
        # Obtener temporadas cruzando con games
        tgs_game_ids = set(tgs_all["game_id"].unique())
        has_data = all_games[all_games["game_id"].isin(tgs_game_ids)]["season"].unique()
        seasons = sorted(has_data)

    _run_checks(store, seasons)


if __name__ == "__main__":
    main()
