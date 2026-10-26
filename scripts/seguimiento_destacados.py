"""Seguimiento diario de los destacados publicados contra el resultado real.

Instrumento (no experimento) del pre-registro D-EXP-7: automatiza la medición
manual de Antonio (2026-10-25). Para cada destacado de la SERVIDA DE RECORD
(regla congelada en D-PRE-1 addendum: la más cercana a las 13:00 CDMX dentro
de [12:50, 13:30] del día del partido; si no hay, la más temprana de ese día)
cruza player_predictions_log contra player_game_stats y reporta:
  - puntos dentro del RANGO publicado [pts_min, pts_max] (cobertura);
  - error absoluto de la MEDIANA publicada en pts, reb, ast y 3P;
  - casos no evaluables: no jugó (sin fila o 0 minutos) y equipo distinto
    (el jugador jugó con otro equipo que el publicado).

Solo se evalúan partidos cuyo game_date es posterior a predicted_at_utc (la
predicción se publicó antes del partido), misma regla que el grading del
criterio. Los destacados no tienen no-peeking: no hay criterio pre-registrado
sobre ellos.

*** SOLO LECTURA DE LA NUBE. *** CloudDataStore construido EXPLÍCITAMENTE
(convención de scripts); no escribe en BigQuery ni en GCS. Única salida:
data/seguimiento_destacados/{desde}_{hasta}.json y la tabla por consola.

Uso:
    python scripts/seguimiento_destacados.py                  # desde el 2026-10-21
    python scripts/seguimiento_destacados.py --desde 2026-10-20 --hasta 2026-10-24
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT_DIR = Path("data/seguimiento_destacados")
STATS = ("pts", "reb", "ast", "fg3m")

# SQL de la servida de record + cruce con el resultado real. Una sola consulta:
# los números los da BigQuery, la clasificación la hace classify_row (pura).
_SQL = """
WITH rec AS (
  SELECT l.*,
    ROW_NUMBER() OVER (
      PARTITION BY l.game_id, l.player_id
      ORDER BY
        IF(TIME(l.predicted_at_utc, 'America/Mexico_City')
           BETWEEN TIME '12:50:00' AND TIME '13:30:00', 0, 1),
        IF(TIME(l.predicted_at_utc, 'America/Mexico_City')
           BETWEEN TIME '12:50:00' AND TIME '13:30:00',
           ABS(TIME_DIFF(TIME(l.predicted_at_utc, 'America/Mexico_City'),
                         TIME '13:00:00', SECOND)),
           UNIX_SECONDS(l.predicted_at_utc))
    ) AS rn
  FROM `{t}.player_predictions_log` l
  WHERE l.game_date BETWEEN @desde AND @hasta
    AND DATE(l.predicted_at_utc, 'America/Mexico_City') = l.game_date
)
SELECT r.game_date, r.game_id, r.player_id, r.player_name, r.team_tricode,
       r.status_flag, r.rank, r.pts_median, r.pts_min, r.pts_max,
       r.reb_median, r.ast_median, r.fg3m_median, r.served_by,
       s.minutes, s.fgm, s.fg3m, s.ftm, s.oreb, s.dreb, s.ast,
       tm.abbreviation AS real_tricode,
       otro.abbreviation AS tricode_ese_dia
FROM rec r
LEFT JOIN `{t}.player_game_stats` s
  ON s.game_id = r.game_id AND s.player_id = r.player_id
LEFT JOIN `{t}.teams` tm ON tm.team_id = s.team_id
LEFT JOIN (
  SELECT ps.player_id, g.game_date, ANY_VALUE(t2.abbreviation) AS abbreviation
  FROM `{t}.player_game_stats` ps
  JOIN `{t}.games` g USING (game_id)
  JOIN `{t}.teams` t2 ON t2.team_id = ps.team_id
  WHERE g.game_date BETWEEN @desde AND @hasta
  GROUP BY 1, 2
) otro ON otro.player_id = r.player_id AND otro.game_date = r.game_date
WHERE r.rn = 1
ORDER BY r.game_date, r.game_id, r.team_tricode, r.rank
"""


def classify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Una fila de record + su resultado real -> veredicto. Función PURA.

    Sin fila en player_game_stats o con 0 minutos = no jugó. El partido aún
    sin ingestar también cae aquí: el llamador filtra por fecha.
    """
    out: dict[str, Any] = {
        "game_date": str(row["game_date"]), "game_id": row["game_id"],
        "player": row["player_name"], "team": row["team_tricode"],
        "rank": row.get("rank"), "status": row.get("status_flag"),
    }
    minutos = row.get("minutes")
    if row.get("real_tricode") is None:
        # No está en el partido publicado. Si ese día jugó con OTRO equipo, el
        # destacado estaba mal atribuido (traspaso no visto), no "no jugó".
        otro = row.get("tricode_ese_dia")
        if otro and otro != row["team_tricode"]:
            out["veredicto"] = "equipo_distinto"
            out["real_team"] = otro
        else:
            out["veredicto"] = "no_jugo"
        return out
    if not minutos:
        out["veredicto"] = "no_jugo"
        return out
    if row["real_tricode"] != row["team_tricode"]:
        out["veredicto"] = "equipo_distinto"
        out["real_team"] = row["real_tricode"]
        return out
    real = {
        "pts": 2 * row["fgm"] + row["fg3m"] + row["ftm"],
        "reb": row["oreb"] + row["dreb"],
        "ast": row["ast"],
        "fg3m": row["fg3m"],
    }
    out["veredicto"] = "evaluado"
    out["real"] = real
    out["pts_en_rango"] = bool(row["pts_min"] <= real["pts"] <= row["pts_max"])
    out["ancho_rango"] = float(row["pts_max"] - row["pts_min"])
    for st in STATS:
        med = row.get(f"{st}_median")
        out[f"err_{st}"] = None if med is None else abs(real[st] - float(med))
    return out


