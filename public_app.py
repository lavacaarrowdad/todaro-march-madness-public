
import html
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

APP_TITLE = "2026 Todaro March Madness"
DATA_PATH = Path(__file__).with_name("teams.json")
MONEY_ROUNDS = {"Sweet 16", "Elite 8", "Elite Eight", "Final Four", "Championship", "Champion"}


def load_data():
    if not DATA_PATH.exists():
        st.error("teams.json not found.")
        return {"teams": []}
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def normalize_team_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("&", " and ")
    s = s.replace("st.", "saint")
    s = s.replace("st ", "saint ")
    s = s.replace("(oh)", " ohio")
    s = s.replace("/", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def team_aliases(team_name: str):
    raw = (team_name or "").strip()
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    aliases = {normalize_team_name(raw)}
    for p in parts:
        aliases.add(normalize_team_name(p))
    manual = {
        "saint mary s": ["saint marys", "saint mary s", "saint mary's"],
        "north carolina": ["unc", "north carolina"],
        "miami ohio smu": ["miami ohio", "smu"],
        "umbc howard": ["umbc", "howard"],
        "lehigh prairie view a m": ["lehigh", "prairie view a m", "prairie view am"],
        "texas nc state": ["texas", "nc state"],
        "saint john s": ["saint johns", "st john s", "st johns"],
    }
    norm_raw = normalize_team_name(raw)
    if norm_raw in manual:
        aliases.update(manual[norm_raw])
    return aliases


@st.cache_data(ttl=45, show_spinner=False)
def fetch_espn_results():
    base_url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    team_map = {}
    games = []

    today = date.today()
    for offset in range(-21, 2):
        d = today + timedelta(days=offset)
        datestr = d.strftime("%Y%m%d")
        try:
            r = requests.get(base_url, params={"groups": 50, "limit": 300, "dates": datestr}, timeout=12)
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

            status_desc = event.get("status", {}).get("type", {}).get("description", "") or ""
            status_detail = event.get("status", {}).get("type", {}).get("shortDetail", "") or ""

            parsed = []
            for comp in competitors:
                team_name = comp.get("team", {}).get("displayName", "") or ""
                parsed.append(
                    {
                        "team": team_name,
                        "score": str(comp.get("score", "") or ""),
                        "winner": bool(comp.get("winner", False)),
                    }
                )

            if len(parsed) != 2:
                continue

            game = {
                "matchup": event.get("shortName", "") or "",
                "status": status_desc,
                "detail": status_detail,
                "teams": parsed,
            }
            games.append(game)

            for i, t in enumerate(parsed):
                opp = parsed[1 - i]
                key = normalize_team_name(t["team"])
                existing = team_map.get(key)
                priority = 2 if status_desc.lower() == "final" else 1 if status_desc.lower() == "in progress" else 0
                existing_priority = existing.get("_priority", -1) if existing else -1
                if priority >= existing_priority:
                    team_map[key] = {
                        "score": t["score"],
                        "opp": opp["team"],
                        "opp_score": opp["score"],
                        "status": status_desc,
                        "detail": status_detail,
                        "winner": t["winner"],
                        "_priority": priority,
                    }

    for v in team_map.values():
        v.pop("_priority", None)

    return {"ok": True, "games": games, "team_map": team_map}


def get_live_for_team(team_name: str, live_map: dict):
    for alias in team_aliases(team_name):
        if alias in live_map:
            return live_map[alias]
    return None


def decide_winner(team_a, team_b, games):
    if not team_a or not team_b:
        return None
    aliases_a = team_aliases(team_a["team"])
    aliases_b = team_aliases(team_b["team"])

    for game in reversed(games):
        if game["status"].lower() != "final":
            continue
        teams = game["teams"]
        g0 = normalize_team_name(teams[0]["team"])
        g1 = normalize_team_name(teams[1]["team"])
        cond = ((g0 in aliases_a and g1 in aliases_b) or (g0 in aliases_b and g1 in aliases_a))
        if not cond:
            continue

        for t in teams:
            if t["winner"]:
                winner_norm = normalize_team_name(t["team"])
                if winner_norm in aliases_a:
                    return team_a
                if winner_norm in aliases_b:
                    return team_b
    return None


def person_color(name: str) -> str:
    if not name or not str(name).strip():
        return "#f5f7fb"
    palette = ["#e8f1ff", "#f6e8ff", "#e9fff4", "#fff3e8", "#eef0ff", "#ffeef5", "#eefcf2", "#fff9df", "#edf7ff", "#f3edff"]
    idx = sum(ord(c) for c in name.strip().lower()) % len(palette)
    return palette[idx]


def safe_text(value: str) -> str:
    return html.escape(str(value or ""))


def is_money_team(team_row) -> bool:
    round_reached = str(team_row.get("round_reached", "") or "").strip()
    manual_status = str(team_row.get("manual_status", "") or "").strip()
    return round_reached in MONEY_ROUNDS or manual_status in MONEY_ROUNDS


def money_icon_html() -> str:
    return '<span class="money-icon">$</span><span class="money-bills">💵</span>'


def live_line_html(team_row, live_map) -> str:
    live = get_live_for_team(str(team_row.get("team", "") or ""), live_map)
    if not live:
        return ""

    status = str(live.get("status", "") or "")
    detail = safe_text(live.get("detail", "") or "")
    score = safe_text(live.get("score", "") or "")
    opp = safe_text(live.get("opp", "") or "")
    opp_score = safe_text(live.get("opp_score", "") or "")
    winner = bool(live.get("winner", False))

    if status.lower() == "in progress":
        label = "LIVE"
    elif status.lower() == "final":
        label = "W" if winner else "L"
    else:
        label = status.upper()[:10] if status else ""

    cls = "live-line final" if status.lower() == "final" else "live-line"
    return f'<div class="{cls}"><span class="live-chip">{safe_text(label)}</span><span>{score}-{opp_score} vs {opp}</span><span class="live-detail">{detail}</span></div>'


def team_line(team_row, live_map):
    if team_row is None:
        return '<div class="team-row tbd"><div class="team-main"><span class="seed">•</span><span class="team-name">TBD</span></div></div>'
    seed = safe_text(team_row["seed"])
    team = safe_text(team_row["team"])
    note = safe_text(team_row.get("slot_note", ""))
    assigned = safe_text(team_row.get("assigned_name", ""))
    status = safe_text(team_row.get("manual_status", ""))
    round_reached = safe_text(team_row.get("round_reached", ""))
    bg = person_color(assigned)
    money_team = is_money_team(team_row)
    extra_class = " money-team" if money_team else ""
    badge_html = money_icon_html() if money_team else ""
    sub_parts = []
    if assigned:
        sub_parts.append(assigned)
    if note:
        sub_parts.append(note)
    if round_reached:
        sub_parts.append(round_reached)
    sub_html = f'<div class="team-sub">{" • ".join(sub_parts)}</div>' if sub_parts else ""
    status_html = f'<div class="team-status">{badge_html}{status}</div>' if status else (f'<div class="team-status">{badge_html}Money game</div>' if money_team else "")
    live_html = live_line_html(team_row, live_map)
    return f'''
    <div class="team-row{extra_class}" style="background:{bg}">
      <div class="team-main">
        <span class="seed">{seed}</span>
        <span class="team-name">{team}</span>
        {badge_html}
      </div>
      {sub_html}
      {status_html}
      {live_html}
    </div>
    '''


def matchup_card(top_row, bottom_row, live_map, title="", money_round=False):
    title_html = ""
    if title:
        money_html = money_icon_html() if money_round else ""
        cls = "match-title money-round-title" if money_round else "match-title"
        label = "Money Game" if money_round else title
        title_html = f'<div class="{cls}">{money_html}{safe_text(label)}</div>'
    extra_class = " money-round-card" if money_round else ""
    return f'''
    <div class="match-card{extra_class}">
      {title_html}
      {team_line(top_row, live_map)}
      {team_line(bottom_row, live_map)}
    </div>
    '''


def tbd_round_card(label: str, live_map, money_round=False):
    return matchup_card(None, None, live_map, label, money_round=money_round)


def region_matchups(region_df: pd.DataFrame):
    seed_to_row = {int(r["seed"]): r.to_dict() for _, r in region_df.iterrows()}
    order = [(1, 16), (8, 9), (5, 12), (4, 13), (6, 11), (3, 14), (7, 10), (2, 15)]
    return [(seed_to_row[a], seed_to_row[b]) for a, b in order]


def build_region_html(region_df: pd.DataFrame, region_name: str, live_map, games):
    matchups = region_matchups(region_df)
    placements = []

    r64_winners = []
    for i, (a, b) in enumerate(matchups):
        row_start = 1 + i * 2
        placements.append(f'<div class="placed" style="grid-column:1;grid-row:{row_start} / span 1;">{matchup_card(a,b,live_map)}</div>')
        r64_winners.append(decide_winner(a, b, games))

    r32_pairs = [(r64_winners[0], r64_winners[1]), (r64_winners[2], r64_winners[3]), (r64_winners[4], r64_winners[5]), (r64_winners[6], r64_winners[7])]
    r32_winners = []
    for i, (a, b) in enumerate(r32_pairs):
        row_start = 2 + i * 4
        card = matchup_card(a, b, live_map, "Round of 32") if a and b else tbd_round_card("Round of 32", live_map)
        placements.append(f'<div class="placed" style="grid-column:2;grid-row:{row_start} / span 1;">{card}</div>')
        r32_winners.append(decide_winner(a, b, games) if a and b else None)

    s16_pairs = [(r32_winners[0], r32_winners[1]), (r32_winners[2], r32_winners[3])]
    s16_winners = []
    for i, (a, b) in enumerate(s16_pairs):
        row_start = 4 + i * 8
        card = matchup_card(a, b, live_map, "Sweet 16", money_round=True) if a and b else tbd_round_card("Sweet 16", live_map, money_round=True)
        placements.append(f'<div class="placed" style="grid-column:3;grid-row:{row_start} / span 1;">{card}</div>')
        s16_winners.append(decide_winner(a, b, games) if a and b else None)

    if s16_winners[0] and s16_winners[1]:
        elite_card = matchup_card(s16_winners[0], s16_winners[1], live_map, "Elite 8", money_round=True)
        elite_winner = decide_winner(s16_winners[0], s16_winners[1], games)
    else:
        elite_card = tbd_round_card("Elite 8", live_map, money_round=True)
        elite_winner = None
    placements.append(f'<div class="placed" style="grid-column:4;grid-row:8 / span 1;">{elite_card}</div>')

    if elite_winner:
        ff_card = matchup_card(elite_winner, None, live_map, "Final Four", money_round=True)
    else:
        ff_card = tbd_round_card("Final Four", live_map, money_round=True)
    placements.append(f'<div class="placed" style="grid-column:5;grid-row:8 / span 1;">{ff_card}</div>')

    return f'''
    <div class="region-section">
      <div class="region-name">{safe_text(region_name)}</div>
      <div class="region-board">
        {"".join(placements)}
      </div>
    </div>
    '''


def render_visual_bracket(df: pd.DataFrame, live_map, games):
    ordered_regions = ["South", "West", "East", "Midwest"]

    st.markdown(
        '''
        <style>
        .bracket-wrap{padding:8px 0 24px 0;}
        .mobile-note{color:#667085;font-size:13px;margin:0 0 14px 4px;}
        .legend{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 14px 4px;font-size:12px;color:#667085;}
        .legend span{display:inline-flex;align-items:center;gap:6px;}
        .dot{width:12px;height:12px;border-radius:999px;display:inline-block;border:1px solid rgba(0,0,0,.08);}
        .title-strip{
            background:#fff;border:1px solid #e7ebf2;border-radius:18px;padding:16px 18px;margin-bottom:18px;
            display:flex;justify-content:center;gap:24px;align-items:center;flex-wrap:wrap;
        }
        .title-box{
            width:220px;background:#f7f4f0;border:1px solid #ebe3da;border-radius:16px;padding:14px 14px;text-align:center;
        }
        .title-box.money-title-box{background:#eefbf0;border-color:#9ed3a7;}
        .title-box h3{margin:0 0 6px 0;font-size:18px;color:#182230;}
        .title-box .big{font-size:28px;font-weight:800;margin:6px 0;color:#182230;}
        .title-box .small{font-size:13px;color:#667085;}
        .region-section{
            background:#fff;border:1px solid #e7ebf2;border-radius:20px;padding:18px 16px 18px 16px;margin-bottom:18px;overflow-x:auto;
        }
        .region-name{font-size:28px;font-weight:800;margin:2px 0 14px 6px;color:#182230;}
        .region-board{
            display:grid;
            grid-template-columns:220px 220px 220px 220px 220px;
            grid-template-rows:repeat(15, 116px);
            column-gap:16px;
            row-gap:14px;
            align-items:start;
            min-width:1164px;
        }
        .placed{align-self:start;}
        .match-card{
            width:220px;background:#f7f4f0;border:1px solid #ebe3da;border-radius:16px;padding:10px 10px 8px 10px;
            box-shadow:0 1px 0 rgba(0,0,0,.02);box-sizing:border-box;height:116px;
        }
        .match-card.money-round-card{background:#eefbf0;border-color:#9ed3a7;}
        .match-title{font-size:11px;font-weight:700;text-transform:uppercase;color:#7a6d61;margin-bottom:6px;letter-spacing:.35px;}
        .match-title.money-round-title{color:#187a2f;}
        .team-row{background:#f5f7fb;border:1px solid rgba(0,0,0,.04);border-radius:10px;padding:8px 9px;margin-top:6px;}
        .team-row.tbd{background:#fbfbfd;}
        .team-row.money-team{border-color:#73c285; box-shadow: inset 0 0 0 1px rgba(31, 130, 52, .18);}
        .team-main{display:flex;gap:8px;align-items:center;line-height:1.15;}
        .seed{font-size:12px;font-weight:800;color:#475467;min-width:15px;}
        .team-name{font-size:14px;font-weight:700;color:#182230;max-width:110px;}
        .team-sub{font-size:11px;color:#667085;margin-top:4px;}
        .team-status{font-size:11px;color:#187a2f;margin-top:4px;font-weight:700;display:flex;gap:4px;align-items:center;flex-wrap:wrap;}
        .money-icon{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:999px;background:#1f9f43;color:#fff;font-size:12px;font-weight:800;line-height:1;}
        .money-bills{font-size:13px;line-height:1;}
        .live-line{margin-top:4px;font-size:10px;color:#1d2939;display:flex;gap:4px;align-items:center;flex-wrap:wrap;}
        .live-line.final{color:#0f5132;}
        .live-chip{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:16px;border-radius:999px;background:#111827;color:#fff;padding:0 6px;font-size:9px;font-weight:800;line-height:1;}
        .live-detail{color:#667085;}
        </style>
        ''',
        unsafe_allow_html=True,
    )

    assigned_names = sorted({str(x).strip() for x in df["assigned_name"].fillna("") if str(x).strip()})[:10]
    legend_html = "".join(
        f'<span><i class="dot" style="background:{person_color(name)}"></i>{safe_text(name)}</span>'
        for name in assigned_names
    ) or '<span><i class="dot" style="background:#f5f7fb"></i>No assignments yet</span>'

    title_html = '''
    <div class="title-strip">
      <div class="title-box money-title-box">
        <h3>National Semifinals</h3>
        <div class="small">04 Apr</div>
        <div class="big">$ 💵</div>
        <div class="small">Money game</div>
      </div>
      <div class="title-box money-title-box">
        <h3>Championship</h3>
        <div class="small">06 Apr</div>
        <div class="big">$ 💵</div>
        <div class="small">Money game</div>
      </div>
    </div>
    '''
    st.markdown(
        '<div class="bracket-wrap"><div class="mobile-note">Public bracket only. Sweet 16 and later rounds are money games. This page auto-refreshes and automatically advances final winners into later rounds when ESPN has the results. Swipe sideways inside a region if needed.</div><div class="legend">'
        + legend_html + '</div>' + title_html + '</div>',
        unsafe_allow_html=True
    )

    for region in ordered_regions:
        region_df = df[df["region"] == region]
        st.markdown(build_region_html(region_df, region, live_map, games), unsafe_allow_html=True)


@st.fragment(run_every="45s")
def live_bracket_fragment(df: pd.DataFrame):
    results = fetch_espn_results()
    live_map = results.get("team_map", {}) if results.get("ok") else {}
    games = results.get("games", [])
    render_visual_bracket(df, live_map, games)
    st.caption("Auto-refreshing every 45 seconds.")


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🏀", layout="wide")
    data = load_data()
    teams = pd.DataFrame(data["teams"])
    if teams.empty:
        st.error("No teams found in teams.json")
        return

    st.title("🏀 2026 Todaro March Madness")
    st.caption("Public bracket view")

    live_bracket_fragment(teams)


if __name__ == "__main__":
    main()
