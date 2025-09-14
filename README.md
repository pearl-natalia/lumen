# Lumen - Safe Walking at Night

**Lumen** is an intelligent route optimization application that helps users find the safest paths for nighttime walking by analyzing real-time safety data, street lighting, and historical incident reports.

## Features

### Safety-First Routing
- **Safest Route Algorithm**: Prioritizes well-lit streets, populated areas, and low-crime zones
- **Real-time Safety Data**: Integrates live incident reports and safety metrics
- **Historical Analysis**: Uses machine learning to predict safer routes based on past data
- **Alternative Routes**: Provides multiple route options with safety scores

### Advanced Mapping
- **Mapbox Integration**: High-quality interactive maps with custom styling
- **Geocoding Support**: Convert addresses to coordinates seamlessly
- **Route Visualization**: Clear visualization of safest vs. shortest paths
- **Real-time Updates**: Dynamic route adjustments based on current conditions

### Data
- **MongoDB Integration**: Stores and analyzes safety incident data
- **Machine Learning Models**: Predicts route safety using scikit-learn
- **Geospatial Analysis**: Advanced geographic data processing with GeoPandas
- **Live Data Feeds**: Real-time incident reporting and updates

## Architecture

### Backend (Python/Flask)
- **Flask Web Framework**: RESTful API for route calculations
- **MongoDB Atlas**: Cloud database for incident data storage
- **OSMnx**: OpenStreetMap data processing and network analysis
- **GeoPandas**: Geospatial data manipulation and analysis
- **Scikit-learn**: Machine learning for safety prediction models

### Frontend (Next.js/React)
- **Modern React Interface**: Clean, responsive user experience
- **VAPI Voice Integration**: AI-powered voice assistant for hands-free navigation
- **Mapbox GL**: Interactive mapping with custom safety overlays
- **Real-time Updates**: Live route optimization and safety alerts

## Installation

### Prerequisites
- Python 3.8+
- Node.js 16+
- MongoDB Atlas account
- Mapbox API token

### Backend Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/pearl-natalia/lumen.git
   cd lumen
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Configuration**
   Create a `.env` file in the root directory:
   ```env
   MAPBOX_TOKEN=your_mapbox_token_here
   MONGODB_URI=your_mongodb_connection_string
   MONGO_DB=your_database_name
   FLASK_ENV=development
   ```

4. **Run the Flask backend**
   ```bash
   python app.py
   ```

### Frontend Setup

1. **Navigate to frontend directory**
   ```bash
   cd frontend
   ```

2. **Install Node.js dependencies**
   ```bash
   npm install
   ```

3. **Configure VAPI (Optional)**
   Create `.env.local` in the frontend directory:
   ```env
   NEXT_PUBLIC_VAPI_PUBLIC_KEY=your_vapi_public_key
   VAPI_PRIVATE_KEY=your_vapi_private_key
   ```

4. **Start the development server**
   ```bash
   npm run dev
   ```

## 📖 Usage

### Web Interface
1. **Open your browser** to `http://localhost:3000`
2. **Enter your destination** in the search bar
3. **Choose route type**: Safest route or shortest route
4. **View the route** with safety metrics and alternatives
5. **Use voice commands** for hands-free navigation (if VAPI is configured)

### API Endpoints

#### Calculate Safest Route
```http
POST /api/safest-route
Content-Type: application/json

{
  "start": "123 Main St, City, State",
  "end": "456 Oak Ave, City, State"
}
```

#### Calculate Shortest Route
```http
POST /api/shortest-route
Content-Type: application/json

{
  "start": "123 Main St, City, State", 
  "end": "456 Oak Ave, City, State"
}
```

#### Get Live Safety Data
```http
GET /api/live-info?lat=40.7128&lon=-74.0060
```

## Configuration

### Mapbox Setup
1. Sign up at [Mapbox](https://mapbox.com)
2. Create a new access token
3. Add to your `.env` file as `MAPBOX_TOKEN`

### MongoDB Setup
1. Create a MongoDB Atlas account
2. Create a new cluster
3. Get your connection string
4. Add to your `.env` file as `MONGODB_URI`

### VAPI Voice Assistant (Optional)
1. Sign up at [VAPI](https://vapi.ai)
2. Get your API keys
3. Add to frontend `.env.local` file

## How It Works

### Safety Scoring Algorithm
1. **Street Lighting Analysis**: Evaluates illumination levels along routes
2. **Population Density**: Considers foot traffic and visibility
3. **Historical Incidents**: Analyzes past safety reports in the area
4. **Real-time Data**: Incorporates current incident reports
5. **Machine Learning**: Uses trained models to predict route safety

### Route Optimization
- **Network Analysis**: Uses OSMnx to analyze street networks
- **Multi-criteria Optimization**: Balances safety, distance, and time
- **Alternative Generation**: Provides multiple route options
- **Dynamic Updates**: Adjusts routes based on real-time conditions

## Data Sources

- **OpenStreetMap**: Street network and infrastructure data
- **Real-time Incident Feeds**: Live safety and crime data
- **Street Lighting Data**: Illumination and visibility metrics
- **Historical Crime Reports**: Past incident analysis
- **Population Density**: Foot traffic and activity levels

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- **OpenStreetMap** contributors for street network data
- **Mapbox** for mapping services
- **VAPI** for voice AI capabilities
- **MongoDB Atlas** for cloud database services

---
