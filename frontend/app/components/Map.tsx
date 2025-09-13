"use client";
import React, { useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import MapboxGeocoder from '@mapbox/mapbox-gl-geocoder';

import 'mapbox-gl/dist/mapbox-gl.css';
import '@mapbox/mapbox-gl-geocoder/dist/mapbox-gl-geocoder.css';

const Map: React.FC = () => {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);

  const mapboxToken = process.env.NEXT_PUBLIC_MAPBOX_ACCESS_TOKEN;

  useEffect(() => {
    if (mapboxToken) {
      mapboxgl.accessToken = mapboxToken;
    } else {
      console.error("Mapbox token is not defined");
      return;
    }

    if (!mapContainerRef.current) return;

    mapRef.current = new mapboxgl.Map({
      container: mapContainerRef.current as HTMLElement,
      style: 'mapbox://styles/mapbox/standard',
      center: [-80.5204, 43.4643],
      zoom: 13
    });

    mapRef.current.addControl(
      new MapboxGeocoder({
        accessToken: mapboxToken,
        mapboxgl: mapboxgl as unknown as typeof import('mapbox-gl')
      })
    );

    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
      }
    };
  }, [mapboxToken]);

  return <div id="map-container" ref={mapContainerRef} style={{ height: '100vh', width: '100vw' }} />;
};

export default Map;