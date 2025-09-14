#!/usr/bin/env python3
import os
from dotenv import load_dotenv

print("Testing .env file loading...")
print(f"Current working directory: {os.getcwd()}")

# Load .env file
load_dotenv()

# Check if variables are loaded
mongo_uri = os.getenv("MONGO_URI")
mongo_db = os.getenv("MONGO_DB")
mapbox_token = os.getenv("MAPBOX_TOKEN")

print(f"\nEnvironment variables:")
print(f"MONGO_URI: {'✅ SET' if mongo_uri else '❌ NOT SET'}")
print(f"MONGO_DB: {'✅ SET' if mongo_db else '❌ NOT SET'}")
print(f"MAPBOX_TOKEN: {'✅ SET' if mapbox_token else '❌ NOT SET'}")

if mongo_uri:
    # Don't print full URI for security, just show it exists
    print(f"MONGO_URI starts with: {mongo_uri[:20]}...")
if mongo_db:
    print(f"MONGO_DB: {mongo_db}")

# Check if .env file exists
if os.path.exists('.env'):
    print(f"\n✅ .env file exists")
    with open('.env', 'r') as f:
        lines = f.readlines()
    print(f"📄 .env file has {len(lines)} lines")
    
    # Check for common issues
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if line and not line.startswith('#'):
            if '=' not in line:
                print(f"⚠️  Line {i}: Missing '=' in '{line}'")
            elif ' = ' in line:
                print(f"⚠️  Line {i}: Spaces around '=' in '{line}' (should be no spaces)")
else:
    print(f"\n❌ .env file not found")
