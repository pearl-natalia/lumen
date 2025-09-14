# app.py
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from pathlib import Path
import os, json, math, traceback, requests
from pymongo import MongoClient
from datetime import datetime

# -------------------- Env & paths --------------------
BASE_DIR   = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"

load_dotenv()                      # load .env first
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB")

# Initialize MongoDB client
try:
    if not MONGO_URI or not MONGO_DB:
        print("[WARN] MONGO_URI or MONGO_DB not set in environment variables", flush=True)
        mongo_client = None
        incidents_collection = None
    else:
        # Connect to MongoDB Atlas
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # Test the connection
        mongo_client.admin.command('ping')
        mongo_db = mongo_client[MONGO_DB]
        incidents_collection = mongo_db.incidents
        print(f"[INFO] Connected to MongoDB Atlas: {MONGO_DB}", flush=True)
except Exception as e:
    print(f"[WARN] MongoDB Atlas connection failed: {e}", flush=True)
    print("[INFO] Make sure MONGO_URI and MONGO_DB are set in your .env file", flush=True)
    mongo_client = None
    incidents_collection = None

# -------------------- Geocoding ----------------------
def _mapbox_geocode_one(q: str):
    """Return (lat, lon) using Mapbox so both routes share identical waypoints."""
    token = MAPBOX_TOKEN or os.getenv("MAPBOX_TOKEN")
    if not token:
        raise RuntimeError("MAPBOX_TOKEN missing")
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{requests.utils.quote(q)}.json"
    r = requests.get(url, params={"access_token": token, "limit": 1}, timeout=15)
    r.raise_for_status()
    js = r.json()
    if not js.get("features"):
        raise ValueError(f"Geocode failed: {q}")
    lon, lat = js["features"][0]["center"]  # Mapbox returns [lon,lat]
    return (lat, lon)

def _to_latlon(val):
    """
    Normalize any input to (lat, lon):
      - if [lon,lat] array from the frontend → convert to (lat, lon)
      - if string (place/address) → geocode via Mapbox → (lat, lon)
    """
    if isinstance(val, (list, tuple)) and len(val) == 2:
        lon, lat = float(val[0]), float(val[1])   # frontend sends [lon,lat]
        return (lat, lon)
    return _mapbox_geocode_one(str(val))

# -------------------- Generators ---------------------
# Your functions WRITE files (they do not return GeoJSON we rely on).
from get_safest_path import get_safest_path
from get_shortest_path import get_shortest_path

try:
    from get_safest_path import SAVE_GEOJSON as SAFE_FILE_DEFAULT
except Exception:
    SAFE_FILE_DEFAULT = "safest_route.geojson"

try:
    from get_shortest_path import OUTPUT_GEOJSON as SHORT_FILE_DEFAULT
except Exception:
    SHORT_FILE_DEFAULT = "shortest_route.geojson"

# -------------------- Flask app ----------------------
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.get("/token")
def token():
    return jsonify({"token": MAPBOX_TOKEN})

@app.get("/healthz")
def healthz():
    return "ok"

# -------------------- Helpers ------------------------
def _normalize(payload: dict):
    s = payload.get("start_text") or payload.get("start")
    e = payload.get("end_text")   or payload.get("end")
    if s is None or e is None:
        raise ValueError("Provide start_text/end_text OR start/end")
    return s, e

def _collect_features(gj: dict, route_type: str):
    """Coerce a GeoJSON dict into a list of Features and tag route_type."""
    feats = []
    if not isinstance(gj, dict):
        return feats
    t = gj.get("type")
    if t == "FeatureCollection":
        for f in (gj.get("features") or []):
            if isinstance(f, dict):
                f.setdefault("properties", {})["route_type"] = route_type
                feats.append(f)
    elif t == "Feature":
        gj.setdefault("properties", {})["route_type"] = route_type
        feats.append(gj)
    elif "coordinates" in gj:  # bare geometry-like
        feats.append({
            "type": "Feature",
            "properties": {"route_type": route_type},
            "geometry": {"type": "LineString", "coordinates": gj["coordinates"]},
        })
    return feats

def _try_paths(name: str):
    p = Path(name)
    return [p] if p.is_absolute() else [p, BASE_DIR / name]

