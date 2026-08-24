"""
Baselines de la Fase 3 — las varas del contrato.

Ambos baselines son obligatorios antes de entrenar cualquier modelo. Sin sus
números de log loss no hay referencia de si un modelo aporta realmente.

Baseline 1 — ConstantBaseline ("siempre gana local")
------------------------------------------------------
Predice una probabilidad CONSTANTE igual a la tasa histórica de victoria local
estimada SOLO con las temporadas de entrenamiento del fold. NO es predecir 1.0
(eso da log loss infinito cuando el visitante gana). Es la versión mínima del
principio "normalizaciones solo con pasado": la constante se re-estima fold a
fold usando únicamente el pasado disponible.

Por qué es el piso:
- Captura un hecho estructural (los locales ganan más), pero ignora cualquier
  información sobre la fortaleza relativa de los equipos.
- Si un modelo no supera este baseline, no está aprendiendo nada.

Baseline 2 — EloBaseline
--------------------------
ELO canónico (sin margen de victoria) con los parámetros cerrados del contrato:
K=20, home_adv=100, divisor=400, carryover 75/25 → 1505, init 1500.

Por qué el ELO es la vara que importa:
- ELO ES una regresión logística online con una sola feature (diferencia de
  rating) y coeficiente fijado por convención. La pregunta de investigación:
  ¿las 15 features de features_v1 contienen más información que este escalar
  actualizado secuencialmente?
- Los puntos ciegos del ELO que las features sí ven: ventaja de local
  declinante (fija en +100 vs. aprendida fold a fold), disponibilidad de
  jugadores esta noche, y CÓMO se ganó (eficiencia) vs. solo resultado binario.

Garantía anti-leakage del ELO:
- `compute_predictions` registra la predicción ANTES de actualizar con el
  resultado. El resultado del partido N nunca contamina la predicción del N.
- Cada partidos solo ve los resultados anteriores: cero leakage por construcción.

Procesamiento completo de la historia:
- El ELO procesa TODOS los partidos desde 2014-15 (warmup y primeros 15 de
  cada temporada incluidos) para que el rating esté bien calentado cuando se
  llega a las filas de features_v1. La evaluación usa solo las predicciones
  correspondientes a game_ids de features_v1 para comparabilidad exacta con
  los modelos futuros.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from nba_predictor.config import (
    ELO_DIVISOR,
    ELO_HOME_ADV,
    ELO_INITIAL_RATING,
    ELO_K,
    ELO_REGRESSION_MEAN,
    ELO_SEASON_CARRYOVER,
)


# ---------------------------------------------------------------------------
# Función pública de expectativa ELO (testeable independientemente)
# ---------------------------------------------------------------------------

def elo_expected(
    home_elo: float,
    away_elo: float,
    home_adv: float,
    divisor: float,
) -> float:
    """
    P(local gana) según la fórmula ELO estándar.

    P = 1 / (1 + 10^(−(home_elo + home_adv − away_elo) / divisor))

    home_adv = 0 en partidos de sede neutral.
    Con home_elo = away_elo y home_adv = 100, divisor = 400 → P ≈ 0.6401.
    """
    return 1.0 / (1.0 + 10.0 ** (-( home_elo + home_adv - away_elo) / divisor))


# ---------------------------------------------------------------------------
# Baseline 1 — Siempre gana local
# ---------------------------------------------------------------------------

@dataclass
class ConstantBaseline:
    """
    Predice la tasa histórica de victoria local del set de entrenamiento.

    Uso:
        baseline = ConstantBaseline()
        baseline.fit(train_df)                # estima p sobre train
        probs = baseline.predict_proba(val_df) # devuelve array constante
    """
    p_home_win: float = field(init=False, default=float("nan"))

    def fit(self, train_df: pd.DataFrame) -> None:
        """
        Estima la constante usando SOLO las temporadas de entrenamiento del fold.

        Regla anti-leakage: nunca llamar con datos del período de validación.
        La constante se re-estima en cada fold; usar el 0.5582 global sería
        usar el futuro (el total incluye temporadas de validación).
        """
        if train_df.empty:
            raise ValueError("train_df vacío — no se puede estimar la constante.")
        self.p_home_win = float(train_df["home_won"].mean())

    def predict_proba(self, val_df: pd.DataFrame) -> np.ndarray:
        """Devuelve un array de longitud len(val_df) con el valor constante."""
        if np.isnan(self.p_home_win):
            raise RuntimeError("Llama a fit() antes de predict_proba().")
        return np.full(len(val_df), self.p_home_win)


# ---------------------------------------------------------------------------
# Baseline 2 — ELO
# ---------------------------------------------------------------------------

@dataclass
class EloBaseline:
    """
    ELO canónico NBA sin margen de victoria.

    Parámetros (valores cerrados del contrato, ver CLAUDE.md):
        k              = 20    (K-factor; memoria ~10-15 partidos)
        home_adv       = 100   (puntos ELO al local; ≈64% entre iguales)
        divisor        = 400   (convención; interdependiente con k y home_adv)
        carryover      = 0.75  (regresión entre temporadas: 75% viejo + 25% mean)
        regression_mean= 1505  (media de regresión; ≈1500)
        init_rating    = 1500  (rating inicial de todos los equipos en 2014-15)

    Uso:
        elo = EloBaseline()
        preds = elo.compute_predictions(all_games, eval_game_ids=set(features["game_id"]))
        # preds: dict {game_id → P(local gana)}
    """
    k: float = ELO_K
    home_adv: float = ELO_HOME_ADV
    divisor: float = ELO_DIVISOR
    carryover: float = ELO_SEASON_CARRYOVER
    regression_mean: float = ELO_REGRESSION_MEAN
    init_rating: float = ELO_INITIAL_RATING

    def compute_predictions(
        self,
        all_games: pd.DataFrame,
        eval_game_ids: set[str] | frozenset[str],
    ) -> dict[str, float]:
        """
        Procesa todos los partidos cronológicamente y devuelve predicciones
        para los game_ids en eval_game_ids.

        Garantía anti-leakage: la predicción se registra ANTES de actualizar
        el rating con el resultado del partido. Cambiar el resultado de un
        partido no cambia su predicción (solo afecta los partidos posteriores).

        Regresión inter-temporada: al detectar un cambio de temporada (columna
        season), aplica elo = 0.75*elo + 0.25*1505 a todos los equipos ANTES
        de procesar el primer partido de la nueva temporada.

        Equipos sin historia previa reciben init_rating (1500) en su primer
        aparición. No es necesario pre-poblar los 30 equipos.

        Parameters
        ----------
        all_games : DataFrame con columnas game_id, season, game_date,
                    home_team_id, away_team_id, home_won, neutral_site.
                    Puede incluir cualquier temporada (warmup + training).
        eval_game_ids : game_ids para los cuales se desea la predicción.

        Returns
        -------
        dict {game_id: P(home wins)} para todos los game_ids en eval_game_ids
        que hayan sido procesados.
        """
        required = {"game_id", "season", "game_date", "home_team_id",
                    "away_team_id", "home_won", "neutral_site"}
        missing = required - set(all_games.columns)
        if missing:
            raise ValueError(f"all_games falta columnas: {sorted(missing)}")

        # Orden cronológico estricto (kind="stable" preserva orden original para mismo día)
        games_sorted = (
            all_games.dropna(subset=["home_won"])
            .sort_values("game_date", kind="stable")
            .reset_index(drop=True)
        )

        ratings: dict[int, float] = {}
        predictions: dict[str, float] = {}
        current_season: str | None = None

        for row in games_sorted.itertuples(index=False):
            # ----- Regresión inter-temporada -----
            if row.season != current_season:
                if current_season is not None:
                    for tid in list(ratings):
                        ratings[tid] = (
                            self.carryover * ratings[tid]
                            + (1.0 - self.carryover) * self.regression_mean
                        )
                current_season = row.season

            home_elo = ratings.get(row.home_team_id, self.init_rating)
            away_elo = ratings.get(row.away_team_id, self.init_rating)

            H = self.home_adv if row.neutral_site == 0 else 0.0
            expected = elo_expected(home_elo, away_elo, H, self.divisor)

            # ----- Registrar predicción ANTES de actualizar -----
            if row.game_id in eval_game_ids:
                predictions[row.game_id] = expected

            # ----- Actualizar (resultado ya conocido: home_won ∈ {0, 1}) -----
            delta = self.k * (float(row.home_won) - expected)
            ratings[row.home_team_id] = home_elo + delta
            ratings[row.away_team_id] = away_elo - delta

        return predictions
