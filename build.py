#!/usr/bin/env python3
"""Spoiler-free "what should I watch" page for the NBA.

Pulls finished games from the last DAYS_BACK days from NBA.com's public JSON feeds,
scores each one on YOUR criteria, and writes docs/index.html with the top TOP_N.
No scores, winners or box-score links ever reach the page.
"""
import html, json, os, re, sys, unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ───────────────────────── YOUR SETTINGS ─────────────────────────
DAYS_BACK = 2          # window: this many ET days before today
TOP_N = 5
SHOW_REASONS = True    # tags like "Comeback" / "Clutch finish" (mild hints, no winners/scores)

FAV_PLAYERS = ["Luka Doncic", "Victor Wembanyama", "Nikola Jokic", "LeBron James", "Alperen Sengun"]
FAV_TEAMS = []         # e.g. ["LAL", "SAS"]  (tricodes)

# Weights add up to 100. Tune to taste.
WEIGHTS = {
    "clutch": 20,      # tight game in the last 5 min of regulation / OT
    "comeback": 10,    # winner overcame a big deficit
    "lead_changes": 5,
    "star": 20,        # best individual game score of anyone
    "favorites": 25,   # your players / teams played, and played well
    "team_quality": 20,  # how good the two teams are (win %, early-season shrunk toward .500)
}
# ─────────────────────────────────────────────────────────────────

ET = ZoneInfo("America/New_York")
SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
PBP = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{}.json"
BOX = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{}.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (nba-watchlist)", "Referer": "https://www.nba.com/"}


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def clock_seconds(c):
    m = re.match(r"PT(?:(\d+)M)?(?:([\d.]+)S)?", c or "")
    return (int(m.group(1) or 0) * 60 + float(m.group(2) or 0)) if m else 0.0


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def game_score(s):
    return (s.get("points", 0) + 0.4 * s.get("fieldGoalsMade", 0) - 0.7 * s.get("fieldGoalsAttempted", 0)
            - 0.4 * (s.get("freeThrowsAttempted", 0) - s.get("freeThrowsMade", 0))
            + 0.7 * s.get("reboundsOffensive", 0) + 0.3 * s.get("reboundsDefensive", 0)
            + s.get("steals", 0) + 0.7 * s.get("assists", 0) + 0.7 * s.get("blocks", 0)
            - 0.4 * s.get("foulsPersonal", 0) - s.get("turnovers", 0))


def analyze_pbp(actions, periods):
    """Return clutch, comeback, lead_changes, final_margin from play-by-play."""
    timeline = []  # (period, secs_remaining, home, away)
    h = a = 0
    for ev in actions:
        if ev.get("scoreHome") not in (None, "") and ev.get("scoreAway") not in (None, ""):
            h, a = int(ev["scoreHome"]), int(ev["scoreAway"])
        timeline.append((ev["period"], clock_seconds(ev.get("clock")), h, a))
    if not timeline:
        return None
    h, a = timeline[-1][2], timeline[-1][3]
    home_won = h > a
    clutch_sec, lead_changes, worst, last_sign = 0.0, 0, 0, 0
    for i, (p, t, hh, aa) in enumerate(timeline):
        m = hh - aa
        sign = (m > 0) - (m < 0)
        if sign and last_sign and sign != last_sign:
            lead_changes += 1
        if sign:
            last_sign = sign
        worst = max(worst, -(m if home_won else -m))  # winner's deficit
        if i + 1 < len(timeline) and p >= 4 and t <= 300 and abs(m) <= 5:
            nxt = timeline[i + 1]
            if nxt[0] == p:
                clutch_sec += max(0, t - nxt[1])
    ot_windows = max(1, periods - 3)  # last 5 of Q4 + each OT
    return {
        "clutch": clamp(clutch_sec / (300 * ot_windows)),
        "comeback": clamp(worst / 20),
        "lead_changes": clamp(lead_changes / 15),
        "margin": abs(h - a),
    }


def analyze_box(box):
    g = box["game"]
    best, fav_val, fav_names = 0.0, 0.0, []
    favs = {norm(n) for n in FAV_PLAYERS}
    for side in ("homeTeam", "awayTeam"):
        team = g[side]
        if team["teamTricode"] in FAV_TEAMS:
            fav_val = max(fav_val, 0.4)
        for p in team["players"]:
            st = p.get("statistics", {})
            mins = clock_seconds(st.get("minutes")) / 60
            if mins < 1:
                continue
            gs = game_score(st)
            best = max(best, gs)
            if norm(p.get("name")) in favs and mins >= 15:
                v = 0.4 + 0.6 * clamp((gs - 10) / 25)
                if v > fav_val:
                    fav_val = v
                fav_names.append(p["name"])
    return {"star": clamp((best - 20) / 25), "favorites": fav_val, "fav_names": fav_names,
            "periods": g.get("period", 4)}


def team_quality(game):
    """0-1. Blends the two teams' average and weaker strength, so two good teams beat one great + one bad."""
    vals = []
    for side in ("homeTeam", "awayTeam"):
        w, l = game[side].get("wins", 0), game[side].get("losses", 0)
        wp = (w + 5) / (w + l + 10)  # shrink toward .500 early in the season
        vals.append(clamp((wp - 0.35) / 0.40))
    return 0.6 * sum(vals) / 2 + 0.4 * min(vals)


