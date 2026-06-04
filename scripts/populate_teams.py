"""
Script: puebla la tabla teams con el catálogo oficial de los 30 equipos NBA.

Usa nba_api.stats.static, que devuelve los datos sin hacer una llamada HTTP
(los equipos están embebidos en la librería). Idempotente: re-correrlo es seguro.

Uso:
    python scripts/populate_teams.py
"""
import pandas as pd
from nba_api.stats.static import teams

from nba_predictor.storage import get_datastore


def main() -> None:
    all_teams = teams.get_teams()

    df = pd.DataFrame([
        {
            "team_id": t["id"],
            "abbreviation": t["abbreviation"],
            "name": t["full_name"],
        }
        for t in all_teams
    ])

    store = get_datastore()
    store.save_teams(df)

    loaded = store.load_teams()
    print(f"Equipos guardados: {len(loaded)}")
    print(loaded.to_string(index=False))


if __name__ == "__main__":
    main()
