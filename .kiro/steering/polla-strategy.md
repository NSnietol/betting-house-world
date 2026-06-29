# Polla Mundialista — Strategy Notes

## Current Status (Jun 27, 2026)
- Position: #12 of 23 (started at #20, improved with model adjustment)
- Points: 214 (as of Jun 24 verification)
- Model: tournament-adjusted predictions with more goals + draws for balanced matches
- Phase: Group stage complete, knockout rounds starting

## Key Learnings from Group Stage

1. **Never override model predictions manually** — Lost 8 pts on Egypt-Iran by changing 1-1 to 3-1 (result was 1-1). The model had it right.

2. **The old model (always 1-0/0-1) was terrible** — 2.8 pts/partido. The adjusted model with more goals averages 5.6 pts/partido.

3. **This tournament has ~35-40% draws** — Much higher than historical 22%. The model accounts for this by predicting 1-1 when P(home) is between 40-50%.

4. **Bookmakers are also losing on draws** — It's not a model error, it's a tournament characteristic. Don't second-guess empates.

5. **Correct adjustments = changing parameters, not individual outputs** — When we changed the goal thresholds (process), we gained 8 positions. When the user changed one prediction (output), lost 8 pts.

## Knockout Phase Considerations (PENDING DECISION)

- Points double in knockout: trend=10, goals=4 each, diff=2, max=20
- Only 90 minutes count (no extra time, no penalties)
- Knockout matches are historically tighter (fewer goals, fewer blowouts)
- Empates in 90 min are still possible and worth predicting (~25-30% in knockouts)
- The `--knockout` flag is ready in the pipeline
- May need to adjust `_tournament_adjusted_prediction` thresholds:
  - Currently: P(home)>75% → 3-0. In knockouts maybe → 2-0
  - Currently: P(home)>60% → 2-0. In knockouts maybe → 1-0 or 2-1
  - Draws (1-1) might become MORE valuable because 10 pts for trend alone
- **Decision deferred until we see knockout odds and first results**

## Operational Rules

- Always run model with fresh odds (<6h before kickoff when possible)
- Never overwrite past predictions
- Filter predictions by date before submitting
- Trust the model — don't manual override
- Review performance after every ~10 matches
- Use `--retro` to collect results and validate
