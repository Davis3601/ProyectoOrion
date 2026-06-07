# Initial Concept
A machine learning pipeline that predicts the probability of the home team winning a regular-season NBA game.

## Target Audience
- **Analysts & Bettors**: Seeking statistical edges and reliable game probabilities.
- **Automated Systems**: Consuming predictions programmatically via the API.
- **Casual Fans**: Looking for game insights and entertainment.

## Core Value Proposition
- **High Calibration & Accuracy**: Focus on reliable, well-calibrated probabilities that outperform baseline ELO models.
- **Advanced Statistical Features**: Leverages the "Four Factors" of basketball, offensive/defensive ratings, and schedule context (rest, back-to-backs) to capture game dynamics.
- **Strict Leakage Prevention**: Robust rolling window feature engineering that prevents temporal data leakage during training.

## Use Cases
- **On-Demand Predictions**: The Cloud Run API endpoint will allow users and systems to request prediction probabilities for specific matchups on demand.