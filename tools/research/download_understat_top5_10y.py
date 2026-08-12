import csv, time, requests
from pathlib import Path

LEAGUES={"EPL":"Premier League","La_Liga":"La Liga","Serie_A":"Serie A","Bundesliga":"Bundesliga","Ligue_1":"Ligue 1"}
SEASONS=range(2016,2026)
COLS=["league","season","team","date","h_a","result","goals_for","goals_against","xg","xga","npxg","npxga","xpts","ppda_att","ppda_def","ppda","oppda_att","oppda_def","oppda"]
HEADERS={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36","X-Requested-With":"XMLHttpRequest","Referer":"https://understat.com/","Accept":"application/json,text/plain,*/*"}

def ratio(a,b):
    try:
        return float(a)/float(b) if float(b)!=0 else ""
    except Exception:
        return ""

def teams_data(slug,season):
    url=f"https://understat.com/main/getLeagueData/{slug}/{season}"
    r=requests.post(url,headers=HEADERS,timeout=30)
    r.raise_for_status()
    data=r.json()
    teams=data.get("teams",{})
    return list(teams.values()) if isinstance(teams,dict) else teams

def main():
    rows=[]
    for slug,name in LEAGUES.items():
        for season in SEASONS:
            print(name,season,flush=True)
            teams=teams_data(slug,season)
            print(" teams",len(teams),flush=True)
            for team in teams:
                for h in team.get("history",[]):
                    p=h.get("ppda",{}) or {}; o=h.get("ppda_allowed",{}) or {}
                    rows.append({"league":name,"season":f"{season}/{str(season+1)[-2:]}","team":team.get("title"),"date":h.get("date"),"h_a":h.get("h_a"),"result":h.get("result"),"goals_for":h.get("scored"),"goals_against":h.get("missed"),"xg":h.get("xG"),"xga":h.get("xGA"),"npxg":h.get("npxG"),"npxga":h.get("npxGA"),"xpts":h.get("xpts"),"ppda_att":p.get("att"),"ppda_def":p.get("def"),"ppda":ratio(p.get("att"),p.get("def")),"oppda_att":o.get("att"),"oppda_def":o.get("def"),"oppda":ratio(o.get("att"),o.get("def"))})
            time.sleep(0.45)
    out=Path("data/research/understat_top5_2016_2026_team_match.csv"); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=COLS); w.writeheader(); w.writerows(rows)
    print("DONE",len(rows),out,flush=True)

if __name__=="__main__": main()
