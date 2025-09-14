# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
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
MONGO_URI = os.getenv("MONGODB_URI")  # Changed from MONGO_URI to MONGODB_URI
MONGO_DB = os.getenv("MONGO_DB")

# Initialize MongoDB client
try:
    if not MONGO_URI or not MONGO_DB:
        print("[WARN] MONGODB_URI or MONGO_DB not set in environment variables", flush=True)
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
    print("[INFO] Make sure MONGODB_URI and MONGO_DB are set in your .env file", flush=True)
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
CORS(app)  # Enable CORS for all routes

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

@app.get("/debug-streets")
def debug_streets():
    """Debug endpoint to show which streets are being parsed from MongoDB."""
    if incidents_collection is None:
        return jsonify({"error": "MongoDB not connected", "streets": []})
    
    try:
        # Get all incidents from MongoDB
        incidents = list(incidents_collection.find({}).limit(2000))
        
        # Group incidents by street name
        street_incidents = {}
        raw_locations = []
        
        for incident in incidents:
            location_text = incident.get("location", "").strip()
            raw_locations.append(location_text)
            
            location_upper = location_text.upper()
            if not location_upper:
                continue
            
            # Extract street name from location
            street_name = None
            
            # Try to extract street name from various formats
            if " ST " in location_upper or location_upper.endswith(" ST"):
                street_name = location_upper.split(" BLOCK ")[0] if " BLOCK " in location_upper else location_upper
                street_name = street_name.replace(" BLOCK", "").strip()
            elif " AVE " in location_upper or location_upper.endswith(" AVE"):
                street_name = location_upper.split(" BLOCK ")[0] if " BLOCK " in location_upper else location_upper
                street_name = street_name.replace(" BLOCK", "").strip()
            elif " RD " in location_upper or location_upper.endswith(" RD"):
                street_name = location_upper.split(" BLOCK ")[0] if " BLOCK " in location_upper else location_upper
                street_name = street_name.replace(" BLOCK", "").strip()
            elif " DR " in location_upper or location_upper.endswith(" DR"):
                street_name = location_upper.split(" BLOCK ")[0] if " BLOCK " in location_upper else location_upper
                street_name = street_name.replace(" BLOCK", "").strip()
            else:
                # Try to extract from other formats
                parts = location_upper.split()
                if len(parts) >= 2:
                    # Remove potential house numbers from the beginning
                    if parts[0].isdigit():
                        street_name = " ".join(parts[1:])
                    else:
                        street_name = location_upper
            
            if not street_name:
                continue
                
            # Clean up street name
            street_name = street_name.replace("BLOCK OF ", "").replace(" BLOCK", "").strip()
            
            if street_name not in street_incidents:
                street_incidents[street_name] = 0
            street_incidents[street_name] += 1
        
        # Sort streets by incident count
        sorted_streets = sorted(street_incidents.items(), key=lambda x: x[1], reverse=True)
        
        return jsonify({
            "total_incidents": len(incidents),
            "total_streets": len(street_incidents),
            "sample_raw_locations": raw_locations[:10],
            "streets_by_incident_count": sorted_streets[:20],
            "all_street_names": list(street_incidents.keys())
        })
        
    except Exception as e:
        return jsonify({"error": str(e), "streets": []})

def format_date(date_str):
    """Format date string for display"""
    if not date_str:
        return "Unknown"
    try:
        # Handle different date formats
        if isinstance(date_str, str):
            # Try parsing common date formats
            from datetime import datetime
            for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S']:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    return dt.strftime('%Y-%m-%d')
                except ValueError:
                    continue
        return str(date_str)
    except Exception:
        return "Unknown"

def format_time(time_str):
    """Format time string for display"""
    if not time_str:
        return "Unknown"
    try:
        # Handle different time formats
        if isinstance(time_str, str):
            from datetime import datetime
            for fmt in ['%H:%M:%S', '%H:%M', '%I:%M %p', '%I:%M:%S %p']:
                try:
                    dt = datetime.strptime(time_str, fmt)
                    return dt.strftime('%H:%M')
                except ValueError:
                    continue
        return str(time_str)
    except Exception:
        return "Unknown"

