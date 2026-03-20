
import html
import json
import re
from datetime import date, timedelta, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

APP_TITLE = "2026 Todaro March Madness"
DATA_PATH = Path(__file__).with_name("teams.json")
LOCKED_RESULTS_PATH = Path(__file__).with_name("locked_results.json")
BUILD_TIMESTAMP_CT = "Mar 19, 2026 7:04 PM CT"

TICKET_VALUES = {"Sweet 16": 1, "Elite 8": 2, "Elite Eight": 2, "Final Four": 3, "Championship": 4, "Champion": 4}
FIRST_ROUND_ORDER = [(1, 16), (8, 9), (5, 12), (4, 13), (6, 11), (3, 14), (7, 10), (2, 15)]

def ct_now():
    return datetime.now(ZoneInfo("America/Chicago"))

def ct_now_str():
    return ct_now().strftime("%b %d, %Y %I:%M:%S %p CT").replace(" 0", " ")

def load_data():
    if not DATA_PATH.exists():
        st.error("teams.json not found.")
        return {"teams": []}
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    for team in data.get("teams", []):
        team.setdefault("espn_team", "")
    return data

def lookup_name(team_row):
    return (team_row.get("espn_team", "") or "").strip() or (team_row.get("team", "") or "")

def normalize_team_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("&", " and ").replace("st.", "saint").replace("st ", "saint ").replace("(oh)", " ohio").replace("/", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def team_aliases(team_name: str):
    raw = (team_name or "").strip()
    aliases = {normalize_team_name(raw)}
    for p in [x.strip() for x in raw.split("/") if x.strip()]:
        aliases.add(normalize_team_name(p))
    manual = {
        "saint mary s": ["saint marys", "saint mary's"],
        "north carolina": ["unc"],
        "miami ohio smu": ["miami ohio", "smu"],
        "umbc howard": ["umbc", "howard"],
        "lehigh prairie view a m": ["lehigh", "prairie view a m", "prairie view am"],
        "texas nc state": ["texas", "nc state"],
        "saint john s": ["saint johns", "st john s", "st johns"],
        "south florida": ["usf"],
        "connecticut": ["uconn"],
    }
    nr = normalize_team_name(raw)
    if nr in manual:
        aliases.update(manual[nr])
    return aliases

def format_ct_datetime(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = pd.to_datetime(date_str, utc=True).tz_convert(ZoneInfo("America/Chicago"))
        return dt.strftime("%a %b %d, %I:%M %p CT").replace(" 0", " ")
    except Exception:
        return ""

def load_locked_results():
    if not LOCKED_RESULTS_PATH.exists():
        return {"games": [], "updated_at": ""}
    try:
        data = json.loads(LOCKED_RESULTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "games" in data:
            return data
    except Exception:
        pass
    return {"games": [], "updated_at": ""}

def save_locked_results(data):
    LOCKED_RESULTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

def game_key_from_names(name_a: str, name_b: str) -> str:
    return " | ".join(sorted([normalize_team_name(name_a), normalize_team_name(name_b)]))

def merge_finals_into_locked(games):
    locked = load_locked_results()
    existing = {g["key"]: g for g in locked.get("games", []) if "key" in g}
    changed = False
    for game in games:
        if str(game.get("status", "")).lower() != "final":
            continue
        teams = game.get("teams", [])
        if len(teams) != 2:
            continue
        key = game_key_from_names(teams[0]["team"], teams[1]["team"])
        if key not in existing:
            winner_name = next((t.get("team", "") for t in teams if t.get("winner")), "")
            existing[key] = {"key": key, "status": "Final", "detail": game.get("detail", ""), "ct_time": game.get("ct_time", ""), "teams": teams, "winner": winner_name}
            changed = True
    if changed:
        locked["games"] = sorted(existing.values(), key=lambda x: x.get("key", ""))
        locked["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        save_locked_results(locked)
    return locked

@st.cache_data(ttl=45, show_spinner=False)
def fetch_recent_espn():
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    team_map, games = {}, []
    for offset in range(0, 4):
        d = (ct_now().date() + timedelta(days=offset)).strftime("%Y%m%d")
        try:
            r = requests.get(url, params={"groups": 50, "limit": 300, "dates": d}, timeout=12)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for event in data.get("events", []):
            comps = event.get("competitions", [])
            if not comps:
                continue
            competitors = comps[0].get("competitors", [])
            if len(competitors) != 2:
                continue
            status = event.get("status", {}).get("type", {}).get("description", "") or ""
            detail = event.get("status", {}).get("type", {}).get("shortDetail", "") or ""
            ct_time = format_ct_datetime(event.get("date", "") or "")
            parsed = []
            for c in competitors:
                parsed.append({"team": c.get("team", {}).get("displayName", "") or "", "score": str(c.get("score", "") or ""), "winner": bool(c.get("winner", False))})
            game = {"status": status, "detail": detail, "ct_time": ct_time, "teams": parsed}
            games.append(game)
            priority = 2 if status.lower() == "final" else 1 if any(x in status.lower() for x in ["in progress", "halftime", "end of", "delayed"]) else 0
            for i, t in enumerate(parsed):
                opp = parsed[1 - i]
                key = normalize_team_name(t["team"])
                old = team_map.get(key, {"_priority": -1})
                if priority >= old["_priority"]:
                    team_map[key] = {"score": t["score"], "opp": opp["team"], "opp_score": opp["score"], "status": status, "detail": detail, "ct_time": ct_time, "winner": t["winner"], "game": game, "_priority": priority}
    for v in team_map.values():
        v.pop("_priority", None)
    return {"games": games, "team_map": team_map}

def get_live_for_team_name(name: str, live_map: dict):
    for alias in team_aliases(name):
        if alias in live_map:
            return live_map[alias]
    return None

def get_live_for_team(team_row, live_map: dict):
    return get_live_for_team_name(lookup_name(team_row), live_map)

def get_game_for_single_team(team_row, live_map: dict):
    info = get_live_for_team(team_row, live_map)
    return info.get("game") if info else None

def teams_match_game(team_a, team_b, game):
    if not team_a or not team_b or not game:
        return False
    aliases_a = team_aliases(lookup_name(team_a)); aliases_b = team_aliases(lookup_name(team_b))
    teams = game.get("teams", [])
    if len(teams) != 2:
        return False
    g0 = normalize_team_name(teams[0]["team"]); g1 = normalize_team_name(teams[1]["team"])
    return (g0 in aliases_a and g1 in aliases_b) or (g0 in aliases_b and g1 in aliases_a)

def matchup_game_for_card(team_a, team_b, live_map, recent_games, prefer_top_team=False):
    if not team_a and not team_b:
        return None
    if prefer_top_team and team_a:
        tg = get_game_for_single_team(team_a, live_map)
        if tg:
            return tg
    if team_a and team_b:
        for game in reversed(recent_games):
            if teams_match_game(team_a, team_b, game):
                return game
    if team_a:
        ga = get_game_for_single_team(team_a, live_map)
        if ga:
            return ga
    if team_b:
        gb = get_game_for_single_team(team_b, live_map)
        if gb:
            return gb
    return None

def decide_winner(team_a, team_b, recent_games, locked_games):
    if not team_a or not team_b:
        return None
    aliases_a = team_aliases(lookup_name(team_a)); aliases_b = team_aliases(lookup_name(team_b))
    for game in list(reversed(recent_games)) + list(reversed(locked_games)):
        if str(game.get("status", "")).lower() != "final":
            continue
        teams = game.get("teams", [])
        if len(teams) != 2:
            continue
        t0 = normalize_team_name(teams[0]["team"]); t1 = normalize_team_name(teams[1]["team"])
        if not ((t0 in aliases_a and t1 in aliases_b) or (t0 in aliases_b and t1 in aliases_a)):
            continue
        for t in teams:
            if t.get("winner"):
                wn = normalize_team_name(t["team"])
                if wn in aliases_a: return team_a
                if wn in aliases_b: return team_b
    return None

def person_color(name: str) -> str:
    if not str(name or "").strip():
        return "#f5f7fb"
    palette = ["#e8f1ff","#f6e8ff","#e9fff4","#fff3e8","#eef0ff","#ffeef5","#eefcf2","#fff9df","#edf7ff","#f3edff"]
    return palette[sum(ord(c) for c in str(name).strip().lower()) % len(palette)]

def safe(value):
    return html.escape(str(value or ""))

def tickets_for(row) -> int:
    return max(TICKET_VALUES.get(str(row.get("round_reached", "")).strip(), 0), TICKET_VALUES.get(str(row.get("manual_status", "")).strip(), 0))

def ticket_label(value: int) -> str:
    return f"{value} ticket" if value == 1 else f"{value} tickets"

def icon_html() -> str:
    return '<span class="money-icon">$</span><span class="money-bills">🎟️</span>'

def totals_by_name(df: pd.DataFrame):
    totals = {}
    for _, row in df.iterrows():
        name = str(row.get("assigned_name", "") or "").strip()
        if name:
            totals[name] = totals.get(name, 0) + tickets_for(row)
    return totals

def is_live_like(status: str) -> bool:
    s = (status or "").lower()
    return any(x in s for x in ["in progress", "halftime", "end of", "delayed"]) and "final" not in s

def matchup_info_line(team_a, team_b, live_map, recent_games, prefer_top_team=False):
    info = matchup_game_for_card(team_a, team_b, live_map, recent_games, prefer_top_team=prefer_top_team)
    if not info:
        return ""
    ct_time = str(info.get("ct_time", "") or "").strip()
    status = str(info.get("status", "") or "").strip()
    detail = str(info.get("detail", "") or "").strip()
    if not ct_time and not detail and not status:
        return ""
    label = "LIVE" if is_live_like(status) else "FINAL" if status.lower() == "final" else "SCHED"
    detail_html = f'<span class="matchup-detail">{safe(detail)}</span>' if detail else ""
    time_html = f'<span class="matchup-time">{safe(ct_time)}</span>' if ct_time else ""
    return f'<div class="matchup-meta"><span class="matchup-chip">{safe(label)}</span>{time_html}{detail_html}</div>'

def live_line(row, live_map):
    live = get_live_for_team(row, live_map)
    if not live:
        return ""
    game = live.get("game") or {}
    teams = game.get("teams", [])
    status = str(live.get("status", "") or "")
    if status.lower() == "scheduled":
        return ""
    label = "LIVE" if is_live_like(status) else ("W" if live.get("winner") else "L") if status.lower() == "final" else ""
    if not label:
        return ""
    my_aliases = team_aliases(lookup_name(row))
    score_text = ""
    if len(teams) == 2:
        t0, t1 = teams[0], teams[1]
        t0n = normalize_team_name(t0.get("team", "")); t1n = normalize_team_name(t1.get("team", ""))
        if t0n in my_aliases:
            score_text = f'{safe(t0.get("score",""))}-{safe(t1.get("score",""))} vs {safe(t1.get("team",""))}'
        elif t1n in my_aliases:
            score_text = f'{safe(t1.get("score",""))}-{safe(t0.get("score",""))} vs {safe(t0.get("team",""))}'
    if not score_text:
        score_text = f'{safe(live.get("score",""))}-{safe(live.get("opp_score",""))} vs {safe(live.get("opp",""))}'
    detail_html = f'<span class="live-detail">{safe(live.get("detail",""))}</span>' if str(live.get("detail","")).strip() else ""
    time_html = f'<span class="live-time">{safe(live.get("ct_time",""))}</span>' if str(live.get("ct_time","")).strip() else ""
    cls = "live-line final" if status.lower() == "final" else "live-line"
    return f'<div class="{cls}"><span class="live-chip">{safe(label)}</span><span>{score_text}</span>{time_html}{detail_html}</div>'

def team_line(row, live_map):
    if row is None:
        return '<div class="team-row tbd"><div class="team-main"><span class="seed">•</span><span class="team-name">TBD</span></div></div>'
    ticket_ct = tickets_for(row); badge = icon_html() if ticket_ct else ""
    status = safe(row.get("manual_status", ""))
    status_html = f'<div class="team-status">{badge}{status}</div>' if status else (f'<div class="team-status">{badge}{ticket_label(ticket_ct)}</div>' if ticket_ct else "")
    sub = []
    if str(row.get("assigned_name","")).strip(): sub.append(safe(row["assigned_name"]))
    if str(row.get("slot_note","")).strip(): sub.append(safe(row["slot_note"]))
    if str(row.get("round_reached","")).strip(): sub.append(safe(row["round_reached"]))
    sub_html = f'<div class="team-sub">{" • ".join(sub)}</div>' if sub else ""
    extra = " money-team" if ticket_ct else ""
    return f'<div class="team-row{extra}" style="background:{person_color(row.get("assigned_name",""))}"><div class="team-main"><span class="seed">{safe(row["seed"])}</span><span class="team-name">{safe(row["team"])}</span>{badge}</div>{sub_html}{status_html}{live_line(row, live_map)}</div>'

def matchup_card(top_row, bottom_row, live_map, recent_games, title="", tickets=0, prefer_top_team=False):
    title_html = ""
    if title:
        cls = "match-title money-round-title" if tickets else "match-title"
        label = ticket_label(tickets) if tickets else title
        title_html = f'<div class="{cls}">{icon_html() if tickets else ""}{safe(label)}</div>'
    extra = " money-round-card" if tickets else ""
    meta_html = matchup_info_line(top_row, bottom_row, live_map, recent_games, prefer_top_team=prefer_top_team)
    return f'<div class="match-card{extra}">{title_html}{meta_html}{team_line(top_row, live_map)}{team_line(bottom_row, live_map)}</div>'

def region_matchups(region_df):
    seed_to_row = {int(r["seed"]): r.to_dict() for _, r in region_df.iterrows()}
    return [(seed_to_row[a], seed_to_row[b]) for a, b in FIRST_ROUND_ORDER]

def build_region(region_df, region_name, live_map, recent_games, locked_games):
    m = region_matchups(region_df); placed = []; r64 = []
    for i, (a, b) in enumerate(m):
        placed.append(f'<div class="placed" style="grid-column:1;grid-row:{1+i*2} / span 1;">{matchup_card(a,b,live_map,recent_games,prefer_top_team=True)}</div>')
        r64.append(decide_winner(a,b,recent_games,locked_games))
    pairs32 = [(r64[0],r64[1]),(r64[2],r64[3]),(r64[4],r64[5]),(r64[6],r64[7])]
    w32 = []
    for i, (a,b) in enumerate(pairs32):
        placed.append(f'<div class="placed" style="grid-column:2;grid-row:{2+i*4} / span 1;">{matchup_card(a,b,live_map,recent_games,"Round of 32") if a and b else matchup_card(None,None,live_map,recent_games,"Round of 32")}</div>')
        w32.append(decide_winner(a,b,recent_games,locked_games) if a and b else None)
    pairs16 = [(w32[0],w32[1]),(w32[2],w32[3])]
    w16 = []
    for i, (a,b) in enumerate(pairs16):
        placed.append(f'<div class="placed" style="grid-column:3;grid-row:{4+i*8} / span 1;">{matchup_card(a,b,live_map,recent_games,"Sweet 16",1) if a and b else matchup_card(None,None,live_map,recent_games,"Sweet 16",1)}</div>')
        w16.append(decide_winner(a,b,recent_games,locked_games) if a and b else None)
    if w16[0] and w16[1]:
        elite = matchup_card(w16[0],w16[1],live_map,recent_games,"Elite 8",2)
        welite = decide_winner(w16[0],w16[1],recent_games,locked_games)
    else:
        elite = matchup_card(None,None,live_map,recent_games,"Elite 8",2); welite = None
    placed.append(f'<div class="placed" style="grid-column:4;grid-row:8 / span 1;">{elite}</div>')
    ff = matchup_card(welite,None,live_map,recent_games,"Final Four",3) if welite else matchup_card(None,None,live_map,recent_games,"Final Four",3)
    placed.append(f'<div class="placed" style="grid-column:5;grid-row:8 / span 1;">{ff}</div>')
    return f'<div class="region-section"><div class="region-name">{safe(region_name)}</div><div class="region-board">{"".join(placed)}</div></div>'

def matchup_list_card_html(team1, team2, meta, detail, label, score_line=""):
    cls = "matchup-list-card"
    if label == "LIVE": cls += " live"
    elif label == "FINAL": cls += " final"
    score_html = f'<div class="matchup-list-score">{safe(score_line)}</div>' if score_line else ""
    detail_html = f'<div class="matchup-list-detail">{safe(detail)}</div>' if detail else ""
    return f'<div class="{cls}"><div class="matchup-list-teams"><div><strong>{safe(team1)}</strong></div><div>vs</div><div><strong>{safe(team2)}</strong></div></div><div class="matchup-list-meta">{safe(meta)}</div>{score_html}{detail_html}</div>'

def render_matchup_list(df, live_map, recent_games):
    st.markdown("### Mobile-friendly game list")
    for region in ["South","West","East","Midwest"]:
        region_df = df[df["region"] == region]
        with st.expander(region, expanded=(region == "South")):
            for a, b in region_matchups(region_df):
                info = matchup_game_for_card(a,b,live_map,recent_games,prefer_top_team=True)
                label = ""; score_line = ""
                if info:
                    status = str(info.get("status","") or "")
                    label = "LIVE" if is_live_like(status) else "FINAL" if status.lower()=="final" else "SCHED"
                    meta = f"{label} · {info.get('ct_time','')}"
                    detail = info.get("detail","")
                    teams = info.get("teams", [])
                    if label in {"LIVE","FINAL"} and len(teams) == 2:
                        score_line = f"{teams[0].get('team','')} {teams[0].get('score','')} — {teams[1].get('score','')} {teams[1].get('team','')}"
                else:
                    meta = "No current game found"; detail = ""
                t1 = f"{a.get('team','TBD')} ({a.get('assigned_name','').strip() or 'Unassigned'})"
                t2 = f"{b.get('team','TBD')} ({b.get('assigned_name','').strip() or 'Unassigned'})"
                st.markdown(matchup_list_card_html(t1, t2, meta, detail, label, score_line), unsafe_allow_html=True)

def render_standings(df):
    st.markdown("### Ticket standings")
    totals = totals_by_name(df)
    if not totals:
        st.info("No tickets won yet."); return
    rows = [{"Name": k, "Tickets": v} for k, v in sorted(totals.items(), key=lambda x: (-x[1], x[0].lower()))]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

def render_header(df, locked_results):
    assigned = sorted({str(x).strip() for x in df["assigned_name"].fillna("") if str(x).strip()})[:10]
    legend_html = "".join(f'<span><i class="dot" style="background:{person_color(n)}"></i>{safe(n)}</span>' for n in assigned) or '<span><i class="dot" style="background:#f5f7fb"></i>No assignments yet</span>'
    totals = totals_by_name(df)
    totals_html = "".join(f'<div class="total-chip">{icon_html()}<span>{safe(n)}: {safe(ticket_label(v))}</span></div>' for n, v in sorted(totals.items(), key=lambda x: (-x[1], x[0].lower()))) or '<div class="total-chip"><span>No tickets won yet</span></div>'
    title_html = '<div class="title-strip"><div class="title-box"><h3>Sweet 16</h3><div class="big">1 🎟️</div><div class="small">1 ticket</div></div><div class="title-box"><h3>Elite 8</h3><div class="big">2 🎟️</div><div class="small">2 tickets</div></div><div class="title-box"><h3>Final Four</h3><div class="big">3 🎟️</div><div class="small">3 tickets</div></div><div class="title-box"><h3>Championship</h3><div class="big">4 🎟️</div><div class="small">4 tickets</div></div></div>'
    totals_block = f'<div class="totals-strip"><div class="totals-title">Total tickets won</div><div class="totals-grid">{totals_html}</div></div>'
    locked_note = f'Locked finals cache updated: {safe(locked_results["updated_at"])}' if locked_results.get("updated_at") else "Locked finals cache not created yet."
    timestamp_block = f'<div class="timestamp-strip"><div class="timestamp-row"><span><strong>App build:</strong> {safe(BUILD_TIMESTAMP_CT)}</span><span><strong>Last page refresh:</strong> {safe(ct_now_str())}</span><span><strong>{locked_note}</strong></span></div></div>'
    css = """
    <style>
    .main .block-container{padding-top:1rem;padding-bottom:4rem;max-width:100%;}
    .bracket-wrap{padding:8px 0 24px 0;}
    .mobile-note{color:#667085;font-size:13px;margin:0 0 14px 4px;}
    .legend{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 14px 4px;font-size:12px;color:#667085;}
    .legend span{display:inline-flex;align-items:center;gap:6px;}
    .dot{width:12px;height:12px;border-radius:999px;display:inline-block;border:1px solid rgba(0,0,0,.08);}
    .title-strip,.totals-strip,.timestamp-strip{background:#fff;border:1px solid #e7ebf2;border-radius:18px;padding:14px 16px;margin-bottom:18px;}
    .title-strip{display:flex;justify-content:center;gap:24px;align-items:center;flex-wrap:wrap;}
    .title-box{width:220px;background:#eefbf0;border:1px solid #9ed3a7;border-radius:16px;padding:14px;text-align:center;}
    .title-box h3{margin:0 0 6px 0;font-size:18px;color:#182230;}
    .title-box .big{font-size:28px;font-weight:800;margin:6px 0;color:#182230;}
    .title-box .small{font-size:13px;color:#667085;}
    .timestamp-row{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:#344054;}
    .timestamp-row span{display:inline-flex;gap:6px;align-items:center;}
    .totals-title{font-size:16px;font-weight:800;color:#182230;margin-bottom:10px;}
    .totals-grid{display:flex;gap:12px;flex-wrap:wrap;}
    .total-chip{border:1px solid #d8e5dc;border-radius:999px;padding:8px 12px;font-size:13px;font-weight:700;color:#14532d;background:#f3fff5;display:inline-flex;align-items:center;gap:8px;}
    .region-section{background:#fff;border:1px solid #e7ebf2;border-radius:20px;padding:18px 16px;margin-bottom:18px;overflow-x:auto;-webkit-overflow-scrolling:touch;}
    .region-name{font-size:28px;font-weight:800;margin:2px 0 14px 6px;color:#182230;}
    .region-board{display:grid;grid-template-columns:220px 220px 220px 220px 220px;grid-template-rows:repeat(15,146px);column-gap:16px;row-gap:14px;align-items:start;min-width:1164px;}
    .placed{align-self:start;}
    .match-card{width:220px;background:#f7f4f0;border:1px solid #ebe3da;border-radius:16px;padding:10px 10px 8px;box-sizing:border-box;height:146px;}
    .match-card.money-round-card{background:#eefbf0;border-color:#9ed3a7;}
    .match-title{font-size:11px;font-weight:700;text-transform:uppercase;color:#7a6d61;margin-bottom:6px;letter-spacing:.35px;}
    .match-title.money-round-title{color:#187a2f;}
    .matchup-meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px;font-size:10px;color:#475467;}
    .matchup-chip{display:inline-flex;align-items:center;justify-content:center;min-width:42px;height:16px;border-radius:999px;background:#344054;color:#fff;padding:0 6px;font-size:9px;font-weight:800;line-height:1;}
    .matchup-time{color:#14532d;font-weight:700;}
    .matchup-detail{color:#667085;}
    .team-row{background:#f5f7fb;border:1px solid rgba(0,0,0,.04);border-radius:10px;padding:8px 9px;margin-top:6px;}
    .team-row.tbd{background:#fbfbfd;}
    .team-row.money-team{border-color:#73c285;}
    .team-main{display:flex;gap:8px;align-items:center;line-height:1.15;}
    .seed{font-size:12px;font-weight:800;color:#475467;min-width:15px;}
    .team-name{font-size:14px;font-weight:700;color:#182230;max-width:110px;}
    .team-sub{font-size:11px;color:#667085;margin-top:4px;}
    .team-status{font-size:11px;color:#187a2f;margin-top:4px;font-weight:700;display:flex;gap:4px;align-items:center;flex-wrap:wrap;}
    .money-icon{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:999px;background:#1f9f43;color:#fff;font-size:12px;font-weight:800;line-height:1;}
    .money-bills{font-size:13px;line-height:1;}
    .live-line{margin-top:4px;font-size:10px;color:#166534;display:flex;gap:4px;align-items:center;flex-wrap:wrap;background:#ecfdf3;border:1px solid #86efac;border-radius:8px;padding:3px 6px;}
    .live-line.final{color:#991b1b;background:#fef2f2;border:1px solid #fca5a5;}
    .live-chip{display:inline-flex;align-items:center;justify-content:center;min-width:38px;height:16px;border-radius:999px;background:#15803d;color:#fff;padding:0 6px;font-size:9px;font-weight:800;line-height:1;}
    .live-line.final .live-chip{background:#b91c1c;}
    .live-time{color:#14532d;font-weight:700;}
    .live-detail{color:#667085;}
    .matchup-list-card{border:1px solid #e5e7eb;border-radius:12px;padding:10px 12px;margin-bottom:10px;background:#ffffff;}
    .matchup-list-card.live{background:#ecfdf3;border-color:#86efac;}
    .matchup-list-card.final{background:#fef2f2;border-color:#fca5a5;}
    .matchup-list-teams{display:flex;flex-direction:column;gap:2px;color:#111827;}
    .matchup-list-meta{margin-top:6px;font-size:12px;font-weight:700;color:#374151;}
    .matchup-list-score{margin-top:8px;font-size:20px;font-weight:900;line-height:1.2;color:#111827;}
    .matchup-list-card.live .matchup-list-score{color:#166534;}
    .matchup-list-card.final .matchup-list-score{color:#991b1b;}
    .matchup-list-detail{margin-top:4px;font-size:12px;color:#6b7280;}
    @media (max-width: 768px) {
      .region-section{padding:14px 10px;margin-bottom:16px;}
      .region-name{font-size:22px;margin:2px 0 10px 4px;}
      .region-board{grid-template-columns:190px 190px 190px 190px 190px;grid-template-rows:repeat(15,136px);min-width:1014px;column-gap:12px;row-gap:12px;}
      .match-card{width:190px;height:136px;padding:8px 8px 6px;}
      .title-box{width:180px;}
      .team-name{max-width:95px;font-size:13px;}
      .team-sub,.team-status,.live-line,.matchup-meta{font-size:9px;}
      .matchup-list-score{font-size:18px;}
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
    st.markdown('<div class="bracket-wrap"><div class="mobile-note">Bracket view restored. Halftime and other in-game pause states count as live.</div>' + timestamp_block + '<div class="legend">' + legend_html + '</div>' + totals_block + title_html + '</div>', unsafe_allow_html=True)

def render_views(df, live_map, recent_games, locked_results):
    render_header(df, locked_results)
    view = st.segmented_control("View", options=["Matchups", "Bracket", "Standings"], default="Matchups", width="stretch")
    if view == "Matchups":
        render_matchup_list(df, live_map, recent_games)
    elif view == "Standings":
        render_standings(df)
    else:
        locked_games = locked_results.get("games", [])
        for region in ["South","West","East","Midwest"]:
            st.markdown(build_region(df[df["region"] == region], region, live_map, recent_games, locked_games), unsafe_allow_html=True)

@st.fragment(run_every="45s")
def live_bracket_fragment(df: pd.DataFrame):
    recent = fetch_recent_espn()
    locked = merge_finals_into_locked(recent.get("games", []))
    render_views(df, recent.get("team_map", {}), recent.get("games", []), locked)
    st.caption("Auto-refreshing every 45 seconds.")

def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🏀", layout="wide")
    data = load_data()
    df = pd.DataFrame(data["teams"])
    if df.empty:
        st.error("No teams found in teams.json")
        return
    st.title("🏀 2026 Todaro March Madness")
    st.caption("Public bracket view")
    live_bracket_fragment(df)

if __name__ == "__main__":
    main()
