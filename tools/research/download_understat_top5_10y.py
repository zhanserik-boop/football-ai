import csv, time, re, json, codecs, requests
from pathlib import Path
from bs4 import BeautifulSoup

LEAGUES={"EPL":"Premier League","La_liga":"La Liga","Serie_A":"Serie A","Bundesliga":"Bundesliga","Ligue_1":"Ligue 1"}
SEASONS=range(2016,2026)
COLS=["league","season","team","date","h_a","result","goals_for","goals_against","xg","xga","npxg","npxga","xpts","ppda_att","ppda_def","oppda_att","oppda_def"]
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

def teams_data(slug,season):
    url=f"https://understat.com/league/{slug}/{season}"
    r=requests.get(url,headers=HEADERS,timeout=30)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    for script in soup.find_all("script"):
        txt=script.string or ""
        if "teamsData" not in txt: continue
        m=re.search(r"teamsData\s*=\s*JSON\.parse\('(.+?)'\)",txt)
        if m:
            decoded=codecs.decode(m.group(1),'unicode_escape')
            data=json.loads(decoded)
            return list(data.values()) if isinstance(data,dict) else data
    raise RuntimeError(f"teamsData not found: {url}")

def main():
    rows=[]
    for slug,name in LEAGUES.items():
        for season in SEASONS:
            print(name,season,flush=True)
            teams=teams_data(slug,season)
            for team in teams:
                for h in team.get("history",[]):
                    p=h.get("ppda",{}) or {}; o=h.get("ppda_allowed",{}) or {}
                    rows.append({"league":name,"season":f"{season}/{str(season+1)[-2:]}","team":team.get("title"),"date":h.get("date"),"h_a":h.get("h_a"),"result":h.get("result"),"goals_for":h.get("scored"),"goals_against":h.get("missed"),"xg":h.get("xG"),"xga":h.get("xGA"),"npxg":h.get("npxG"),"npxga":h.get("npxGA"),"xpts":h.get("xpts"),"ppda_att":p.get("att"),"ppda_def":p.get("def"),"oppda_att":o.get("att"),"oppda_def":o.get("def")})
            time.sleep(0.7)
    out=Path("data/research/understat_top5_2016_2026_team_match.csv"); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=COLS); w.writeheader(); w.writerows(rows)
    print("DONE",len(rows),out)

if __name__=="__main__": main()