@app.route('/crime-data')
def get_crime_data():
    """Get crime data aggregated by street for frontend Mapbox vector tile matching"""
    if incidents_collection is None:
        print("[WARN] MongoDB not connected", flush=True)
        return jsonify({"streets": []})
    
    try:
        print(f"[DEBUG] Fetching crime data from MongoDB...")
        
        # Get all incidents from the collection
        incidents = list(incidents_collection.find())
        print(f"[DEBUG] Found {len(incidents)} total incidents")
        
        if not incidents:
            print("[DEBUG] No incidents found in database")
            return jsonify({"streets": []})
        
        # Group incidents by street name
        street_data = {}
        
        for incident in incidents:
            location = incident.get('location', '')
            if not location:
                continue
                
            # Parse street name (everything before the comma)
            street_name = location.split(',')[0].strip()
            if not street_name:
                continue
                
            print(f"[DEBUG] Processing incident on street: {street_name}")
            
            if street_name not in street_data:
                street_data[street_name] = {
                    'street_name': street_name,
                    'incidents': [],
                    'crime_types': set(),
                    'incident_count': 0
                }
            
            # Add incident data
            incident_data = {
                'incident_id': incident.get('incident_id', ''),
                'location': location,
                'call_type': incident.get('call_type', ''),
                'title_line': incident.get('title_line', ''),
                'formatted_date': format_date(incident.get('date')),
                'formatted_time': format_time(incident.get('time'))
            }
            
            street_data[street_name]['incidents'].append(incident_data)
            street_data[street_name]['incident_count'] += 1
            
            # Add crime type to set (will be converted to list later)
            call_type = incident.get('call_type', '').strip()
            if call_type:
                street_data[street_name]['crime_types'].add(call_type)
        
        # Convert to final format for frontend
        streets = []
        for street_name, data in street_data.items():
            # Sort incidents by date (most recent first)
            incidents_sorted = sorted(data['incidents'], 
                                    key=lambda x: (x['formatted_date'], x['formatted_time']), 
                                    reverse=True)
            
            street_info = {
                'street_name': street_name,
                'incident_count': data['incident_count'],
                'crime_types': list(data['crime_types']),
                'recent_incidents': incidents_sorted
            }
            streets.append(street_info)
            
        print(f"[DEBUG] Processed {len(streets)} streets with crime data")
        for street in streets[:5]:  # Debug first 5 streets
            print(f"[DEBUG] Street: {street['street_name']}, Incidents: {street['incident_count']}")
        
        return jsonify({"streets": streets})
        
    except Exception as e:
        print(f"[ERROR] Error fetching crime data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/camera-data')
def get_camera_data():
    """Get camera data from cameras collection for intersection mapping"""
    if mongo_client is None:
        print("[WARN] MongoDB not connected", flush=True)
        return jsonify({"cameras": []})
    
    try:
        print(f"[DEBUG] Fetching camera data from MongoDB...")
        
        # Get cameras collection
        cameras_collection = mongo_db.cameras
        
        # Get all cameras from the cameras collection
        cameras = list(cameras_collection.find())
        print(f"[DEBUG] Found {len(cameras)} total cameras")
        
        if not cameras:
            print("[DEBUG] No cameras found in database")
            return jsonify({"cameras": []})
        
        # Process cameras for frontend
        camera_data = []
        
        for camera in cameras:
            primary_road = camera.get('primary_road', '').strip()
            cross_street = camera.get('cross_street_or_notes', '').strip()
            city = camera.get('city', '').strip()
            camera_type = camera.get('camera_type', '').strip()
            
            # Create intersection name, handling missing data gracefully
            if primary_road and cross_street:
                intersection = f"{primary_road} & {cross_street}"
            elif primary_road:
                intersection = primary_road
            elif cross_street:
                intersection = cross_street
            else:
                intersection = f"Camera #{str(camera.get('_id', 'Unknown'))[-4:]}"
                
            print(f"[DEBUG] Processing camera: {camera_type} at {intersection}")
            
            camera_info = {
                'id': str(camera.get('_id', '')),
                'primary_road': primary_road,
                'cross_street': cross_street,
                'city': city,
                'camera_type': camera_type,
                'intersection': intersection
            }
            
            camera_data.append(camera_info)
            
        print(f"[DEBUG] Processed {len(camera_data)} cameras")
        for camera in camera_data[:5]:  # Debug first 5 cameras
            print(f"[DEBUG] Camera: {camera['camera_type']} at {camera['intersection']}")
        
        return jsonify({"cameras": camera_data})
        
    except Exception as e:
        print(f"[ERROR] Error fetching camera data: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------- Main ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