def score_game(game):
    gid = game["gameId"]
    box = analyze_box(get(BOX.format(gid)))
    pbp = analyze_pbp(get(PBP.format(gid))["game"]["actions"], box["periods"])
    if not pbp:
        return None
    clutch = 0.6 * pbp["clutch"] + 0.4 * (1 - clamp(pbp["margin"] / 15))
    parts = {"clutch": clutch, "comeback": pbp["comeback"], "lead_changes": pbp["lead_changes"],
             "star": box["star"], "favorites": box["favorites"],
             "team_quality": team_quality(game)}
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    tags = []
    if box["periods"] > 4: tags.append("Overtime")
    if parts["clutch"] >= 0.55: tags.append("Clutch finish")
    if parts["comeback"] >= 0.5: tags.append("Big comeback")
    if parts["lead_changes"] >= 0.6: tags.append("Back and forth")
    if parts["team_quality"] >= 0.7: tags.append("Contenders")
    if parts["star"] >= 0.6: tags.append("Monster individual night")
    for n in dict.fromkeys(box["fav_names"]): tags.append(f"{n} played")
    return {"score": round(total), "tags": tags, "parts": parts}


def finished_games():
    today = datetime.now(ET).date()
    wanted = {(today - timedelta(days=i)).strftime("%m/%d/%Y") for i in range(1, DAYS_BACK + 1)}
    out = []
    for day in get(SCHEDULE)["leagueSchedule"]["gameDates"]:
        if day["gameDate"].split(" ")[0] in wanted:
            out += [g for g in day["games"] if g["gameStatus"] == 3]
    return out


def render(items, window):
    cards = []
    for i, it in enumerate(items, 1):
        tags = "".join(f"<li>{html.escape(t)}</li>" for t in it["tags"]) if SHOW_REASONS else ""
        cards.append(f"""<article><div class="rank">{i}</div><div class="body">
<h2>{html.escape(it['away'])} <span>at</span> {html.escape(it['home'])}</h2>
<p class="date">{html.escape(it['date'])}</p><ul>{tags}</ul></div>
<div class="score" aria-label="Watch score {it['score']} out of 100"><b>{it['score']}</b><small>watch score</small></div></article>""")
    body = "\n".join(cards) or "<p class='empty'>No finished games in this window yet.</p>"
    updated = datetime.now(ET).strftime("%b %-d, %Y")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Worth Watching</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Barlow:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0e1a2b;--panel:#16263c;--ink:#e9eef5;--mute:#8fa3bd;--accent:#ffb703;--line:#25395a}}
@media(prefers-color-scheme:light){{:root{{--bg:#eef2f7;--panel:#fff;--ink:#0e1a2b;--mute:#566b87;--accent:#c47a00;--line:#d3dbe6}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 Barlow,system-ui,sans-serif}}
main{{max-width:680px;margin:0 auto;padding:48px 20px 64px}}
h1{{font:700 44px/1 'Barlow Condensed',sans-serif;margin:0 0 6px}}
.sub{{color:var(--mute);margin:0 0 32px}}
article{{display:grid;grid-template-columns:44px 1fr auto;gap:16px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:12px}}
.rank{{font:700 34px 'Barlow Condensed',sans-serif;color:var(--mute)}}
article:first-of-type .rank{{color:var(--accent)}}
h2{{font:700 28px/1.1 'Barlow Condensed',sans-serif;margin:0}}h2 span{{color:var(--mute);font-weight:600}}
.date{{margin:2px 0 8px;color:var(--mute);font-size:14px}}
ul{{display:flex;flex-wrap:wrap;gap:6px;list-style:none;margin:0;padding:0}}
li{{font-size:13px;border:1px solid var(--line);border-radius:99px;padding:1px 10px}}
.score{{text-align:center}}.score b{{display:block;font:700 40px/1 'Barlow Condensed',sans-serif;color:var(--accent)}}
.score small{{color:var(--mute);font-size:12px}}
.empty{{color:var(--mute)}}
@media(max-width:480px){{article{{grid-template-columns:32px 1fr;}}.score{{grid-column:2;text-align:left}}.score b{{display:inline;font-size:28px}}}}
</style></head><body><main>
<h1>Worth watching</h1>
<p class="sub">Top {len(items)} games from {html.escape(window)}. No scores, no winners. Updated {updated}.</p>
{body}
</main></body></html>"""


def main():
    games = finished_games()
    results = []
    for g in games:
        try:
            r = score_game(g)
        except Exception as e:
            print(f"skip {g['gameId']}: {e}", file=sys.stderr)
            continue
        if r:
            d = datetime.strptime(g["gameDateEst"].split("T")[0], "%Y-%m-%d")
            r.update(away=g["awayTeam"]["teamName"], home=g["homeTeam"]["teamName"],
                     date=d.strftime("%a, %b %-d"))
            results.append(r)
    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:TOP_N]
    today = datetime.now(ET).date()
    window = f"{(today - timedelta(days=DAYS_BACK)).strftime('%b %-d')} to {(today - timedelta(days=1)).strftime('%b %-d')}"
    os.makedirs("docs", exist_ok=True)
    open("docs/index.html", "w", encoding="utf-8").write(render(top, window))
    json.dump(results, open("docs/debug.json", "w"), indent=1)  # for tuning; delete if you like
    print(f"{len(games)} games scored, wrote top {len(top)}")


if __name__ == "__main__":
    main()
