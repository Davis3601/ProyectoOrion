import time

from nba_api.stats.endpoints import leaguegamefinder, boxscoretraditionalv2
from nba_api.stats.static import teams


def list_teams():
    """Lists the 30 NBA teams with their IDs."""
    all_teams = teams.get_teams()
    print(f"Total teams: {len(all_teams)}")
    for t in all_teams[:5]:
        print(f"  {t['id']}: {t['abbreviation']} - {t['full_name']}")
    return all_teams


def get_one_season_schedule(season: str = "2023-24"):
    """
    Downloads the schedule for a season.
    
    Args:
        season: format '2023-24' (the season that ended in June 2024).
    """
    print(f"\nDownloading schedule for {season}...")
    finder = leaguegamefinder.LeagueGameFinder(
        season_nullable=season,
        season_type_nullable="Regular Season",
        league_id_nullable="00",  # NBA (not G-League or WNBA)
    )
    df = finder.get_data_frames()[0]
    print(f"Rows returned: {len(df)}")
    print(f"Columns: {df.columns.tolist()}")
    print("\nFirst 3 rows:")
    print(df.head(3))
    return df


def get_one_boxscore(game_id: str):
    """
    Downloads the box score for a specific game.
    
    Args:
        game_id: string like '0022300001' (official NBA format).
    """
    print(f"\nDownloading box score for {game_id}...")
    box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
    
    # The endpoint returns two DataFrames: players and teams
    player_stats = box.player_stats.get_data_frame()
    team_stats = box.team_stats.get_data_frame()
    
    print("\n--- Team Stats ---")
    print(team_stats)
    
    print("\n--- Player Stats (first 5) ---")
    print(player_stats.head(5))
    
    return player_stats, team_stats


if __name__ == "__main__":
    # 1. Teams
    list_teams()
    
    # 2. Season schedule
    schedule = get_one_season_schedule("2023-24")
    
    # 3. A box score (take the first game_id from the schedule)
    # Pause to be polite with the API
    time.sleep(1)
    
    # NOTE: each game appears TWICE in LeagueGameFinder (one per team).
    # That's why we filter unique game_ids.
    sample_game_id = schedule["GAME_ID"].iloc[0]
    print(f"\nSample Game ID: {sample_game_id}")
    
    get_one_boxscore(sample_game_id)
