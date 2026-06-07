"""Local implementation of DataStore: SQLite + filesystem."""
import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from .base import DataStore


class LocalDataStore(DataStore):
    
    def __init__(self, db_path: Path, raw_dir: Path):
        self.db_path = db_path
        self.raw_dir = raw_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()
    
    def _init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS teams (
                    team_id      INTEGER PRIMARY KEY,
                    abbreviation TEXT NOT NULL,
                    name         TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    season TEXT NOT NULL,
                    season_type TEXT NOT NULL,
                    game_date DATE NOT NULL,
                    home_team_id INTEGER NOT NULL,
                    away_team_id INTEGER NOT NULL,
                    home_pts INTEGER,
                    away_pts INTEGER,
                    home_won INTEGER,
                    neutral_site INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_games_season ON games(season);
                CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);

                CREATE TABLE IF NOT EXISTS team_game_stats (
                    game_id     TEXT    NOT NULL,
                    team_id     INTEGER NOT NULL,
                    is_home     INTEGER NOT NULL,
                    fgm         INTEGER,
                    fga         INTEGER,
                    fg3m        INTEGER,
                    fg3a        INTEGER,
                    ftm         INTEGER,
                    fta         INTEGER,
                    oreb        INTEGER,
                    dreb        INTEGER,
                    ast         INTEGER,
                    stl         INTEGER,
                    blk         INTEGER,
                    tov         INTEGER,
                    pf          INTEGER,
                    plus_minus  REAL,
                    PRIMARY KEY (game_id, team_id),
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tgs_team ON team_game_stats(team_id);
                CREATE INDEX IF NOT EXISTS idx_tgs_game ON team_game_stats(game_id);

                CREATE TABLE IF NOT EXISTS player_game_stats (
                    game_id     TEXT    NOT NULL,
                    player_id   INTEGER NOT NULL,
                    team_id     INTEGER NOT NULL,
                    is_home     INTEGER NOT NULL,
                    minutes     REAL,
                    started     INTEGER NOT NULL DEFAULT 0,
                    fgm         INTEGER,
                    fga         INTEGER,
                    fg3m        INTEGER,
                    fg3a        INTEGER,
                    ftm         INTEGER,
                    fta         INTEGER,
                    oreb        INTEGER,
                    dreb        INTEGER,
                    ast         INTEGER,
                    stl         INTEGER,
                    blk         INTEGER,
                    tov         INTEGER,
                    pf          INTEGER,
                    plus_minus  REAL,
                    PRIMARY KEY (game_id, player_id),
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                );
                CREATE INDEX IF NOT EXISTS idx_pgs_player ON player_game_stats(player_id);
                CREATE INDEX IF NOT EXISTS idx_pgs_team   ON player_game_stats(team_id);
                CREATE INDEX IF NOT EXISTS idx_pgs_game   ON player_game_stats(game_id);
            """)
    
    def save_teams(self, teams: pd.DataFrame) -> None:
        if teams.empty:
            return
        with sqlite3.connect(self.db_path) as conn:
            teams.to_sql("_teams_staging", conn, if_exists="replace", index=False)
            conn.execute("""
                INSERT OR REPLACE INTO teams (team_id, abbreviation, name)
                SELECT team_id, abbreviation, name FROM _teams_staging
            """)
            conn.execute("DROP TABLE _teams_staging")

    def save_games(self, games: pd.DataFrame) -> None:
        if games.empty:
            return
        with sqlite3.connect(self.db_path) as conn:
            # Temporary staging + INSERT OR REPLACE for idempotency
            games.to_sql("_games_staging", conn, if_exists="replace", index=False)
            conn.execute("""
                INSERT OR REPLACE INTO games 
                (game_id, season, season_type, game_date, 
                 home_team_id, away_team_id,
                 home_pts, away_pts, home_won, neutral_site)
                SELECT game_id, season, season_type, game_date,
                       home_team_id, away_team_id,
                       home_pts, away_pts, home_won, neutral_site
                FROM _games_staging
            """)
            conn.execute("DROP TABLE _games_staging")
    
    def load_games(
        self,
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        query = "SELECT * FROM games WHERE 1=1"
        params: list = []
        if season is not None:
            query += " AND season = ?"
            params.append(season)
        if start_date is not None:
            query += " AND game_date >= ?"
            params.append(start_date.isoformat())
        if end_date is not None:
            query += " AND game_date <= ?"
            params.append(end_date.isoformat())
        query += " ORDER BY game_date"
        
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(query, conn, params=params)
        
        # SQLite does not preserve date types
        if not df.empty:
            df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
        return df
    
    def existing_game_ids(self, season: str) -> set[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT game_id FROM games WHERE season = ?", (season,)
            ).fetchall()
        return {r[0] for r in rows}
    
    def save_team_game_stats(self, stats: pd.DataFrame) -> None:
        if stats.empty:
            return
        with sqlite3.connect(self.db_path) as conn:
            stats.to_sql("_tgs_staging", conn, if_exists="replace", index=False)
            conn.execute("""
                INSERT OR REPLACE INTO team_game_stats
                (game_id, team_id, is_home, fgm, fga, fg3m, fg3a, ftm, fta,
                 oreb, dreb, ast, stl, blk, tov, pf, plus_minus)
                SELECT game_id, team_id, is_home, fgm, fga, fg3m, fg3a, ftm, fta,
                       oreb, dreb, ast, stl, blk, tov, pf, plus_minus
                FROM _tgs_staging
            """)
            conn.execute("DROP TABLE _tgs_staging")

    def save_player_game_stats(self, stats: pd.DataFrame) -> None:
        if stats.empty:
            return
        with sqlite3.connect(self.db_path) as conn:
            stats.to_sql("_pgs_staging", conn, if_exists="replace", index=False)
            conn.execute("""
                INSERT OR REPLACE INTO player_game_stats
                (game_id, player_id, team_id, is_home, minutes, started,
                 fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb,
                 ast, stl, blk, tov, pf, plus_minus)
                SELECT game_id, player_id, team_id, is_home, minutes, started,
                       fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb,
                       ast, stl, blk, tov, pf, plus_minus
                FROM _pgs_staging
            """)
            conn.execute("DROP TABLE _pgs_staging")

    def save_raw_boxscore(self, game_id: str, payload: dict) -> None:
        target = self.raw_dir / f"{game_id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def load_team_game_stats(
        self,
        season: str | None = None,
        team_id: int | None = None,
    ) -> pd.DataFrame:
        """
        Loads team stats per game.

        The JOIN to games to filter by season is necessary because team_game_stats
        does not store season directly (it is derivable and we avoid redundancy).
        ORDER BY game_date ensures chronological order for rolling windows.
        """
        query = """
            SELECT tgs.*
            FROM team_game_stats tgs
            JOIN games g ON g.game_id = tgs.game_id
            WHERE 1=1
        """
        params: list = []
        if season is not None:
            query += " AND g.season = ?"
            params.append(season)
        if team_id is not None:
            query += " AND tgs.team_id = ?"
            params.append(team_id)
        query += " ORDER BY g.game_date"

        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(query, conn, params=params)

    def load_teams(self) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(
                "SELECT * FROM teams ORDER BY abbreviation", conn
            )

    def load_player_game_stats(
        self,
        season: str | None = None,
        team_id: int | None = None,
        player_id: int | None = None,
    ) -> pd.DataFrame:
        """
        Loads player stats per game.

        Same pattern as load_team_game_stats: JOIN to games to filter by season
        without storing season in the table (derivable). ORDER BY game_date for
        rolling windows in Phase 2.
        """
        query = """
            SELECT pgs.*
            FROM player_game_stats pgs
            JOIN games g ON g.game_id = pgs.game_id
            WHERE 1=1
        """
        params: list = []
        if season is not None:
            query += " AND g.season = ?"
            params.append(season)
        if team_id is not None:
            query += " AND pgs.team_id = ?"
            params.append(team_id)
        if player_id is not None:
            query += " AND pgs.player_id = ?"
            params.append(player_id)
        query += " ORDER BY g.game_date"

        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(query, conn, params=params)
