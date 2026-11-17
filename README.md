# Worth Watching (spoiler-free NBA picks)

WEIGHTS = {
    "clutch": 20,      # tight game in the last 5 min of regulation / OT
    "comeback": 10,    # winner overcame a big deficit
    "lead_changes": 5,
    "star": 20,        # best individual game score of anyone
    "favorites": 25,   # your players / teams played, and played well
    "team_quality": 20,  # how good the two teams are (win %, early-season shrunk toward .500)

Tune `FAV_PLAYERS`, `FAV_TEAMS`, `WEIGHTS`, `DAYS_BACK` at the top of build.py.
`docs/debug.json` shows every game's score breakdown (it contains spoilers; don't open it before watching).
