import requests, json

def get_incidents(csv_file="sources/incidents.csv", max_pages=50, stop_after_consecutive_empty=3, sleep_between=0.4):
    import os, re, csv, requests, time
    from bs4 import BeautifulSoup
    """
    Scrape all WRPS incident pages, filter for Kitchener/Waterloo,
    and append new incidents to a CSV file (creating it if needed).
    Deduplicates by 'incident_id'.
    """
    BASE = "https://wrps.ca/news/incidents"
    HEADERS = {"User-Agent": "Mozilla/5.0 (WRPS scraper for research; contact: you@example.com)"}
    KW_CITIES = {"KITCHENER", "WATERLOO"}

    # Regex patterns for parsing WRPS text blocks
    RE_TITLE_ID    = re.compile(r"^(WA\d{8}\s*-\s*.+)$", re.MULTILINE)
    RE_INCIDENT_NO = re.compile(r"Incident\s*#:\s*(WA\d+)", re.IGNORECASE)
    RE_POSTED_ON   = re.compile(r"Posted on:\s*([^\n]+)")
    RE_CALL_TYPE   = re.compile(
        r"^\s*(Break & Enter|Disturbance|Fire|MVC Personal Injury|Offensive Weapon|Property Damage|Robbery|Theft|Traffic)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    RE_INCIDENT_DT = re.compile(r"Incident Date:\s*([^\n]+)", re.IGNORECASE)
    RE_LOCATION    = re.compile(
        r"^\s*([A-Z0-9 ,.&'/()-]+(?:, (?:WATERLOO|KITCHENER|CAMBRIDGE|WATERLOO REGION|NORTH DUMFRIES|WELLESLEY|WILMOT|WOOLWICH|OUTSIDE REGION|ON))?)\s*$",
        re.MULTILINE,
    )

    # Load existing IDs from CSV
    existing_ids = set()
    if os.path.exists(csv_file):
        with open(csv_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("incident_id"):
                    existing_ids.add(row["incident_id"])

    new_rows = []
    empty_streak = 0

    for page in range(1, max_pages + 1):
        url = BASE + (f"?page={page-1}" if page > 1 else "")
        try:
            r = requests.get(url, params={}, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        if "Automated Incidents" not in text:
            empty_streak += 1
            if empty_streak >= stop_after_consecutive_empty:
                break
            continue

        # Split text into blocks by WA###### headers
        starts = list(RE_TITLE_ID.finditer(text))
        if not starts:
            empty_streak += 1
            if empty_streak >= stop_after_consecutive_empty:
                break
            continue

        for i, m in enumerate(starts):
            start = m.start()
            end = starts[i+1].start() if i + 1 < len(starts) else len(text)
            blk = text[start:end]

            m_id = RE_INCIDENT_NO.search(blk) or re.search(r"(WA\d{8})", blk)
            if not m_id:
                continue
            inc_no = m_id.group(1)

            posted_on   = (RE_POSTED_ON.search(blk) or [None, ""])[1].strip()
            call_type   = (RE_CALL_TYPE.search(blk) or [None, ""])[1].title().strip()
            title_line  = (RE_TITLE_ID.search(blk) or [None, ""])[1].strip()
            incident_dt = (RE_INCIDENT_DT.search(blk) or [None, ""])[1].strip()
            loc = ""
            if incident_dt:
                after = blk[blk.find(incident_dt) + len(incident_dt):]
                mloc = RE_LOCATION.search(after)
                if mloc:
                    loc = mloc.group(1).strip()

            loc_up = loc.upper()
            city = None
            for c in KW_CITIES:
                if c in loc_up:
                    city = c.capitalize()
                    break
            if not city:
                continue

            if inc_no not in existing_ids:
                new_rows.append({
                    "incident_id": inc_no,
                    "posted_on": posted_on,
                    "incident_date": incident_dt,
                    "call_type": call_type,
                    "title_line": title_line,
                    "location": loc,
                    "city": city,
                    "page_url": url,
                })
                existing_ids.add(inc_no)

        empty_streak = 0
        if sleep_between:
            time.sleep(sleep_between)

    if new_rows:
        exists = os.path.exists(csv_file)
        with open(csv_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "incident_id","posted_on","incident_date","call_type","title_line","location","city","page_url"
            ])
            if not exists:
                writer.writeheader()
            writer.writerows(new_rows)
        print(f"Added {len(new_rows)} new Kitchener/Waterloo incidents.")
    else:
        print("No new Kitchener/Waterloo incidents found.")
  
def get_collisions(out_path="sources/collisions.csv", city_filter=None, page_size=2000):
    import requests, csv, os, sys, datetime as dt
    BASE = "https://services1.arcgis.com/qAo1OsXi67t7XgmS/arcgis/rest/services/Traffic_Collisions/FeatureServer/0/query"
    header = ["DATE","TIME","LATITUDE","LONGITUDE","PEDESTRIANINVOLVED","ACCIDENTNUM"]

    # load existing keys (dedupe by ACCIDENTNUM; fallback OID-<OBJECTID>)
    seen = set()
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                k = (row.get("ACCIDENTNUM") or "").strip()
                if k:
                    seen.add(k)

    parts = ["1=1"]
    if city_filter:
        quoted = ",".join(f"'{c}'" for c in city_filter)
        parts.append(f"CITY IN ({quoted})")
    base_where = " AND ".join(parts)

    last_oid, new_rows = -1, []
    sess = requests.Session()

    while True:
        params = {
            "where": f"{base_where} AND OBJECTID>{last_oid}",
            "outFields": "*",
            "orderByFields": "OBJECTID",
            "resultRecordCount": page_size,
            "f": "geojson",
        }
        resp = sess.get(BASE, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        feats = data.get("features", [])
        if not feats:
            break

        for feat in feats:
            props = feat.get("properties") or {}
            oid = props.get("OBJECTID", last_oid)
            try:
                last_oid = max(last_oid, int(oid))
            except Exception:
                pass

            accnum = props.get("ACCIDENTNUM")
            accnum = (str(accnum).strip() if accnum else f"OID-{oid}")
            if accnum in seen:
                continue

            # Date/time
            date_str, time_str = "", ""
            ms = props.get("ACCIDENTDATE")
            if isinstance(ms, (int, float)):
                ts = dt.datetime.utcfromtimestamp(ms/1000.0)
                date_str = ts.strftime("%Y-%m-%d")
                time_str = ts.strftime("%H:%M")

            # Coordinates
            lat, lon = props.get("LATITUDE"), props.get("LONGITUDE")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or (lat == 0 and lon == 0):
                g = feat.get("geometry") or {}
                if g.get("type") == "Point":
                    c = g.get("coordinates") or []
                    if len(c) >= 2:
                        lon, lat = c[0], c[1]

            ped = props.get("PEDESTRIANINVOLVED")
            if isinstance(ped, str):
                ped = ped.strip().lower() in {"true","t","1","yes","y"}

            new_rows.append({
                "DATE": date_str,
                "TIME": time_str,
                "LATITUDE": lat,
                "LONGITUDE": lon,
                "PEDESTRIANINVOLVED": str(ped),  # force string True/False
                "ACCIDENTNUM": accnum,
            })
            seen.add(accnum)

        if not (data.get("properties") or {}).get("exceededTransferLimit") or len(feats) < page_size:
            break

    mode = "a" if os.path.exists(out_path) else "w"
    with open(out_path, mode, encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if mode == "w":
            w.writeheader()
        if new_rows:
            w.writerows(new_rows)

    print(f"{'Created' if mode=='w' else 'Updated'} {out_path} | +{len(new_rows)} new rows")

get_collisions()
get_incidents()