#!/usr/bin/env python3
"""
Sync MongoDB crime data to CSV file for routing algorithm.
This ensures the safest path algorithm uses current crime data.
"""

import os
import csv
import sys
from datetime import datetime
from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables
load_dotenv()

MONGODB_URI = os.getenv('MONGODB_URI')
MONGO_DB = os.getenv('MONGO_DB', 'luma')
CSV_OUTPUT = 'sources/incidents.csv'

def connect_to_mongodb():
    """Connect to MongoDB and return database."""
    if not MONGODB_URI:
        raise ValueError("MONGODB_URI not found in environment variables")
    
    client = MongoClient(MONGODB_URI)
    db = client[MONGO_DB]
    return db

def export_incidents_to_csv():
    """Export current MongoDB incidents to CSV file for routing algorithm."""
    try:
        print("Connecting to MongoDB...")
        db = connect_to_mongodb()
        
        # Get incidents collection
        incidents_collection = db.incidents
        
        # Fetch all incidents
        incidents = list(incidents_collection.find())
        print(f"Found {len(incidents)} incidents in MongoDB")
        
        if not incidents:
            print("No incidents found in MongoDB")
            return
        
        # Ensure sources directory exists
        os.makedirs('sources', exist_ok=True)
        
        # Write to CSV file
        with open(CSV_OUTPUT, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'incident_id', 'posted_on', 'incident_date', 
                'call_type', 'title_line', 'location', 'city', 'page_url'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for incident in incidents:
                # Map MongoDB fields to CSV format
                row = {
                    'incident_id': incident.get('incident_id', ''),
                    'posted_on': incident.get('posted_on', ''),
                    'incident_date': incident.get('incident_date', ''),
                    'call_type': incident.get('call_type', ''),
                    'title_line': incident.get('title_line', ''),
                    'location': incident.get('location', ''),
                    'city': incident.get('city', ''),
                    'page_url': incident.get('page_url', '')
                }
                writer.writerow(row)
        
        print(f"Successfully exported {len(incidents)} incidents to {CSV_OUTPUT}")
        print("Safest path algorithm will now use current crime data!")
        
    except Exception as e:
        print(f"Error syncing crime data: {e}")
        sys.exit(1)

def export_cameras_to_csv():
    """Export current MongoDB camera data to CSV files for routing algorithm."""
    try:
        db = connect_to_mongodb()
        cameras_collection = db.cameras
        
        # Fetch all cameras
        cameras = list(cameras_collection.find())
        print(f"Found {len(cameras)} cameras in MongoDB")
        
        if not cameras:
            print("No cameras found in MongoDB")
            return
        
        # Separate red light and speed cameras
        red_light_cameras = [c for c in cameras if c.get('camera_type') == 'red_light']
        speed_cameras = [c for c in cameras if c.get('camera_type') == 'speed']
        
        # Export red light cameras
        with open('sources/red_light_cameras.csv', 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['city', 'approach_direction', 'primary_road', 'cross_street_or_notes']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for camera in red_light_cameras:
                row = {
                    'city': camera.get('city', ''),
                    'approach_direction': '',  # Not in MongoDB schema
                    'primary_road': camera.get('primary_road', ''),
                    'cross_street_or_notes': camera.get('cross_street_or_notes', '')
                }
                writer.writerow(row)
        
        # Export speed cameras
        with open('sources/speed_cameras.csv', 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['city', 'approach_direction', 'primary_road', 'cross_street_or_notes']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for camera in speed_cameras:
                row = {
                    'city': camera.get('city', ''),
                    'approach_direction': '',  # Not in MongoDB schema
                    'primary_road': camera.get('primary_road', ''),
                    'cross_street_or_notes': camera.get('cross_street_or_notes', '')
                }
                writer.writerow(row)
        
        print(f"Exported {len(red_light_cameras)} red light cameras and {len(speed_cameras)} speed cameras")
        
    except Exception as e:
        print(f"Error syncing camera data: {e}")

if __name__ == "__main__":
    print("Syncing MongoDB data to CSV files for routing algorithm...")
    print("=" * 60)
    
    export_incidents_to_csv()
    export_cameras_to_csv()
    
    print("=" * 60)
    print("Data sync complete! Routing algorithm now uses current data.")
    print("Run this script regularly to keep routing data current.")
