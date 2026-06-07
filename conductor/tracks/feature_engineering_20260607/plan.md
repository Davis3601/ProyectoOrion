# Plan: Feature engineering

## Phase 1: Core Feature Infrastructure
- [x] Task: Set up base pipeline framework for feature generation and temporal windowing 2e0a345
    - [x] Write Tests
    - [x] Implement Feature
- [x] Task: Implement rolling average utility functions strictly without look-ahead 4518efd
    - [x] Write Tests
    - [x] Implement Feature
- [ ] Task: Conductor - User Manual Verification 'Core Feature Infrastructure' (Protocol in workflow.md)

## Phase 2: Implement Four Factors (Group 1)
- [ ] Task: Implement eFG% difference (`efg_diff`) and Turnover Rate difference (`tov_rate_diff`)
    - [ ] Write Tests
    - [ ] Implement Feature
- [ ] Task: Implement Offensive Rebound Rate difference (`oreb_rate_diff`) and Free Throw Rate difference (`ft_rate_diff`)
    - [ ] Write Tests
    - [ ] Implement Feature
- [ ] Task: Conductor - User Manual Verification 'Implement Four Factors (Group 1)' (Protocol in workflow.md)

## Phase 3: Implement Ratings & Context (Groups 2 & 4)
- [ ] Task: Implement rolling offensive, defensive, and net ratings differences
    - [ ] Write Tests
    - [ ] Implement Feature
- [ ] Task: Implement contextual features (`rest_diff`, `home_b2b`, `away_b2b`)
    - [ ] Write Tests
    - [ ] Implement Feature
- [ ] Task: Conductor - User Manual Verification 'Implement Ratings & Context (Groups 2 & 4)' (Protocol in workflow.md)
