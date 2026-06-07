import time

from nba_api.stats.endpoints import leaguegamefinder, boxscoretraditionalv2
from nba_api.stats.static import teams


def list_teams():
    """Lista los 30 equipos NBA con sus IDs."""
    all_teams = teams.get_teams()
    print(f"Total de equipos: {len(all_teams)}")
    for t in all_teams[:5]:
        print(f"  {t['id']}: {t['abbreviation']} - {t['full_name']}")
    return all_teams


def get_one_season_schedule(season: str = "2023-24"):
    """
    Descarga el calendario de una temporada.
    
    season: formato '2023-24' (la temporada que terminó en junio 2024).
    """
    print(f"\nDescargando calendario de {season}...")
    finder = leaguegamefinder.LeagueGameFinder(
        season_nullable=season,
        season_type_nullable="Regular Season",
        league_id_nullable="00",  # NBA (no G-League ni WNBA)
    )
    df = finder.get_data_frames()[0]
    print(f"Filas devueltas: {len(df)}")
    print(f"Columnas: {df.columns.tolist()}")
    print("\nPrimeras 3 filas:")
    print(df.head(3))
    return df


def get_one_boxscore(game_id: str):
    """
    Descarga el box score de un partido específico.
    
    game_id: string como '0022300001' (formato oficial NBA).
    """
    print(f"\nDescargando box score de {game_id}...")
    box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
    
    # El endpoint devuelve dos DataFrames: jugadores y equipos
    player_stats = box.player_stats.get_data_frame()
    team_stats = box.team_stats.get_data_frame()
    
    print("\n--- Stats de equipo ---")
    print(team_stats)
    
    print("\n--- Stats de jugadores (primeros 5) ---")
    print(player_stats.head(5))
    
    return player_stats, team_stats


if __name__ == "__main__":
    # 1. Equipos
    list_teams()
    
    # 2. Calendario de una temporada
    schedule = get_one_season_schedule("2023-24")
    
    # 3. Un box score (tomamos el primer game_id del calendario)
    # Pausa para ser educados con la API
    time.sleep(1)
    
    # NOTA: cada partido aparece DOS veces en LeagueGameFinder (una por equipo).
    # Por eso filtramos game_ids únicos.
    sample_game_id = schedule["GAME_ID"].iloc[0]
    print(f"\nGame ID de ejemplo: {sample_game_id}")
    
    get_one_boxscore(sample_game_id)