def summarize(evals: list[dict[str, Any]]) -> dict[str, Any]:
    """Agregados sobre los veredictos. Función PURA."""
    ev = [e for e in evals if e["veredicto"] == "evaluado"]
    res: dict[str, Any] = {
        "destacados": len(evals),
        "evaluados": len(ev),
        "no_jugo": sum(e["veredicto"] == "no_jugo" for e in evals),
        "equipo_distinto": sum(e["veredicto"] == "equipo_distinto" for e in evals),
    }
    if ev:
        dentro = sum(e["pts_en_rango"] for e in ev)
        res["pts_en_rango"] = dentro
        res["cobertura_pts"] = round(dentro / len(ev), 4)
        res["ancho_medio_pts"] = round(sum(e["ancho_rango"] for e in ev) / len(ev), 2)
        for st in STATS:
            errs = [e[f"err_{st}"] for e in ev if e[f"err_{st}"] is not None]
            res[f"mae_mediana_{st}"] = round(sum(errs) / len(errs), 3) if errs else None
    return res


def render(evals: list[dict[str, Any]]) -> str:
    """Tabla por día + total, legible por humanos (nombres, no ids)."""
    lines = ["día         dest  eval  noj  eq≠  pts∈rango  cobertura  MAEpts MAEreb MAEast"]
    dias = sorted({e["game_date"] for e in evals})
    for d in dias + ["TOTAL"]:
        sub = evals if d == "TOTAL" else [e for e in evals if e["game_date"] == d]
        s = summarize(sub)
        if s["evaluados"]:
            lines.append(
                f"{d:<11} {s['destacados']:>4}  {s['evaluados']:>4}  {s['no_jugo']:>3}  "
                f"{s['equipo_distinto']:>3}  {s['pts_en_rango']:>9}  {s['cobertura_pts']:>9.1%}  "
                f"{s['mae_mediana_pts']:>6} {s['mae_mediana_reb']:>6} {s['mae_mediana_ast']:>6}"
            )
        else:
            lines.append(f"{d:<11} {s['destacados']:>4}     0  (sin resultados aún)")
    otros = [e for e in evals if e["veredicto"] != "evaluado"]
    if otros:
        lines.append("")
        lines.append("no evaluables:")
        for e in otros:
            extra = f" -> jugó con {e['real_team']}" if e["veredicto"] == "equipo_distinto" else ""
            lines.append(f"  {e['game_date']}  {e['team']:<4} {e['player']:<28} "
                         f"{e['veredicto']}{extra}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2026-10-21")
    ap.add_argument("--hasta", default=None, help="por defecto, ayer")
    args = ap.parse_args()

    from datetime import timedelta

    from google.cloud import bigquery

    from nba_predictor.config import settings
    from nba_predictor.storage.cloud import CloudDataStore

    desde = date.fromisoformat(args.desde)
    hasta = date.fromisoformat(args.hasta) if args.hasta else date.today() - timedelta(days=1)
    # Lectura con CloudDataStore EXPLÍCITO (convención): solo se usa su cliente.
    store = CloudDataStore(settings.gcp_project_id, settings.bq_dataset, settings.gcs_bucket)
    sql = _SQL.format(t=f"{settings.gcp_project_id}.{settings.bq_dataset}")
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("desde", "DATE", desde),
        bigquery.ScalarQueryParameter("hasta", "DATE", hasta),
    ])
    filas = [dict(r.items()) for r in store._bq.query(sql, job_config=cfg).result()]
    evals = [classify_row(f) for f in filas]

    sys.stdout.reconfigure(encoding="utf-8")
    print(render(evals))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{desde}_{hasta}.json"
    out.write_text(json.dumps({"desde": str(desde), "hasta": str(hasta),
                               "resumen": summarize(evals), "detalle": evals},
                              ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n[detalle en {out}]")


if __name__ == "__main__":
    main()
