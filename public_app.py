
import html
import json
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


@st.cache_data(ttl=45, show_spinner=False)
def fetch_espn_scoreboard():
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    try:
        r = requests.get(url, params={"groups": 50, "limit": 300}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "games": [], "team_map": {}}

    games = []
    team_map = {}
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
                    "home_away": comp.get("homeAway", "") or "",
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

        opp0 = parsed[1]["team"]
        opp1 = parsed[0]["team"]
        team_map[parsed[0]["team"]] = {
            "score": parsed[0]["score"],
            "opp": opp0,
            "opp_score": parsed[1]["score"],
            "status": status_desc,
            "detail": status_detail,
            "winner": parsed[0]["winner"],
        }
        team_map[parsed[1]["team"]] = {
            "score": parsed[1]["score"],
            "opp": opp1,
            "opp_score": parsed[0]["score"],
            "status": status_desc,
            "detail": status_detail,
            "winner": parsed[1]["winner"],
        }

    return {"ok": True, "error": "", "games": games, "team_map": team_map}


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
    team_name = str(team_row.get("team", "") or "")
    live = live_map.get(team_name)
    if not live:
        return ""

    status = str(live.get("status", "") or "")
    detail = safe_text(live.get("detail", "") or "")
    score = safe_text(live.get("score", "") or "")
    opp = safe_text(live.get("opp", "") or "")
    opp_score = safe_text(live.get("opp_score", "") or "")
    winner = bool(live.get("winner", False))

    if "STATUS_HALFTIME" in status.upper():
        label = "HALF"
    elif status.lower() == "in progress":
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


def build_region_html(region_df: pd.DataFrame, region_name: str, live_map):
    matchups = region_matchups(region_df)
    placements = []

    for i, (a, b) in enumerate(matchups):
        row_start = 1 + i * 2
        placements.append(f'<div class="placed" style="grid-column:1;grid-row:{row_start} / span 1;">{matchup_card(a,b,live_map)}</div>')

    for i in range(4):
        row_start = 2 + i * 4
        placements.append(f'<div class="placed" style="grid-column:2;grid-row:{row_start} / span 1;">{tbd_round_card("Round of 32", live_map)}</div>')

    for i in range(2):
        row_start = 4 + i * 8
        placements.append(f'<div class="placed" style="grid-column:3;grid-row:{row_start} / span 1;">{tbd_round_card("Sweet 16", live_map, money_round=True)}</div>')

    placements.append(f'<div class="placed" style="grid-column:4;grid-row:8 / span 1;">{tbd_round_card("Elite 8", live_map, money_round=True)}</div>')
    placements.append(f'<div class="placed" style="grid-column:5;grid-row:8 / span 1;">{tbd_round_card("Final Four", live_map, money_round=True)}</div>')

    return f'''
    <div class="region-section">
      <div class="region-name">{safe_text(region_name)}</div>
      <div class="region-board">
        {"".join(placements)}
      </div>
    </div>
    '''


def render_visual_bracket(df: pd.DataFrame, live_map):
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
        '<div class="bracket-wrap"><div class="mobile-note">Public bracket only. Sweet 16 and later rounds are money games. Live scores appear inside team cards when ESPN has them available. Swipe sideways inside a region if needed.</div><div class="legend">'
        + legend_html + '</div>' + title_html + '</div>',
        unsafe_allow_html=True
    )

    for region in ordered_regions:
        region_df = df[df["region"] == region]
        st.markdown(build_region_html(region_df, region, live_map), unsafe_allow_html=True)


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🏀", layout="wide")
    data = load_data()
    teams = pd.DataFrame(data["teams"])
    if teams.empty:
        st.error("No teams found in teams.json")
        return

    live = fetch_espn_scoreboard()
    live_map = live.get("team_map", {}) if live.get("ok") else {}

    st.title("🏀 2026 Todaro March Madness")
    st.caption("Public bracket view")

    render_visual_bracket(teams, live_map)


if __name__ == "__main__":
    main()
