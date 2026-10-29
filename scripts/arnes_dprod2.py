"""Arnés local de D-PROD-2: ensaya el mensaje 2 con un PDF REAL y el modo R1 forzado.

Por qué existe (adjudicación f del diseño sellado): un dry-run del endpoint con
fecha futura no tiene injury report (0 sondas), así que jamás ejercita el split;
y antes del 16-nov no hay filas R1 con resultado para el marcador. Aquí:
  - SPLITS: el PDF publish archivado de --fecha (el snapshot que el propio
    endpoint guardó al publicar) pasa por EL MISMO _fetch_absences del endpoint
    (descubrimiento y descarga sustituidos por el blob de GCS), y los destacados
    se construyen con _attach_highlights forzando modo R1 y D-PROD-2 activo.
  - MARCADOR: con --lunes, la semana cerrada anterior se evalúa sobre las filas
    REALES de player_predictions_log. Como esas filas son de régimen (sin línea
    ni R1), se reporta el cálculo real del paquete y, aparte, una SIMULACIÓN
    declarada: línea derivada de pts_median (ceil - 0.5) y rango [mín, máx]
    del régimen contado como si fuera R1.

*** SOLO LECTURA DE LA NUBE. *** CloudDataStore construido EXPLÍCITAMENTE
(convención de scripts); no escribe en BigQuery ni en GCS (save_raw=False, sin
logs). El forzado de modo vive aquí, jamás en un parámetro del endpoint.

Uso:
    python scripts/arnes_dprod2.py --fecha 2026-10-27
    python scripts/arnes_dprod2.py --fecha 2026-10-26 --lunes
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", required=True)
    ap.add_argument("--sufijo", default="03_00PM", help="corte publish archivado")
    ap.add_argument("--lunes", action="store_true", help="ensaya también el marcador")
    args = ap.parse_args()

    import pandas as pd

    from nba_predictor.api import daily_predictions as dp
    from nba_predictor.api import weekly_scoreboard as ws
    from nba_predictor.api.player_highlights import derive_box_stats, pts_line
    from nba_predictor.config import settings
    from nba_predictor.ingestion import injury_report as ir
    from nba_predictor.ingestion.future_schedule import ScheduledGame
    from nba_predictor.storage.cloud import CloudDataStore

    sys.stdout.reconfigure(encoding="utf-8")
    fecha = date.fromisoformat(args.fecha)
    store = CloudDataStore(project_id=settings.gcp_project_id, dataset=settings.bq_dataset,
                           bucket_name=settings.gcs_bucket)

    blob = f"raw/injury_reports/{args.fecha}_{args.sufijo}.pdf"
    pdf = store._gcs.bucket(store.bucket_name).blob(blob).download_as_bytes()
    print(f"PDF: gs://{store.bucket_name}/{blob} ({len(pdf)} bytes)")

    teams = store.load_teams()
    tricodes = dp._tricode_map(teams)
    player_map = store.load_player_names()

    # El MISMO _fetch_absences del endpoint; solo se sustituye la red por el blob.
    ir.discover_latest_snapshot = lambda *a, **k: (blob, args.sufijo)
    ir.download_snapshot = lambda url: pdf
    _, nys, statuses, sin_resolver = dp._fetch_absences(
        target_date=fecha, store=store, norm_team_map=dp._build_norm_team_map(teams),
        player_map=player_map, max_requests=1, save_raw=False)
    outs = sum(1 for d in statuses.values() for s in d.values() if s == "Out")
    print(f"estatus: {sum(len(d) for d in statuses.values())} jugadores, {outs} Out, "
          f"NYS {len(nys)}, sin resolver {len(sin_resolver)}")

    games = store.load_games()
    del_dia = games[(pd.to_datetime(games["game_date"]).dt.date == fecha)
                    & games["game_id"].astype(str).str.startswith("002")]
    sched = [ScheduledGame(game_id=r.game_id, game_date=fecha, home_team_id=int(r.home_team_id),
                           away_team_id=int(r.away_team_id),
                           home_tricode=tricodes[int(r.home_team_id)],
                           away_tricode=tricodes[int(r.away_team_id)], season=r.season)
             for r in del_dia.itertuples()]
    preds = [dp.GamePrediction(home_tricode=s.home_tricode, away_tricode=s.away_tricode,
                               game_date=args.fecha, probability_home=0.5, home_absences=[],
                               away_absences=[], availability_flag=dp.AvailabilityFlag.OK,
                               model_version="arnes", game_id=s.game_id) for s in sched]
    extra: dict = {}
    modo, previa = dp._attach_highlights(
        store=store, target_date=fecha, scheduled=sched, game_preds=preds,
        statuses_by_tid=statuses, player_map=player_map, season=sched[0].season,
        tricode_by_tid=tricodes, extra=extra, force_mode="r1", force_dprod2=True)
    print(f"partidos {len(sched)} · modo forzado {modo} · marcador del paquete: "
          f"{extra.get('scoreboard')!r}")

    scoreboard = extra.get("scoreboard")
    if args.lunes:
        ini, fin = ws.closed_week(fecha)
        try:
            filas = store.load_player_predictions_log(ini, fin)
        except Exception as exc:
            # Antes del ALTER de D-PROD-2 la columna pts_line no existe: se leen
            # las mismas filas sin ella (pts_line = None), solo para el ensayo.
            print(f"  lector del paquete falló ({type(exc).__name__}); se lee sin pts_line")
            from google.cloud import bigquery

            sql = ("SELECT game_id, game_date, player_id, team_tricode, pts_median, pts_min, "
                   "pts_max, games_in_window, predicted_at_utc, rank FROM "
                   f"`{settings.gcp_project_id}.{settings.bq_dataset}.player_predictions_log` "
                   "WHERE game_date BETWEEN @d AND @h")
            cfg = bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("d", "DATE", ini),
                bigquery.ScalarQueryParameter("h", "DATE", fin)])
            filas = [dict(r.items(), pts_line=None)
                     for r in store._bq.query(sql, job_config=cfg).result()]
        record = ws.record_rows(filas)
        pgs = derive_box_stats(store.load_player_game_stats(season=sched[0].season))
        j = pgs[pgs["minutes"] > 0]
        played = {(str(g), int(p)): (int(t), float(x)) for g, p, t, x in
                  zip(j["game_id"], j["player_id"], j["team_id"], j["pts"])}
        real = ws.evaluate(record, played, tricodes, date(2026, 10, 20))
        print(f"semana {ini}..{fin}: {len(filas)} filas, {len(record)} de record")
        print(f"  REAL (paquete): {real} -> {ws.render(ini, fin, real)}")
        sim = [dict(r, pts_line=(pts_line(r["pts_median"])
                                 if r.get("games_in_window") == 10 else None))
               for r in record]
        simul = ws.evaluate(sim, played, tricodes, date(2000, 1, 1))
        scoreboard = ws.render(ini, fin, simul)
        print(f"  SIMULADO (línea desde mediana; [mín,máx] de régimen como R1): {simul}")

    r = dp.DailyResult(target_date=args.fecha, games=preds, feed_down=False,
                       feed_down_reason=None, model_version="arnes", highlights_mode=modo,
                       highlights_prev_season=previa, highlights_dprod2=True,
                       scoreboard=scoreboard)
    dp.apply_players_length_budget(r)
    msg = dp.format_players_message(r)
    splits = [(p.player_name, p.split.mate_name, p.split.n_with, p.split.n_without)
              for gp in preds for p in gp.players if p.split]
    print(f"escalón: {r.highlights_truncated or 'completo'} · {len(msg)} caracteres · "
          f"splits {len(splits)}: {splits}")
    print("\n----- MENSAJE 2 -----")
    print(msg)


if __name__ == "__main__":
    main()
