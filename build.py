#!/usr/bin/env python3
"""Spoiler-free "what should I watch" page for the NBA.

Pulls finished games from the last DAYS_BACK days from ESPN's public JSON feeds,
scores each one on YOUR criteria, and writes docs/index.html with the top TOP_N.
No scores, winners or box-score links ever reach the page.
"""
import html, json, os, sys, time, unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ───────────────────────── YOUR SETTINGS ─────────────────────────
DAYS_BACK = 2          # window: this many ET days before today
TOP_N = 5
SHOW_REASONS = True    # tags like "Comeback" / "Clutch finish" (mild hints, no winners/scores)

FAV_PLAYERS = ["Luka Doncic", "Victor Wembanyama", "Nikola Jokic", "LeBron James", "Alperen Sengun"]
FAV_TEAMS = []         # ESPN abbreviations, e.g. ["LAL", "SA", "DEN"] (Spurs = SA, Knicks = NY)

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
# ESPN's public JSON (NBA.com blocks GitHub's servers, ESPN doesn't)
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
HEADERS = {"User-Agent": "Mozilla/5.0 (nba-watchlist)"}


def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def clock_seconds(c):
    """ESPN clocks look like '5:30' or '34.5'."""
    try:
        c = str(c)
        if ":" in c:
            m, sec = c.split(":")
            return int(m) * 60 + float(sec)
        return float(c)
    except (ValueError, TypeError):
        return 0.0


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def num(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return 0.0


def made_att(x):
    try:
        a, b = str(x).split("-")
        return float(a), float(b)
    except ValueError:
        return 0.0, 0.0


def game_score(st):
    fgm, fga = made_att(st.get("FG"))
    ftm, fta = made_att(st.get("FT"))
    return (num(st.get("PTS")) + 0.4 * fgm - 0.7 * fga - 0.4 * (fta - ftm)
            + 0.7 * num(st.get("OREB")) + 0.3 * num(st.get("DREB"))
            + num(st.get("STL")) + 0.7 * num(st.get("AST")) + 0.7 * num(st.get("BLK"))
            - 0.4 * num(st.get("PF")) - num(st.get("TO")))


def analyze_pbp(plays, home_won):
    """Clutch, comeback, lead changes, final margin from ESPN play-by-play."""
    timeline = []
    for pl in plays:
        if pl.get("homeScore") is None or pl.get("awayScore") is None:
            continue
        timeline.append((int(pl["period"]["number"]), clock_seconds(pl["clock"]["displayValue"]),
                         int(pl["homeScore"]), int(pl["awayScore"])))
    if not timeline:
        return None
    periods = max(t[0] for t in timeline)
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
    h, a = timeline[-1][2], timeline[-1][3]
    return {
        "clutch": clamp(clutch_sec / (300 * ot_windows)),
        "comeback": clamp(worst / 20),
        "lead_changes": clamp(lead_changes / 15),
        "margin": abs(h - a),
        "periods": periods,
    }


def analyze_box(summary, fav_team_codes):
    best, fav_val, fav_names = 0.0, 0.0, []
    favs = {norm(n) for n in FAV_PLAYERS}
    for team in summary["boxscore"]["players"]:
        if team["team"].get("abbreviation") in fav_team_codes:
            fav_val = max(fav_val, 0.4)
        for grp in team.get("statistics", []):
            cols = grp.get("labels") or grp.get("names") or []
            for ath in grp.get("athletes", []):
                if ath.get("didNotPlay") or not ath.get("stats"):
                    continue
                st = dict(zip(cols, ath["stats"]))
                mins = num(st.get("MIN"))
                if mins < 1:
                    continue
                gs = game_score(st)
                best = max(best, gs)
                name = ath["athlete"].get("displayName", "")
                if norm(name) in favs and mins >= 15:
                    fav_fit = 0.4 + 0.6 * clamp((gs - 10) / 25)
                    fav_val = max(fav_val, fav_fit)
                    fav_names.append(name)
    return {"star": clamp((best - 20) / 25), "favorites": fav_val, "fav_names": fav_names}


def team_quality(records):
    """0-1. Blends the two teams' average and weaker strength, so two good teams beat one great + one bad."""
    vals = []
    for rec in records:
        w, l = made_att(rec.replace("-", "-", 1))
        wp = (w + 5) / (w + l + 10)  # shrink toward .500 early in the season
        vals.append(clamp((wp - 0.35) / 0.40))
    return 0.6 * sum(vals) / 2 + 0.4 * min(vals)


def score_game(ev):
    comp = ev["competitions"][0]["competitors"]
    home = next(c for c in comp if c["homeAway"] == "home")
    away = next(c for c in comp if c["homeAway"] == "away")
    home_won = num(home.get("score")) > num(away.get("score"))
    summary = get(f"{BASE}/summary?event={ev['id']}")
    codes = {home["team"]["abbreviation"], away["team"]["abbreviation"]} & set(FAV_TEAMS)
    box = analyze_box(summary, codes)
    pbp = analyze_pbp(summary.get("plays", []), home_won)
    if not pbp:
        return None
    recs = [next((r["summary"] for r in c.get("records", []) if r.get("type") == "total"), "0-0")
            for c in (home, away)]
    clutch = 0.6 * pbp["clutch"] + 0.4 * (1 - clamp(pbp["margin"] / 15))
    parts = {"clutch": clutch, "comeback": pbp["comeback"], "lead_changes": pbp["lead_changes"],
             "star": box["star"], "favorites": box["favorites"], "team_quality": team_quality(recs)}
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    tags = []
    if pbp["periods"] > 4: tags.append("Overtime")
    if parts["clutch"] >= 0.55: tags.append("Clutch finish")
    if parts["comeback"] >= 0.5: tags.append("Big comeback")
    if parts["lead_changes"] >= 0.6: tags.append("Back and forth")
    if parts["team_quality"] >= 0.7: tags.append("Contenders")
    if parts["star"] >= 0.6: tags.append("Monster individual night")
    for n in dict.fromkeys(box["fav_names"]): tags.append(f"{n} played")
    when = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(ET)
    return {"score": round(total), "tags": tags, "parts": parts,
            "away": away["team"]["displayName"], "home": home["team"]["displayName"],
            "date": when.strftime("%a, %b %-d")}


def finished_games():
    today = datetime.now(ET).date()
    out = {}
    for i in range(1, DAYS_BACK + 1):
        day = (today - timedelta(days=i)).strftime("%Y%m%d")
        for ev in get(f"{BASE}/scoreboard?dates={day}&limit=100").get("events", []):
            if ev["status"]["type"].get("completed"):
                out[ev["id"]] = ev
    return list(out.values())


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
    for ev in games:
        try:
            r = score_game(ev)
        except Exception as e:
            print(f"skip {ev.get('id')}: {e!r}", file=sys.stderr)
            continue
        if r:
            results.append(r)
    if games and not results:
        sys.exit("Found games but could not score any; leaving the existing page untouched.")
    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:TOP_N]
    today = datetime.now(ET).date()
    window = f"{(today - timedelta(days=DAYS_BACK)).strftime('%b %-d')} to {(today - timedelta(days=1)).strftime('%b %-d')}"
    os.makedirs("docs", exist_ok=True)
    open("docs/index.html", "w", encoding="utf-8").write(render(top, window))
    json.dump(results, open("docs/debug.json", "w"), indent=1)  # for tuning; contains spoilers
    print(f"{len(games)} games found, {len(results)} scored, wrote top {len(top)}")


if __name__ == "__main__":
    main()
