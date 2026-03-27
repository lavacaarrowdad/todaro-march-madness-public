
import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

APP_TITLE = "2026 Todaro March Madness"
DATA_PATH = Path(__file__).with_name("teams.json")
LOCKED_RESULTS_PATH = Path(__file__).with_name("locked_results.json")
BUILD_TIMESTAMP_CT = "Mar 26, 2026 9:08 PM CT"

FIRST_ROUND_ORDER = [(1, 16), (8, 9), (5, 12), (4, 13), (6, 11), (3, 14), (7, 10), (2, 15)]
ROUND_STAKES = {"Sweet 16": 1, "Elite 8": 2, "Final Four": 3, "Championship": 4}
REGIONS = ["South", "West", "East", "Midwest"]


def ct_now():
    return datetime.now(ZoneInfo("America/Chicago"))


def ct_now_str():
    return ct_now().strftime("%b %d, %Y %I:%M:%S %p CT").replace(" 0", " ")


def safe(v):
    return html.escape(str(v or ""))


def person_color(name: str) -> str:
    if not str(name or "").strip():
        return "#f5f7fb"
    palette = ["#e8f1ff", "#f6e8ff", "#e9fff4", "#fff3e8", "#eef0ff", "#ffeef5", "#eefcf2", "#fff9df", "#edf7ff", "#f3edff"]
    return palette[sum(ord(c) for c in str(name).strip().lower()) % len(palette)]


