#!/usr/bin/env python3
# get_shortest_path.py — shortest walking route via Geoapify → clean LineString GeoJSON

import os, sys, json
import requests
from dotenv import load_dotenv

# ---------------- CONFIG ----------------

OUTPUT_GEOJSON = "shortest_route.geojson"
MODE       = "walk"     # walk | bicycle | drive | hike | transit ...
ROUTE_TYPE = "short"    # balanced | short | less_maneuvers

# ------------- LOAD ENV -----------------
load_dotenv()
API_KEY = os.getenv("GEOAPIFY_KEY")
if not API_KEY:
    sys.exit("❌ GEOAPIFY_KEY missing. Add it to your .env file.")

# ------------- HELPERS ------------------
def geocode_to_latlon(q: str):
    url = "https://api.geoapify.com/v1/geocode/search"
    r = requests.get(url, params={"text": q, "limit": 1, "apiKey": API_KEY}, timeout=20)
    r.raise_for_status()
    js = r.json()
    feats = js.get("features") or []
    if not feats:
        raise RuntimeError(f"Geocode failed: {q}")
    p = feats[0]["properties"]
    return float(p["lat"]), float(p["lon"])

def ensure_latlon(x):
    if isinstance(x, (tuple, list)) and len(x) == 2:
        return float(x[0]), float(x[1])
    return geocode_to_latlon(str(x))

def flatten_geometry(geom: dict):
    """
    Always return a flat list of [lon, lat] suitable for LineString.
    Handles LineString, MultiLineString, and strips altitude.
    """
    gtype = geom.get("type", "LineString")
    coords = geom["coordinates"]

    if gtype == "LineString":
        pts = coords
    elif gtype == "MultiLineString":
        pts = [pt for line in coords for pt in line]
    else:
        raise RuntimeError(f"Unsupported geometry type: {gtype}")

    return [[pt[0], pt[1]] for pt in pts if len(pt) >= 2]

# --------------- MAIN -------------------
def get_shortest_path(origin, dest):
    print('computing...')
    o_lat, o_lon = ensure_latlon(origin)
    d_lat, d_lon = ensure_latlon(dest)

    url = "https://api.geoapify.com/v1/routing"
    params = {
        "waypoints": f"{o_lat},{o_lon}|{d_lat},{d_lon}",
        "mode": MODE,
        "type": ROUTE_TYPE,
        "format": "geojson",
        "apiKey": API_KEY,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    gj_full = r.json()

    feat = gj_full["features"][0]
    props = feat.get("properties", {})
    coords = flatten_geometry(feat["geometry"])

    distance_m = props.get("distance", 0.0)
    time_s     = props.get("time", 0.0)

    # --- Minimal clean LineString GeoJSON ---
    out = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords  # [lon,lat] pairs
            },
            "properties": {
                "name": "Shortest Walk (Geoapify)",
                "length_m": float(distance_m),
                "time_s": float(time_s),
                "cost": float(distance_m)
            }
        }]
    }

    with open(OUTPUT_GEOJSON, "w") as f:
        json.dump(out, f)
    print(f"✅ Clean LineString GeoJSON saved → {OUTPUT_GEOJSON}")
    print(f"Path points: {len(coords)} | Length ≈ {distance_m/1000:.2f} km | ETA ≈ {time_s/60:.1f} min")

