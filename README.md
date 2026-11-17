# Worth Watching (spoiler-free NBA picks)

1. Create a GitHub repo and upload these files (keep the `.github/workflows` folder).
2. Settings > Pages > Source: "Deploy from a branch", branch `main`, folder `/docs`.
3. Actions tab > "Update watchlist" > Run workflow (first run). After that it runs every 2 days.
4. Your page: https://YOUR-USER.github.io/YOUR-REPO/

Tune `FAV_PLAYERS`, `FAV_TEAMS`, `WEIGHTS`, `DAYS_BACK` at the top of build.py.
`docs/debug.json` shows every game's score breakdown (it contains spoilers; don't open it before watching).