def _load_geojson_file(candidates, route_type: str):
    """Load first existing candidate file; return features tagged with route_type."""
    if not isinstance(candidates, (list, tuple)):
        candidates = [candidates]
    for cand in candidates:
        for fp in _try_paths(cand):
            if fp.exists():
                try:
                    gj = json.loads(fp.read_text(encoding="utf-8"))
                    feats = _collect_features(gj, route_type)
                    # keep only LineStrings with coordinates
                    good = [f for f in feats if f.get("geometry", {}).get("type") == "LineString"
                            and f.get("geometry", {}).get("coordinates")]
                    if good:
                        return good
                except Exception as e:
                    print(f"[WARN] Failed to read {fp}: {e}", flush=True)
    return []

def _error_feature(route_type: str, message: str):
    return {
        "type": "Feature",
        "properties": {"route_type": route_type, "error": message},
        "geometry": {"type": "LineString", "coordinates": []},
    }

# ------------- Uniform ETA (monotonic) ----------------
WALK_SPEED_MPS = 1.25   # ~4.5 km/h typical
ETA_MULT       = 1.08   # global buffer for lights/crowds

def _haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2*R*math.asin(math.sqrt(a))

def _polyline_length_m(coords):
    if not coords or len(coords) < 2: return 0.0
    total = 0.0
    for (x1,y1), (x2,y2) in zip(coords[:-1], coords[1:]):
        total += _haversine_m(x1,y1,x2,y2)
    return total

def _apply_uniform_eta(features):
    """Overwrite length_m and time_s uniformly for all route features (no turn penalty)."""
    for f in features:
        if f.get("geometry", {}).get("type") != "LineString":
            continue
        coords = f["geometry"]["coordinates"]
        length_m = _polyline_length_m(coords)
        time_s   = (length_m / max(0.1, WALK_SPEED_MPS)) * ETA_MULT
        p = f.setdefault("properties", {})
        p["length_m"] = float(length_m)
        p["time_s"]   = float(time_s)

# -------------------- API ----------------------------
@app.post("/route")
def route():
    data = request.get_json(force=True)
    mode = (data.get("mode") or "both").lower()

    try:
        raw_start, raw_end = _normalize(data)   # text or [lon,lat]
        start_ll = _to_latlon(raw_start)        # (lat, lon)
        end_ll   = _to_latlon(raw_end)          # (lat, lon)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 400

    features = []

    # ---- Safest: run -> read file -> uniform ETA ----
    if mode in ("both", "safest"):
        err = ""
        try:
            get_safest_path(start_ll, end_ll)   # writes SAFE_FILE_DEFAULT
        except Exception:
            err = traceback.format_exc()
            print("[safest] exception:\n", err, flush=True)
        feats = _load_geojson_file([SAFE_FILE_DEFAULT, "safest_route.geojson"], "safest")
        _apply_uniform_eta(feats)
        features += feats if feats else [_error_feature("safest", f"Could not load {SAFE_FILE_DEFAULT}. {err}".strip())]

    # ---- Shortest: run -> read file -> uniform ETA ----
    if mode in ("both", "shortest"):
        err = ""
        try:
            get_shortest_path(start_ll, end_ll) # writes SHORT_FILE_DEFAULT
        except Exception:
            err = traceback.format_exc()
            print("[shortest] exception:\n", err, flush=True)
        feats = _load_geojson_file([SHORT_FILE_DEFAULT, "shortest_route.geojson"], "shortest")
        _apply_uniform_eta(feats)
        features += feats if feats else [_error_feature("shortest", f"Could not load {SHORT_FILE_DEFAULT}. {err}".strip())]

    # Always return a FeatureCollection
    return jsonify({"type": "FeatureCollection", "features": features})

