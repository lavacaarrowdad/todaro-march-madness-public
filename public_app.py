
import html, json, re
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import requests
import streamlit as st

APP_TITLE = "2026 Todaro March Madness"
DATA_PATH = Path(__file__).with_name("teams.json")
TICKET_VALUES = {"Sweet 16": 1, "Elite 8": 2, "Elite Eight": 2, "Final Four": 3, "Championship": 4, "Champion": 4}

def load_data():
    return json.loads(DATA_PATH.read_text(encoding="utf-8")) if DATA_PATH.exists() else {"teams":[]}

def normalize(s: str) -> str:
    s = (s or "").lower().strip().replace("&", " and ").replace("st.", "saint").replace("st ", "saint ").replace("(oh)", " ohio").replace("/", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def aliases(team_name: str):
    raw = (team_name or "").strip()
    out = {normalize(raw)}
    for p in [x.strip() for x in raw.split("/") if x.strip()]:
        out.add(normalize(p))
    extra = {
        "saint mary s": ["saint marys", "saint mary's"],
        "north carolina": ["unc"],
        "miami ohio smu": ["miami ohio", "smu"],
        "umbc howard": ["umbc", "howard"],
        "lehigh prairie view a m": ["lehigh", "prairie view a m"],
        "texas nc state": ["texas", "nc state"],
        "saint john s": ["saint johns", "st john s", "st johns"],
    }
    n = normalize(raw)
    if n in extra:
        out.update(extra[n])
    return out

@st.cache_data(ttl=45, show_spinner=False)
def fetch_espn():
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    team_map, games = {}, []
    for offset in range(-21, 2):
        d = (date.today() + timedelta(days=offset)).strftime("%Y%m%d")
        try:
            r = requests.get(url, params={"groups":50,"limit":300,"dates":d}, timeout=12)
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
            parsed = []
            for c in competitors:
                parsed.append({"team": c.get("team", {}).get("displayName", "") or "", "score": str(c.get("score", "") or ""), "winner": bool(c.get("winner", False))})
            games.append({"status": status, "detail": detail, "teams": parsed})
            priority = 2 if status.lower()=="final" else 1 if status.lower()=="in progress" else 0
            for i, t in enumerate(parsed):
                opp = parsed[1-i]
                k = normalize(t["team"])
                old = team_map.get(k, {"_p":-1})
                if priority >= old["_p"]:
                    team_map[k] = {"score": t["score"], "opp": opp["team"], "opp_score": opp["score"], "status": status, "detail": detail, "winner": t["winner"], "_p": priority}
    for v in team_map.values():
        v.pop("_p", None)
    return {"games": games, "team_map": team_map}

def get_live(team_name: str, live_map: dict):
    for a in aliases(team_name):
        if a in live_map:
            return live_map[a]
    return None

def decide_winner(team_a, team_b, games):
    if not team_a or not team_b:
        return None
    aa, bb = aliases(team_a["team"]), aliases(team_b["team"])
    for g in reversed(games):
        if g["status"].lower() != "final":
            continue
        t0 = normalize(g["teams"][0]["team"]); t1 = normalize(g["teams"][1]["team"])
        if not ((t0 in aa and t1 in bb) or (t0 in bb and t1 in aa)):
            continue
        for t in g["teams"]:
            if t["winner"]:
                wn = normalize(t["team"])
                if wn in aa: return team_a
                if wn in bb: return team_b
    return None

def person_color(name: str) -> str:
    if not str(name or "").strip():
        return "#f5f7fb"
    palette = ["#e8f1ff","#f6e8ff","#e9fff4","#fff3e8","#eef0ff","#ffeef5","#eefcf2","#fff9df","#edf7ff","#f3edff"]
    return palette[sum(ord(c) for c in str(name).strip().lower()) % len(palette)]

def safe(x): return html.escape(str(x or ""))

def tickets_for(row) -> int:
    return max(TICKET_VALUES.get(str(row.get("round_reached","")).strip(), 0), TICKET_VALUES.get(str(row.get("manual_status","")).strip(), 0))

def ticket_label(v: int) -> str:
    return f"{v} ticket" if v == 1 else f"{v} tickets"

def icon_html() -> str:
    return '<span class="money-icon">$</span><span class="money-bills">🎟️</span>'

def totals_by_name(df):
    totals = {}
    for _, r in df.iterrows():
        n = str(r.get("assigned_name","") or "").strip()
        if n:
            totals[n] = totals.get(n, 0) + tickets_for(r)
    return totals

def live_line(row, live_map):
    live = get_live(str(row.get("team","")), live_map)
    if not live:
        return ""
    status = str(live.get("status","") or "")
    label = "LIVE" if status.lower()=="in progress" else ("W" if live.get("winner") else "L") if status.lower()=="final" else status.upper()[:10]
    cls = "live-line final" if status.lower()=="final" else "live-line"
    return f'<div class="{cls}"><span class="live-chip">{safe(label)}</span><span>{safe(live.get("score",""))}-{safe(live.get("opp_score",""))} vs {safe(live.get("opp",""))}</span><span class="live-detail">{safe(live.get("detail",""))}</span></div>'

def team_line(row, live_map):
    if row is None:
        return '<div class="team-row tbd"><div class="team-main"><span class="seed">•</span><span class="team-name">TBD</span></div></div>'
    ticket_ct = tickets_for(row)
    badge = icon_html() if ticket_ct else ""
    status = safe(row.get("manual_status",""))
    status_html = f'<div class="team-status">{badge}{status}</div>' if status else (f'<div class="team-status">{badge}{ticket_label(ticket_ct)}</div>' if ticket_ct else "")
    sub = []
    if str(row.get("assigned_name","")).strip(): sub.append(safe(row["assigned_name"]))
    if str(row.get("slot_note","")).strip(): sub.append(safe(row["slot_note"]))
    if str(row.get("round_reached","")).strip(): sub.append(safe(row["round_reached"]))
    sub_html = f'<div class="team-sub">{" • ".join(sub)}</div>' if sub else ""
    extra = " money-team" if ticket_ct else ""
    return f'<div class="team-row{extra}" style="background:{person_color(row.get("assigned_name",""))}"><div class="team-main"><span class="seed">{safe(row["seed"])}</span><span class="team-name">{safe(row["team"])}</span>{badge}</div>{sub_html}{status_html}{live_line(row, live_map)}</div>'

def matchup_card(top_row, bottom_row, live_map, title="", tickets=0):
    title_html = f'<div class="{"match-title money-round-title" if tickets else "match-title"}">{icon_html() if tickets else ""}{safe(ticket_label(tickets) if tickets else title)}</div>' if title else ""
    extra = " money-round-card" if tickets else ""
    return f'<div class="match-card{extra}">{title_html}{team_line(top_row, live_map)}{team_line(bottom_row, live_map)}</div>'

def region_matchups(region_df):
    seed_to_row = {int(r["seed"]): r.to_dict() for _, r in region_df.iterrows()}
    order = [(1,16),(8,9),(5,12),(4,13),(6,11),(3,14),(7,10),(2,15)]
    return [(seed_to_row[a], seed_to_row[b]) for a,b in order]

def build_region(region_df, region_name, live_map, games):
    m = region_matchups(region_df)
    p = []
    r64 = []
    for i, (a,b) in enumerate(m):
        p.append(f'<div class="placed" style="grid-column:1;grid-row:{1+i*2} / span 1;">{matchup_card(a,b,live_map)}</div>')
        r64.append(decide_winner(a,b,games))
    pairs32 = [(r64[0],r64[1]),(r64[2],r64[3]),(r64[4],r64[5]),(r64[6],r64[7])]
    w32 = []
    for i, (a,b) in enumerate(pairs32):
        p.append(f'<div class="placed" style="grid-column:2;grid-row:{2+i*4} / span 1;">{matchup_card(a,b,live_map,"Round of 32") if a and b else matchup_card(None,None,live_map,"Round of 32")}</div>')
        w32.append(decide_winner(a,b,games) if a and b else None)
    pairs16 = [(w32[0],w32[1]),(w32[2],w32[3])]
    w16 = []
    for i, (a,b) in enumerate(pairs16):
        p.append(f'<div class="placed" style="grid-column:3;grid-row:{4+i*8} / span 1;">{matchup_card(a,b,live_map,"Sweet 16",1) if a and b else matchup_card(None,None,live_map,"Sweet 16",1)}</div>')
        w16.append(decide_winner(a,b,games) if a and b else None)
    if w16[0] and w16[1]:
        elite = matchup_card(w16[0], w16[1], live_map, "Elite 8", 2)
        welite = decide_winner(w16[0], w16[1], games)
    else:
        elite = matchup_card(None, None, live_map, "Elite 8", 2); welite = None
    p.append(f'<div class="placed" style="grid-column:4;grid-row:8 / span 1;">{elite}</div>')
    ff = matchup_card(welite, None, live_map, "Final Four", 3) if welite else matchup_card(None,None,live_map,"Final Four",3)
    p.append(f'<div class="placed" style="grid-column:5;grid-row:8 / span 1;">{ff}</div>')
    return f'<div class="region-section"><div class="region-name">{safe(region_name)}</div><div class="region-board">{"".join(p)}</div></div>'

def render(df, live_map, games):
    st.markdown("""
    <style>
    .bracket-wrap{padding:8px 0 24px 0;}
    .mobile-note{color:#667085;font-size:13px;margin:0 0 14px 4px;}
    .legend{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 14px 4px;font-size:12px;color:#667085;}
    .legend span{display:inline-flex;align-items:center;gap:6px;}
    .dot{width:12px;height:12px;border-radius:999px;display:inline-block;border:1px solid rgba(0,0,0,.08);}
    .title-strip,.totals-strip{background:#fff;border:1px solid #e7ebf2;border-radius:18px;padding:14px 16px;margin-bottom:18px;}
    .title-strip{display:flex;justify-content:center;gap:24px;align-items:center;flex-wrap:wrap;}
    .title-box{width:220px;background:#eefbf0;border:1px solid #9ed3a7;border-radius:16px;padding:14px;text-align:center;}
    .title-box h3{margin:0 0 6px 0;font-size:18px;color:#182230;}
    .title-box .big{font-size:28px;font-weight:800;margin:6px 0;color:#182230;}
    .title-box .small{font-size:13px;color:#667085;}
    .totals-title{font-size:16px;font-weight:800;color:#182230;margin-bottom:10px;}
    .totals-grid{display:flex;gap:12px;flex-wrap:wrap;}
    .total-chip{border:1px solid #d8e5dc;border-radius:999px;padding:8px 12px;font-size:13px;font-weight:700;color:#14532d;background:#f3fff5;display:inline-flex;align-items:center;gap:8px;}
    .region-section{background:#fff;border:1px solid #e7ebf2;border-radius:20px;padding:18px 16px;margin-bottom:18px;overflow-x:auto;}
    .region-name{font-size:28px;font-weight:800;margin:2px 0 14px 6px;color:#182230;}
    .region-board{display:grid;grid-template-columns:220px 220px 220px 220px 220px;grid-template-rows:repeat(15,116px);column-gap:16px;row-gap:14px;align-items:start;min-width:1164px;}
    .placed{align-self:start;}
    .match-card{width:220px;background:#f7f4f0;border:1px solid #ebe3da;border-radius:16px;padding:10px 10px 8px;box-shadow:0 1px 0 rgba(0,0,0,.02);box-sizing:border-box;height:116px;}
    .match-card.money-round-card{background:#eefbf0;border-color:#9ed3a7;}
    .match-title{font-size:11px;font-weight:700;text-transform:uppercase;color:#7a6d61;margin-bottom:6px;letter-spacing:.35px;}
    .match-title.money-round-title{color:#187a2f;}
    .team-row{background:#f5f7fb;border:1px solid rgba(0,0,0,.04);border-radius:10px;padding:8px 9px;margin-top:6px;}
    .team-row.tbd{background:#fbfbfd;}
    .team-row.money-team{border-color:#73c285;box-shadow: inset 0 0 0 1px rgba(31,130,52,.18);}
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
    """, unsafe_allow_html=True)

    assigned = sorted({str(x).strip() for x in df["assigned_name"].fillna("") if str(x).strip()})[:10]
    legend_html = "".join(f'<span><i class="dot" style="background:{person_color(n)}"></i>{safe(n)}</span>' for n in assigned) or '<span><i class="dot" style="background:#f5f7fb"></i>No assignments yet</span>'
    totals = totals_by_name(df)
    totals_html = "".join(f'<div class="total-chip">{icon_html()}<span>{safe(n)}: {safe(ticket_label(v))}</span></div>' for n,v in sorted(totals.items(), key=lambda x: (-x[1], x[0].lower()))) or '<div class="total-chip"><span>No tickets won yet</span></div>'
    title_html = '<div class="title-strip"><div class="title-box"><h3>Sweet 16</h3><div class="big">1 🎟️</div><div class="small">1 ticket</div></div><div class="title-box"><h3>Elite 8</h3><div class="big">2 🎟️</div><div class="small">2 tickets</div></div><div class="title-box"><h3>Final Four</h3><div class="big">3 🎟️</div><div class="small">3 tickets</div></div><div class="title-box"><h3>Championship</h3><div class="big">4 🎟️</div><div class="small">4 tickets</div></div></div>'
    totals_block = f'<div class="totals-strip"><div class="totals-title">Total tickets won</div><div class="totals-grid">{totals_html}</div></div>'
    st.markdown('<div class="bracket-wrap"><div class="mobile-note">Public bracket only. Sweet 16 and later rounds award tickets. This page auto-refreshes and automatically advances final winners into later rounds when ESPN has the results. Swipe sideways inside a region if needed.</div><div class="legend">' + legend_html + '</div>' + totals_block + title_html + '</div>', unsafe_allow_html=True)
    for region in ["South","West","East","Midwest"]:
        st.markdown(build_region(df[df["region"]==region], region, live_map, games), unsafe_allow_html=True)

@st.fragment(run_every="45s")
def live_bracket_fragment(df: pd.DataFrame):
    results = fetch_espn()
    render(df, results.get("team_map", {}), results.get("games", []))
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
