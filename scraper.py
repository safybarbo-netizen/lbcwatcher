"""Scraper LBC — identique à l'app Python, adapté pour le backend."""
import requests, re, json, time
from urllib.parse import urlencode

MARQUE_SLUGS = {
    "Audi":"AUDI","BMW":"BMW","Citroën":"CITROEN","Dacia":"DACIA",
    "Ferrari":"FERRARI","Fiat":"FIAT","Ford":"FORD","Honda":"HONDA",
    "Hyundai":"HYUNDAI","Kia":"KIA","Lamborghini":"LAMBORGHINI",
    "Land Rover":"LAND_ROVER","Maserati":"MASERATI","Mazda":"MAZDA",
    "Mercedes-Benz":"MERCEDES_BENZ","Mini":"MINI","Mitsubishi":"MITSUBISHI",
    "Nissan":"NISSAN","Opel":"OPEL","Peugeot":"PEUGEOT","Porsche":"PORSCHE",
    "Renault":"RENAULT","Seat":"SEAT","Skoda":"SKODA","Subaru":"SUBARU",
    "Suzuki":"SUZUKI","Tesla":"TESLA","Toyota":"TOYOTA",
    "Volkswagen":"VOLKSWAGEN","Volvo":"VOLVO"
}
CARBURANT_SLUGS = {"Essence":"1","Diesel":"2","Électrique":"3","Hybride":"4","GPL":"5","Hydrogène":"6"}
BOITE_SLUGS     = {"Manuelle":"1","Automatique":"2"}
DISTANCE_VALS   = {"5 km":"5","10 km":"10","20 km":"20","30 km":"30","50 km":"50","75 km":"75","100 km":"100","150 km":"150","200 km":"200"}

def build_url(f: dict) -> str:
    p = {"category":"2"}
    kw = f.get("mot_cle","").strip()
    if kw: p["text"] = kw
    m = f.get("marque","")
    if m and m not in ("","Toutes marques"):
        s = MARQUE_SLUGS.get(m,"")
        if s: p["u_car_brand"] = s
    mo = f.get("modele","").strip()
    if mo: p["u_car_model"] = mo.upper().replace(" ","_")
    pmin,pmax = f.get("prix_min","").strip(), f.get("prix_max","").strip()
    if pmin or pmax: p["price"] = f"{pmin or '0'}-{pmax or '99999999'}"
    amin,amax = f.get("annee_min","").strip(), f.get("annee_max","").strip()
    if amin or amax: p["regdate"] = f"{amin or '1900'}-{amax or '2100'}"
    km = f.get("km_max","").strip()
    if km: p["mileage"] = f"0-{km}"
    carb = f.get("carburant","")
    if carb and carb not in ("","Tous"):
        s = CARBURANT_SLUGS.get(carb,"")
        if s: p["fuel"] = s
    bt = f.get("boite","")
    if bt and bt not in ("","Toutes"):
        s = BOITE_SLUGS.get(bt,"")
        if s: p["gearbox"] = s
    dep = f.get("departement","")
    if dep and dep not in ("","Tous"): p["locations"] = dep
    dv = DISTANCE_VALS.get(f.get("distance",""),"")
    if dv and dep and dep not in ("","Tous"): p["location_radius"] = dv
    p["sort"] = "time"; p["order"] = "desc"
    return "https://www.leboncoin.fr/recherche?" + urlencode(p)

def fetch_listings(filters: dict):
    url = build_url(filters)
    hdrs = {
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
        "Accept":"text/html,*/*;q=0.9",
        "Accept-Language":"fr-FR,fr;q=0.9",
        "Cache-Control":"no-cache",
    }
    try:
        s = requests.Session()
        s.get("https://www.leboncoin.fr/", headers=hdrs, timeout=10)
        time.sleep(1.5)
        r = s.get(url, headers=hdrs, timeout=20)
        if r.status_code != 200: return [], url, f"HTTP {r.status_code}"
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.DOTALL)
        if not m: return [], url, "Structure non reconnue"
        data = json.loads(m.group(1))
        pp = data.get("props",{}).get("pageProps",{})
        ads = []
        for path in [["searchData","ads"],["ads"],["initialData","ads"],["data","ads"]]:
            node, ok = pp, True
            for k in path:
                if isinstance(node,dict) and k in node: node=node[k]
                else: ok=False; break
            if ok and isinstance(node,list) and node: ads=node; break
        if not ads: return [], url, "Aucune annonce"
        res = []
        for ad in ads:
            if not isinstance(ad,dict): continue
            pr = ad.get("price")
            price = pr[0] if isinstance(pr,list) and pr else (pr if isinstance(pr,(int,float)) else None)
            loc = ad.get("location",{})
            city,zc = loc.get("city",""), (loc.get("zipcode","") or "")
            loc_s = f"{city} ({zc[:2]})" if zc else city
            au = ad.get("url","")
            if au and not au.startswith("http"): au = "https://www.leboncoin.fr"+au
            attrs = {}
            for a in ad.get("attributes",[]):
                if isinstance(a,dict): attrs[a.get("key","")] = a.get("value_label",a.get("value",""))
            has_phone = bool(ad.get("has_phone") or any(a.get("key","")=="phone" for a in ad.get("attributes",[]) if isinstance(a,dict)))
            res.append({"id":str(ad.get("list_id","")),"title":ad.get("subject",""),
                "price":price,"location":loc_s,"url":au,
                "date":ad.get("index_date",ad.get("first_publication_date","")),
                "attrs":attrs,"has_phone":has_phone})
        # Filtre téléphone
        if filters.get("only_phone"):
            res = [a for a in res if a.get("has_phone")]
        return res, url, None
    except requests.exceptions.Timeout: return [], url, "Timeout"
    except Exception as e: return [], url, str(e)