@app.get("/crime-data")
def crime_data():
    """Fetch crime incidents from MongoDB and return street segments as GeoJSON for map overlay."""
    if incidents_collection is None:
        print("[INFO] MongoDB not connected, using mock street crime data for testing", flush=True)
        
        # Mock street crime data for downtown Kitchener area - using actual downtown coordinates
        mock_street_data = [
            {
                "street_name": "KING ST W",
                "incident_count": 3,
                "crime_types": ["Theft", "Vandalism"],
                "recent_incidents": [
                    {"call_type": "Theft", "formatted_date": "August 18, 2025", "formatted_time": "1:00 PM"},
                    {"call_type": "Vandalism", "formatted_date": "August 20, 2025", "formatted_time": "6:45 PM"}
                ],
                # King St W downtown core - main commercial strip
                "coordinates": [
                    [-80.4928, 43.4508], [-80.4940, 43.4508], [-80.4952, 43.4508], 
                    [-80.4964, 43.4508], [-80.4976, 43.4508], [-80.4988, 43.4508]
                ]
            },
            {
                "street_name": "WEBER ST N",
                "incident_count": 2,
                "crime_types": ["Assault", "Break and Enter"],
                "recent_incidents": [
                    {"call_type": "Assault", "formatted_date": "August 22, 2025", "formatted_time": "10:30 PM"},
                    {"call_type": "Break and Enter", "formatted_date": "August 25, 2025", "formatted_time": "3:15 AM"}
                ],
                # Weber St N - major north-south arterial
                "coordinates": [
                    [-80.4928, 43.4508], [-80.4928, 43.4520], [-80.4928, 43.4532], 
                    [-80.4928, 43.4544], [-80.4928, 43.4556], [-80.4928, 43.4568]
                ]
            },
            {
                "street_name": "UNIVERSITY AVE W",
                "incident_count": 1,
                "crime_types": ["Theft"],
                "recent_incidents": [
                    {"call_type": "Theft", "formatted_date": "August 28, 2025", "formatted_time": "2:20 PM"}
                ],
                # University Ave W in Waterloo - near UW campus
                "coordinates": [
                    [-80.5400, 43.4723], [-80.5420, 43.4723], [-80.5440, 43.4723], 
                    [-80.5460, 43.4723], [-80.5480, 43.4723], [-80.5500, 43.4723]
                ]
            },
            {
                "street_name": "VICTORIA ST N",
                "incident_count": 4,
                "crime_types": ["Vandalism", "Theft", "Assault"],
                "recent_incidents": [
                    {"call_type": "Assault", "formatted_date": "September 1, 2025", "formatted_time": "11:15 PM"},
                    {"call_type": "Theft", "formatted_date": "September 3, 2025", "formatted_time": "4:30 PM"},
                    {"call_type": "Vandalism", "formatted_date": "September 5, 2025", "formatted_time": "8:45 PM"}
                ],
                # Victoria St N downtown - parallel to Weber
                "coordinates": [
                    [-80.4900, 43.4508], [-80.4900, 43.4520], [-80.4900, 43.4532], 
                    [-80.4900, 43.4544], [-80.4900, 43.4556], [-80.4900, 43.4568]
                ]
            }
        ]
        
        features = []
        for street_data in mock_street_data:
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": street_data["coordinates"]
                },
                "properties": {
                    "street_name": street_data["street_name"],
                    "incident_count": street_data["incident_count"],
                    "crime_types": street_data["crime_types"],
                    "recent_incidents": street_data["recent_incidents"],
                    "incident_type": "street_crime"
                }
            }
            features.append(feature)
        
        print(f"[INFO] Returning {len(features)} mock street segments with crime data", flush=True)
        
        return jsonify({
            "type": "FeatureCollection", 
            "features": features
        })
    
    # Original MongoDB code for when database is connected
    try:
        # Get recent incidents (last 6 months for performance)
        six_months_ago = datetime.now().replace(month=datetime.now().month-6 if datetime.now().month > 6 else datetime.now().month+6, year=datetime.now().year-1 if datetime.now().month <= 6 else datetime.now().year)
        
        incidents = list(incidents_collection.find({
            "incident_date": {"$gte": six_months_ago}
        }).limit(1000))  # Limit for performance
        
        features = []
        for incident in incidents:
            # Geocode the location to get coordinates
            try:
                location_text = incident.get("location", "")
                if not location_text:
                    continue
                    
                # Use Mapbox geocoding to get coordinates for the location
                lat, lon = _mapbox_geocode_one(location_text + ", Kitchener, ON, Canada")
                
                # Format incident date
                incident_date = incident.get("incident_date")
                formatted_date = incident_date.strftime("%B %d, %Y") if incident_date else "Unknown date"
                formatted_time = incident_date.strftime("%I:%M %p") if incident_date else "Unknown time"
                
                # Create a point feature for the incident
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat]
                    },
                    "properties": {
                        "incident_id": incident.get("incident_id", ""),
                        "call_type": incident.get("call_type", "Unknown"),
                        "location": location_text,
                        "title_line": incident.get("title_line", ""),
                        "formatted_date": formatted_date,
                        "formatted_time": formatted_time,
                        "incident_type": "crime"
                    }
                }
                features.append(feature)
                
            except Exception as e:
                print(f"[WARN] Failed to geocode location '{location_text}': {e}", flush=True)
                continue
        
        return jsonify({
            "type": "FeatureCollection", 
            "features": features
        })
        
    except Exception as e:
        print(f"[ERROR] Crime data fetch failed: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

# -------------------- Main ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5500, debug=True)
