# Initial Concept
A machine learning pipeline that predicts the probability of the home team winning a regular-season NBA game, using rolling team statistics derived from historical box score data.

# Product Definition

## Vision
The NBA Game Outcome Predictor aims to provide calibrated win probability predictions for regular-season NBA games. By leveraging an agentic ML workflow, it processes historical box scores and derives rolling "Four Factors" metrics to model game outcomes.

## Target Audience
- **Sports Bettors:** Seeking a statistical edge in predicting game outcomes.
- **Data Science Portfolio:** Serving as a comprehensive showcase of end-to-end ML engineering and MLOps practices.
- **Basketball Fans:** Providing deep analytical insights and win probabilities for game enthusiasts.

## Use Cases & Consumption
Predictions will be served via a Cloud Run HTTP endpoint, primarily consumed by:
- **Web Dashboard:** A visual user interface displaying upcoming games, team matchups, and predicted odds.
- **Automated Bots:** Scripts that programmatically consume the API for real-time alerts, downstream analytics, or automated actions.

## Core Features
- **Data Ingestion:** Automated fetching of schedules and box scores from the NBA API.
- **Feature Engineering:** Calculation of rolling strength features (like the Four Factors via moving averages), ensuring strict temporal ordering to prevent data leakage.
- **Predictive Modeling:** Binary classification models evaluated on log loss, outperforming baseline ELO models.
- **API Serving:** Reliable deployment of predictions via scalable Cloud Run services.