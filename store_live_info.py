#!/usr/bin/env python3
import os, csv
from datetime import datetime
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError
from dotenv import load_dotenv

def store_incidents(csv_path: str = "sources/incidents.csv",
                        uri: str | None = None,
                        db_name: str = "public_safety",
                        coll_name: str = "incidents"):
    """
    Load WRPS incidents from a CSV into MongoDB.
    Keeps only: incident_id, incident_date, call_type, title_line, location.
    Prevents duplicates via unique index on incident_id and upserts.

    Returns: (inserted_count, modified_count)
    """
    load_dotenv()
    uri = uri or os.getenv("MONGODB_URI", "mongodb://localhost:27017")

    # --- inline parser fixes the "no year" warning by explicitly setting a year ---
    def parse_wrps_datetime(s: str, year: int) -> datetime | None:
        s = (s or "").strip()
        if not s:
            return None
        # Format like: "Monday August 18, 1pm"
        dt = datetime.strptime(s, "%A %B %d, %I%p")
        return dt.replace(year=year)

    client = MongoClient(uri)
    coll = client[db_name][coll_name]

    # Ensure unique index on incident_id
    coll.create_index([("incident_id", 1)], unique=True, name="incident_id_unique")

    # Use the *current* year (or adjust if you later add a year column)
    default_year = datetime.now().year

    ops = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse incident_date with an explicit year to avoid deprecation warnings
            inc_dt = parse_wrps_datetime(row.get("incident_date", ""), default_year)

            raw_title = row.get("title_line", "")
            # split once on " - "
            clean_title = raw_title.split(" - ", 1)[-1].strip() if " - " in raw_title else raw_title

            doc = {
                "incident_id": row.get("incident_id"),
                "incident_date": inc_dt,
                "call_type": row.get("call_type"),
                "title_line": clean_title,
                "location": row.get("location"),
            }

            ops.append(UpdateOne(
                {"incident_id": doc["incident_id"]},
                {"$set": doc},
                upsert=True
            ))

    inserted = modified = 0
    if not ops:
        print("No rows found in CSV.")
        return (0, 0)

    try:
        res = coll.bulk_write(ops, ordered=False)
        # bulk_write doesn't always populate inserted_count; count upserts explicitly
        inserted = getattr(res, "upserted_count", 0) or len(getattr(res, "upserted_ids", {}) or {})
        modified = getattr(res, "modified_count", 0)
    except BulkWriteError as bwe:
        det = bwe.details or {}
        inserted = det.get("nUpserted", 0)
        modified = det.get("nModified", 0)
        dups = sum(1 for e in det.get("writeErrors", []) if e.get("code") == 11000)
        if dups:
            print(f"Skipped {dups} duplicate(s) (incident_id).")
    print(f"Inserted: {inserted}, Modified: {modified}")
    return (inserted, modified)

def store_cameras():
    """
    Load red-light and speed cameras into MongoDB Atlas.
    Stores: { camera_type, city, primary_road, cross_street_or_notes }
    Dedupe key: (camera_type, city, primary_road, cross_street_or_notes)
    """
    # --- Hardcoded settings ---
    red_csv = "sources/red_light_cameras.csv"
    speed_csv = "sources/speed_cameras.csv"
    db_name = "public_safety"
    coll_name = "cameras"

    # Load Atlas connection string from .env
    load_dotenv()
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("⚠️ Please set MONGODB_URI in .env")

    client = MongoClient(uri)
    coll = client[db_name][coll_name]

    # Ensure unique compound index (no duplicates)
    coll.create_index(
        [
            ("camera_type", 1),
            ("city", 1),
            ("primary_road", 1),
            ("cross_street_or_notes", 1),
        ],
        unique=True,
        name="camera_unique"
    )

    def _norm(s: str | None) -> str:
        return (s or "").strip()

    def _load(csv_path: str, camera_type: str) -> tuple[int, int]:
        if not os.path.exists(csv_path):
            print(f"⚠️ CSV not found: {csv_path}")
            return (0, 0)
        ops = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                doc = {
                    "camera_type": camera_type,
                    "city": _norm(row.get("city")),
                    "primary_road": _norm(row.get("primary_road")),
                    "cross_street_or_notes": _norm(row.get("cross_street_or_notes")),
                }
                if not (doc["city"] and doc["primary_road"] and doc["cross_street_or_notes"]):
                    continue
                filt = {
                    "camera_type": doc["camera_type"],
                    "city": doc["city"],
                    "primary_road": doc["primary_road"],
                    "cross_street_or_notes": doc["cross_street_or_notes"],
                }
                ops.append(UpdateOne(filt, {"$set": doc}, upsert=True))

        if not ops:
            return (0, 0)
        res = coll.bulk_write(ops, ordered=False)
        inserted = getattr(res, "upserted_count", 0) or len(getattr(res, "upserted_ids", {}) or {})
        modified = getattr(res, "modified_count", 0)
        return (inserted, modified)

    ins_rl, mod_rl = _load(red_csv, "red_light")
    ins_sp, mod_sp = _load(speed_csv, "speed")

    print(f"[red_light] Inserted: {ins_rl}, Modified: {mod_rl}")
    print(f"[speed]     Inserted: {ins_sp}, Modified: {mod_sp}")
    print(f"[total]     Inserted: {ins_rl + ins_sp}, Modified: {mod_rl + mod_sp}")

store_cameras()
store_incidents()