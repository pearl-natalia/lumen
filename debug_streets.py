#!/usr/bin/env python3
import os
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# MongoDB connection
mongo_uri = os.getenv("MONGO_URI")
mongo_db = os.getenv("MONGO_DB")

if not mongo_uri or not mongo_db:
    print("❌ MONGO_URI or MONGO_DB not set in .env file")
    exit(1)

try:
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[mongo_db]
    incidents_collection = db.incidents
    
    # Test connection
    client.admin.command('ping')
    print("✅ Connected to MongoDB successfully!")
    
    # Get all incidents
    incidents = list(incidents_collection.find({}).limit(2000))
    print(f"📊 Found {len(incidents)} incidents in MongoDB")
    
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
    
    print(f"\n🏘️  Found {len(street_incidents)} unique streets")
    print("\n📍 Sample raw locations from MongoDB:")
    for i, loc in enumerate(raw_locations[:10], 1):
        print(f"  {i}. {loc}")
    
    print(f"\n🚨 Top 20 streets by incident count:")
    for i, (street, count) in enumerate(sorted_streets[:20], 1):
        print(f"  {i:2d}. {street:<30} ({count} incidents)")
    
    if len(sorted_streets) > 20:
        print(f"\n... and {len(sorted_streets) - 20} more streets")
    
    print(f"\n📋 All {len(street_incidents)} street names:")
    all_streets = list(street_incidents.keys())
    for i, street in enumerate(all_streets, 1):
        print(f"  {i:3d}. {street}")

except Exception as e:
    print(f"❌ Error connecting to MongoDB: {e}")