def normalize_team_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("&", " and ").replace("st.", "saint").replace("st ", "saint ").replace("(oh)", " ohio").replace("/", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def team_aliases(team_name: str):
    raw = (team_name or "").strip()
    aliases = set()
    if not raw:
        return aliases

    def add_variant(v: str):
        nv = normalize_team_name(v)
        if not nv:
            return
        aliases.add(nv)
        parts = nv.split()
        if len(parts) >= 2:
            aliases.add(" ".join(parts[:2]))
        if len(parts) >= 1:
            aliases.add(parts[0])

    add_variant(raw)
    for p in [x.strip() for x in raw.split("/") if x.strip()]:
        add_variant(p)

    manual = {
        "saint mary s": ["saint marys", "saint mary's"],
        "north carolina": ["unc"],
        "miami ohio smu": ["miami ohio", "smu"],
        "umbc howard": ["umbc", "howard"],
        "lehigh prairie view a m": ["lehigh", "prairie view a m", "prairie view am", "prairie view"],
        "texas nc state": ["texas", "nc state"],
        "saint john s": ["saint johns", "st john s", "st johns"],
        "south florida": ["usf"],
        "connecticut": ["uconn"],
        "ohio state": ["ohio state buckeyes"],
        "texas a m": ["texas am", "texas a&m"],
        "brigham young": ["byu"],
    }
    nr = normalize_team_name(raw)
    if nr in manual:
        for v in manual[nr]:
            add_variant(v)
    return aliases


def format_ct_datetime(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = pd.to_datetime(date_str, utc=True).tz_convert(ZoneInfo("America/Chicago"))
        return dt.strftime("%a %b %d, %I:%M %p CT").replace(" 0", " ")
    except Exception:
        return ""


def ct_date_label_from_ct_time(ct_time: str) -> str:
    m = re.match(r"^[A-Za-z]{3} ([A-Za-z]{3} \d{2}),", ct_time or "")
    return m.group(1) if m else ""


def is_live_like(status: str) -> bool:
    s = (status or "").lower()
    return any(x in s for x in ["in progress", "halftime", "end of", "delayed"]) and "final" not in s


def load_data():
    if not DATA_PATH.exists():
        st.error("teams.json not found.")
        return {"teams": []}
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    for team in data.get("teams", []):
        team.setdefault("espn_team", "")
    return data


def load_locked_results():
    if not LOCKED_RESULTS_PATH.exists():
        return {"slots": {}, "updated_at": ""}
    try:
        data = json.loads(LOCKED_RESULTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "slots" in data:
            return data
    except Exception:
        pass
    return {"slots": {}, "updated_at": ""}


def save_locked_results(data):
    LOCKED_RESULTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
                parsed.append({
                    "team": c.get("team", {}).get("displayName", "") or "",
                    "score": str(c.get("score", "") or ""),
                    "winner": bool(c.get("winner", False)),
                })
            game = {"status": status, "detail": detail, "ct_time": ct_time, "teams": parsed}
            games.append(game)
            priority = 2 if status.lower() == "final" else 1 if is_live_like(status) else 0
            for i, t in enumerate(parsed):
                opp = parsed[1 - i]
                key = normalize_team_name(t["team"])
                old = team_map.get(key, {"_priority": -1})
                if priority >= old["_priority"]:
                    team_map[key] = {
                        "score": t["score"], "opp": opp["team"], "opp_score": opp["score"],
                        "status": status, "detail": detail, "ct_time": ct_time,
                        "winner": t["winner"], "game": game, "_priority": priority
                    }
    for v in team_map.values():
        v.pop("_priority", None)
    return {"games": games, "team_map": team_map}


def row_aliases(team_row) -> set:
    aliases = set()
    if not team_row:
        return aliases
    aliases.update(team_aliases(team_row.get("team", "")))
    aliases.update(team_aliases(team_row.get("espn_team", "")))
    return {a for a in aliases if a}


def row_matches_game_name(team_row, game_name: str) -> bool:
    if not team_row or not game_name:
        return False
    g = normalize_team_name(game_name)
    aliases = row_aliases(team_row)
    if g in aliases:
        return True
    for a in aliases:
        if a == g or a.startswith(g) or g.startswith(a):
            return True
        sa = set(a.split())
        sg = set(g.split())
        if len(sa & sg) >= min(2, len(sa), len(sg)):
            return True
    return False


def teams_match_game(team_a, team_b, game):
    if not team_a or not team_b or not game:
        return False
    teams = game.get("teams", [])
    if len(teams) != 2:
        return False
    g0 = teams[0].get("team", "")
    g1 = teams[1].get("team", "")
    return (row_matches_game_name(team_a, g0) and row_matches_game_name(team_b, g1)) or (row_matches_game_name(team_a, g1) and row_matches_game_name(team_b, g0))


def exact_matchup_game(team_a, team_b, recent_games, locked_slots_games):
    if not team_a or not team_b:
        return None
    finals = []
    others = []
    for game in list(reversed(recent_games)) + list(reversed(locked_slots_games)):
        if teams_match_game(team_a, team_b, game):
            if str(game.get("status", "")).lower() == "final":
                finals.append(game)
            else:
                others.append(game)
    return finals[0] if finals else (others[0] if others else None)


def lookup_name(team_row):
    return (team_row.get("espn_team", "") or "").strip() or (team_row.get("team", "") or "")


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


def region_matchups(region_df):
    seed_to_row = {int(r["seed"]): r.to_dict() for _, r in region_df.iterrows()}
    return [(seed_to_row[a], seed_to_row[b]) for a, b in FIRST_ROUND_ORDER]


def slot_id(region, round_name, index):
    return f"{region}|{round_name}|{index}"


def build_slot_structure(df):
    slots = {}
    for region in REGIONS:
        region_df = df[df["region"] == region]
        first = region_matchups(region_df)
        for i, (a, b) in enumerate(first, start=1):
            slots[slot_id(region, "Round of 64", i)] = {"team1": a, "team2": b, "winner": None, "game": None}
        for i in range(1, 5):
            slots[slot_id(region, "Round of 32", i)] = {"team1": None, "team2": None, "winner": None, "game": None}
        for i in range(1, 3):
            slots[slot_id(region, "Sweet 16", i)] = {"team1": None, "team2": None, "winner": None, "game": None}
        slots[slot_id(region, "Elite 8", 1)] = {"team1": None, "team2": None, "winner": None, "game": None}
        slots[slot_id(region, "Final Four", 1)] = {"team1": None, "team2": None, "winner": None, "game": None}
    return slots


def slot_target(round_name, index):
    if round_name == "Round of 64":
        return ("Round of 32", (index + 1) // 2, 1 if index % 2 == 1 else 2)
    if round_name == "Round of 32":
        return ("Sweet 16", (index + 1) // 2, 1 if index % 2 == 1 else 2)
    if round_name == "Sweet 16":
        return ("Elite 8", 1, 1 if index == 1 else 2)
    if round_name == "Elite 8":
        return ("Final Four", 1, 1)
    return None


def merge_slot_finals(df, recent_games):
    locked = load_locked_results()
    base_slots = build_slot_structure(df)
    saved_slots = locked.get("slots", {}) or {}

    # restore saved winners/games
    for sid, info in saved_slots.items():
        if sid in base_slots:
            if info.get("winner"):
                base_slots[sid]["winner"] = info.get("winner")
            if info.get("game"):
                base_slots[sid]["game"] = info.get("game")

    # iterate first round to elite 8; if formed and final exists, lock result into exact slot
    for region in REGIONS:
        for round_name, count in [("Round of 64", 8), ("Round of 32", 4), ("Sweet 16", 2), ("Elite 8", 1)]:
            for idx in range(1, count + 1):
                sid = slot_id(region, round_name, idx)
                slot = base_slots[sid]
                team1 = slot["team1"]
                team2 = slot["team2"]
                if not (team1 and team2):
                    continue
                game = exact_matchup_game(team1, team2, recent_games, [s["game"] for s in base_slots.values() if s.get("game")])
                if game and str(game.get("status", "")).lower() == "final":
                    slot["game"] = game
                    winner_name = None
                    for t in game.get("teams", []):
                        if t.get("winner"):
                            winner_name = t.get("team", "")
                            break
                    if winner_name:
                        slot["winner"] = team1 if row_matches_game_name(team1, winner_name) else team2 if row_matches_game_name(team2, winner_name) else None

    # propagate winners forward
    # clear future slots first
    for region in REGIONS:
        for round_name, count in [("Round of 32", 4), ("Sweet 16", 2), ("Elite 8", 1), ("Final Four", 1)]:
            for idx in range(1, count + 1):
                sid = slot_id(region, round_name, idx)
                base_slots[sid]["team1"] = None
                base_slots[sid]["team2"] = None

    for region in REGIONS:
        for round_name, count in [("Round of 64", 8), ("Round of 32", 4), ("Sweet 16", 2), ("Elite 8", 1)]:
            for idx in range(1, count + 1):
                sid = slot_id(region, round_name, idx)
                winner = base_slots[sid]["winner"]
                target = slot_target(round_name, idx)
                if winner and target:
                    tround, tidx, side = target
                    tsid = slot_id(region, tround, tidx)
                    base_slots[tsid]["team1" if side == 1 else "team2"] = winner

    locked["slots"] = base_slots
    locked["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    save_locked_results(locked)
    return locked


def matchup_game_for_slot(slot, live_map, recent_games):
    team1 = slot.get("team1")
    team2 = slot.get("team2")
    if team1 and team2:
        if slot.get("game"):
            return slot["game"]
        exact = exact_matchup_game(team1, team2, recent_games, [])
        if exact:
            return exact
    if team1 and not team2:
        tg = get_game_for_single_team(team1, live_map)
        if tg:
            return tg
    if team2 and not team1:
        tg = get_game_for_single_team(team2, live_map)
        if tg:
            return tg
    return None


def stake_badge_html(tickets: int) -> str:
    if not tickets:
        return ""
    return f'<div class="stake-badge">{icon_html()}<span>{safe(ticket_label(tickets))} at stake</span></div>'


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
    my_aliases = row_aliases(row)
    score_text = ""
    if len(teams) == 2:
        t0, t1 = teams[0], teams[1]
        t0n = normalize_team_name(t0.get("team", ""))
        t1n = normalize_team_name(t1.get("team", ""))
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
    sub = []
    if str(row.get("assigned_name", "")).strip():
        sub.append(safe(row["assigned_name"]))
    if str(row.get("slot_note", "")).strip():
        sub.append(safe(row["slot_note"]))
    if str(row.get("round_reached", "")).strip():
        sub.append(safe(row["round_reached"]))
    sub_html = f'<div class="team-sub">{" • ".join(sub)}</div>' if sub else ""
    return f'<div class="team-row" style="background:{person_color(row.get("assigned_name",""))}"><div class="team-main"><span class="seed">{safe(row.get("seed","")) if row.get("seed") is not None else "•"}</span><span class="team-name">{safe(row["team"])}</span></div>{sub_html}{live_line(row, live_map)}</div>'


def meta_from_game(game):
    if not game:
        return ""
    ct_time = str(game.get("ct_time", "") or "").strip()
    status = str(game.get("status", "") or "").strip()
    detail = str(game.get("detail", "") or "").strip()
    label = "LIVE" if is_live_like(status) else "FINAL" if status.lower() == "final" else "SCHED"
    if label == "SCHED":
        detail = ""
    time_html = f'<span class="matchup-time">{safe(ct_time)}</span>' if ct_time else ""
    detail_html = f'<span class="matchup-detail">{safe(detail)}</span>' if detail else ""
    return f'<div class="matchup-meta"><span class="matchup-chip">{safe(label)}</span>{time_html}{detail_html}</div>'


def matchup_card(slot, live_map, recent_games, title="", tickets=0):
    title_html = f'<div class="{"match-title money-round-title" if tickets else "match-title"}">{safe(title)}</div>' if title else ""
    stake_html = stake_badge_html(tickets) if tickets else ""
    game = matchup_game_for_slot(slot, live_map, recent_games)
    meta_html = meta_from_game(game)
    extra = " money-round-card" if tickets else ""
    return f'<div class="match-card{extra}">{title_html}{stake_html}{meta_html}{team_line(slot.get("team1"), live_map)}{team_line(slot.get("team2"), live_map)}</div>'


def build_region(region_name, slots, live_map, recent_games):
    placed = []
    for i in range(1, 9):
        sid = slot_id(region_name, "Round of 64", i)
        placed.append(f'<div class="placed" style="grid-column:1;grid-row:{1+(i-1)*2} / span 1;">{matchup_card(slots[sid], live_map, recent_games, prefer_top_team=False if False else "")}</div>')
    for i in range(1, 5):
        sid = slot_id(region_name, "Round of 32", i)
        placed.append(f'<div class="placed" style="grid-column:2;grid-row:{2+(i-1)*4} / span 1;">{matchup_card(slots[sid], live_map, recent_games, "Round of 32")}</div>')
    for i in range(1, 3):
        sid = slot_id(region_name, "Sweet 16", i)
        placed.append(f'<div class="placed" style="grid-column:3;grid-row:{4+(i-1)*8} / span 1;">{matchup_card(slots[sid], live_map, recent_games, "Sweet 16", 1)}</div>')
    sid = slot_id(region_name, "Elite 8", 1)
    placed.append(f'<div class="placed" style="grid-column:4;grid-row:8 / span 1;">{matchup_card(slots[sid], live_map, recent_games, "Elite 8", 2)}</div>')
    sid = slot_id(region_name, "Final Four", 1)
    placed.append(f'<div class="placed" style="grid-column:5;grid-row:8 / span 1;">{matchup_card(slots[sid], live_map, recent_games, "Final Four", 3)}</div>')
    return f'<div class="region-section"><div class="region-name">{safe(region_name)}</div><div class="region-board">{"".join(placed)}</div></div>'


def matchup_list_card_html(team1, team2, meta, detail, label, score_line="", tickets=0):
    cls = "matchup-list-card"
    if label == "LIVE":
        cls += " live"
    elif label == "FINAL":
        cls += " final"
    score_html = f'<div class="matchup-list-score">{safe(score_line)}</div>' if score_line else ""
    detail_html = f'<div class="matchup-list-detail">{safe(detail)}</div>' if detail else ""
    stake_html = f'<div class="matchup-list-stake">{icon_html()}<span>{safe(ticket_label(tickets))} at stake</span></div>' if tickets else ""
    return f'<div class="{cls}"><div class="matchup-list-teams"><div><strong>{safe(team1)}</strong></div><div>vs</div><div><strong>{safe(team2)}</strong></div></div><div class="matchup-list-meta">{safe(meta)}</div>{score_html}{stake_html}{detail_html}</div>'


def render_matchup_list(df, slots, live_map, recent_games):
    st.markdown("### Mobile-friendly game list")
    today_label = ct_now().strftime("%b %d")
    visible_sections = []
    past_sections = {}

    for region in REGIONS:
        for round_name, count in [("Round of 64", 8), ("Round of 32", 4), ("Sweet 16", 2), ("Elite 8", 1), ("Final Four", 1)]:
            for idx in range(1, count + 1):
                sid = slot_id(region, round_name, idx)
                slot = slots[sid]
                a, b = slot.get("team1"), slot.get("team2")
                if not (a or b):
                    continue

                game = matchup_game_for_slot(slot, live_map, recent_games)
                label = ""
                score_line = ""
                if game:
                    status = str(game.get("status", "") or "")
                    label = "LIVE" if is_live_like(status) else "FINAL" if status.lower() == "final" else "SCHED"
                    ct_time = str(game.get("ct_time", "") or "")
                    meta = f"{label} · {ct_time}" if ct_time else label
                    detail = str(game.get("detail", "") or "")
                    teams = game.get("teams", [])
                    if label in {"LIVE", "FINAL"} and len(teams) == 2:
                        score_line = f"{teams[0].get('team','')} {teams[0].get('score','')} — {teams[1].get('score','')} {teams[1].get('team','')}"
                    if label == "FINAL" and len(teams) == 2:
                        winner = teams[0].get("team", "") if teams[0].get("winner") else teams[1].get("team", "") if teams[1].get("winner") else ""
                        if winner:
                            detail = f"{winner} won"
                    if label == "SCHED":
                        detail = ""
                else:
                    meta = "Awaiting prior result" if a and b else "Not formed yet"
                    detail = ""

                t1 = f"{a.get('team','TBD')} ({a.get('assigned_name','').strip() or 'Unassigned'})" if a else "TBD"
                t2 = f"{b.get('team','TBD')} ({b.get('assigned_name','').strip() or 'Unassigned'})" if b else "TBD"
                card_html = matchup_list_card_html(t1, t2, meta, detail, label, score_line, ROUND_STAKES.get(round_name, 0))

                entry = {"region": region, "round": round_name, "html": card_html, "date_label": ct_date_label_from_ct_time(str(game.get("ct_time","") if game else "")), "label": label}
                if label == "FINAL" and entry["date_label"] and entry["date_label"] != today_label:
                    past_sections.setdefault(entry["date_label"], []).append(entry)
                else:
                    visible_sections.append(entry)

    if visible_sections:
        current_region = None
        current_round = None
        for entry in visible_sections:
            if entry["region"] != current_region:
                current_region = entry["region"]
                current_round = None
                st.markdown(f"## {current_region}")
            if entry["round"] != current_round:
                current_round = entry["round"]
                st.markdown(f"**{current_round}**")
            st.markdown(entry["html"], unsafe_allow_html=True)
    else:
        st.info("No current or upcoming games to show.")

    if past_sections:
        for date_label in sorted(past_sections.keys(), reverse=True):
            with st.expander(f"Completed games — {date_label}", expanded=False):
                current_region = None
                current_round = None
                for entry in past_sections[date_label]:
                    if entry["region"] != current_region:
                        current_region = entry["region"]
                        current_round = None
                        st.markdown(f"## {current_region}")
                    if entry["round"] != current_round:
                        current_round = entry["round"]
                        st.markdown(f"**{current_round}**")
                    st.markdown(entry["html"], unsafe_allow_html=True)


def render_standings(df):
    st.markdown("### Ticket standings")
    totals = {}
    for _, row in df.iterrows():
        name = str(row.get("assigned_name", "") or "").strip()
        if name:
            totals[name] = totals.get(name, 0)
    rows = [{"Name": k, "Tickets": v} for k, v in sorted(totals.items(), key=lambda x: x[0].lower())]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_header(df, locked_results):
    assigned = sorted({str(x).strip() for x in df["assigned_name"].fillna("") if str(x).strip()})[:10]
    legend_html = "".join(f'<span><i class="dot" style="background:{person_color(n)}"></i>{safe(n)}</span>' for n in assigned) or '<span><i class="dot" style="background:#f5f7fb"></i>No assignments yet</span>'
    totals_html = "".join(f'<div class="total-chip">{icon_html()}<span>{safe(n)}: 0 tickets</span></div>' for n in assigned) or '<div class="total-chip"><span>No tickets won yet</span></div>'
    totals_block = f'<div class="totals-strip"><div class="totals-title">Total tickets won</div><div class="totals-grid">{totals_html}</div></div>'
    locked_note = f'Locked slot cache updated: {safe(locked_results["updated_at"])}' if locked_results.get("updated_at") else "Locked slot cache not created yet."
    timestamp_block = f'<div class="timestamp-strip"><div class="timestamp-row"><span><strong>App build:</strong> {safe(BUILD_TIMESTAMP_CT)}</span><span><strong>Last page refresh:</strong> {safe(ct_now_str())}</span><span><strong>{locked_note}</strong></span></div></div>'
    css = """
    <style>
    html, body, [data-testid="stAppViewContainer"], [data-testid="stVerticalBlock"] {overflow-y: visible !important;}
    .main .block-container{padding-top:1rem;padding-bottom:4rem;max-width:100%;}
    .bracket-wrap{padding:8px 0 24px 0;}
    .mobile-note{color:#667085;font-size:13px;margin:0 0 14px 4px;}
    .legend{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 14px 4px;font-size:12px;color:#667085;}
    .legend span{display:inline-flex;align-items:center;gap:6px;}
    .dot{width:12px;height:12px;border-radius:999px;display:inline-block;border:1px solid rgba(0,0,0,.08);}
    .totals-strip,.timestamp-strip{background:#fff;border:1px solid #e7ebf2;border-radius:18px;padding:14px 16px;margin-bottom:18px;}
    .timestamp-row{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:#344054;}
    .timestamp-row span{display:inline-flex;gap:6px;align-items:center;}
    .totals-title{font-size:16px;font-weight:800;color:#182230;margin-bottom:10px;}
    .totals-grid{display:flex;gap:12px;flex-wrap:wrap;}
    .total-chip{border:1px solid #d8e5dc;border-radius:999px;padding:8px 12px;font-size:13px;font-weight:700;color:#14532d;background:#f3fff5;display:inline-flex;align-items:center;gap:8px;}
    .region-section{background:#fff;border:1px solid #e7ebf2;border-radius:20px;padding:18px 16px;margin-bottom:18px;overflow-x:auto;overflow-y:visible;-webkit-overflow-scrolling:touch;touch-action:pan-x pan-y;}
    .region-name{font-size:28px;font-weight:800;margin:2px 0 14px 6px;color:#182230;}
    .region-board{display:grid;grid-template-columns:220px 220px 220px 220px 220px;grid-template-rows:repeat(15,146px);column-gap:16px;row-gap:14px;align-items:start;min-width:1164px;}
    .placed{align-self:start;}
    .match-card{width:220px;background:#f7f4f0;border:1px solid #ebe3da;border-radius:16px;padding:10px 10px 8px;box-sizing:border-box;height:146px;}
    .match-card.money-round-card{background:#eefbf0;border-color:#9ed3a7;}
    .match-title{font-size:11px;font-weight:700;text-transform:uppercase;color:#7a6d61;margin-bottom:6px;letter-spacing:.35px;}
    .match-title.money-round-title{color:#187a2f;}
    .stake-badge{display:inline-flex;align-items:center;gap:6px;margin-bottom:6px;padding:4px 8px;border-radius:999px;background:#eefbf0;border:1px solid #9ed3a7;color:#166534;font-size:11px;font-weight:800;}
    .matchup-meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px;font-size:10px;color:#475467;}
    .matchup-chip{display:inline-flex;align-items:center;justify-content:center;min-width:42px;height:16px;border-radius:999px;background:#344054;color:#fff;padding:0 6px;font-size:9px;font-weight:800;line-height:1;}
    .matchup-time{color:#14532d;font-weight:700;}
    .matchup-detail{color:#667085;}
    .team-row{background:#f5f7fb;border:1px solid rgba(0,0,0,.04);border-radius:10px;padding:8px 9px;margin-top:6px;}
    .team-row.tbd{background:#fbfbfd;}
    .team-main{display:flex;gap:8px;align-items:center;line-height:1.15;}
    .seed{font-size:12px;font-weight:800;color:#475467;min-width:15px;}
    .team-name{font-size:14px;font-weight:700;color:#182230;max-width:110px;}
    .team-sub{font-size:11px;color:#667085;margin-top:4px;}
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
    .matchup-list-stake{margin-top:8px;display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:999px;background:#eefbf0;border:1px solid #9ed3a7;color:#166534;font-size:11px;font-weight:800;}
    @media (max-width: 768px) {
      .region-section{padding:14px 10px;margin-bottom:16px;}
      .region-name{font-size:22px;margin:2px 0 10px 4px;}
      .region-board{grid-template-columns:190px 190px 190px 190px 190px;grid-template-rows:repeat(15,136px);min-width:1014px;column-gap:12px;row-gap:12px;}
      .match-card{width:190px;height:136px;padding:8px 8px 6px;}
      .team-name{max-width:95px;font-size:13px;}
      .team-sub,.live-line,.matchup-meta{font-size:9px;}
      .matchup-list-score{font-size:18px;}
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
    st.markdown('<div class="bracket-wrap"><div class="mobile-note">Slot-based local result locking enabled. Today’s games are shown by default. Prior completed days are collapsible. Central Time only.</div>' + timestamp_block + '<div class="legend">' + legend_html + '</div>' + totals_block + '</div>', unsafe_allow_html=True)


def render_views(df, slots, live_map, recent_games, locked_results):
    render_header(df, locked_results)
    view = st.segmented_control("View", options=["Matchups", "Bracket", "Standings"], default="Matchups", width="stretch")
    if view == "Matchups":
        render_matchup_list(df, slots, live_map, recent_games)
    elif view == "Standings":
        render_standings(df)
    else:
        for region in REGIONS:
            st.markdown(build_region(region, slots, live_map, recent_games), unsafe_allow_html=True)


@st.fragment(run_every="45s")
def live_bracket_fragment(df: pd.DataFrame):
    recent = fetch_recent_espn()
    locked = merge_slot_finals(df, recent.get("games", []))
    render_views(df, locked.get("slots", {}), recent.get("team_map", {}), recent.get("games", []), locked)
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
