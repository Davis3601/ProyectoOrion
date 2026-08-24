"""
Predicción en vivo de un partido NBA — Fase 5a.

CLI que orquesta: lookup de features → modelo del registry → probabilidad.
Registra cada predicción en data/predictions_log.jsonl (hito de la Fase 5a).

Uso:
    python scripts/predict_tonight.py --home BOS --away LAL --date 2026-10-22
    python scripts/predict_tonight.py --home BOS --away LAL --date 2026-10-22 \\
        --out "Jaylen Brown, Tatum" --version v1_logistic_bclean_2026-08-12

La disponibilidad es MANUAL v0 (Decisión 2, CLAUDE.md): el usuario lee el
injury report público y pasa --out con los ausentes de ambos equipos.

El log data/predictions_log.jsonl es el hito de validación prospectiva:
cada predicción queda fechada antes del tip-off, con la probabilidad exacta,
las features calculadas y la versión del modelo usada.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

# Añadir raíz del proyecto al path para importar en modo script
sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Predice la probabilidad de victoria del equipo local.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/predict_tonight.py --home BOS --away LAL --date 2026-10-22
  python scripts/predict_tonight.py --home MIL --away GSW --date 2026-10-22 \\
      --out "Damian Lillard, Khris Middleton"
        """,
    )
    parser.add_argument("--home", required=True, help="Abreviatura del equipo local (ej: BOS)")
    parser.add_argument("--away", required=True, help="Abreviatura del equipo visitante (ej: LAL)")
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        required=True,
        help="Fecha del partido (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Jugadores ausentes separados por coma (ambos equipos). "
             "Ej: --out 'Jaylen Brown, LeBron James'",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Versión del modelo del registry. Default: la más reciente.",
    )
    parser.add_argument(
        "--neutral",
        action="store_true",
        help="Marca el partido como sede neutral (anula ventaja local).",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("data/predictions_log.jsonl"),
        help="Ruta del log de predicciones JSONL. Default: data/predictions_log.jsonl",
    )

    args = parser.parse_args()

    # Parsear ausentes
    out_names = [n.strip() for n in args.out.split(",") if n.strip()] if args.out else []

    from nba_predictor.live.predict_game import predict_game

    try:
        result = predict_game(
            home_abbr=args.home,
            away_abbr=args.away,
            game_date=args.date,
            out_names=out_names,
            version_name=args.version,
            neutral_site=1 if args.neutral else 0,
            log_path=args.log,
        )
    except Exception as exc:
        log.error(f"Error al predecir: {exc}")
        sys.exit(1)

    # ── Reporte en consola ──
    prob = result["probability"]
    away_prob = 1.0 - prob

    print()
    print("=" * 60)
    print(f"  PREDICCIÓN: {result['home_team']} (local) vs {result['away_team']}")
    print(f"  Fecha     : {result['game_date']}")
    print(f"  Modelo    : {result['model_version']}")
    print("=" * 60)
    print(f"  P({result['home_team']} gana) = {prob:.1%}  |  P({result['away_team']} gana) = {away_prob:.1%}")
    print()

    if result["absent"]:
        print("  Ausentes declarados:")
        for a in result["absent"]:
            print(f"    - {a['name']} (id={a['player_id']}, team={a['team_id']})")
        print()

    print("  Features calculadas:")
    for feat, val in result["features"].items():
        print(f"    {feat:<22} = {val:+.5f}" if val is not None else f"    {feat:<22} = NaN")
    print()
    print(f"  Log guardado en: {args.log}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
