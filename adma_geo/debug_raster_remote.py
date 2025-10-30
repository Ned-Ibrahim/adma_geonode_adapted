#!/usr/bin/env python3

"""
Debug script to test raster (TIFF) layer access on remote server.
This script helps identify differences between local and remote GeoServer raster serving.
"""

import requests
from requests.auth import HTTPBasicAuth
import json

def test_raster_layer_access():
    """Test various aspects of raster layer access"""
    
    # Configuration - update these for your remote server
    REMOTE_GEOSERVER_URL = "https://adma.unl.edu/geoserver"
    LOCAL_GEOSERVER_URL = "http://localhost/geoserver"
    
    # Actual TIFF layer from the user's issue
    WORKSPACE = "adma_geo"
    LAYER_NAME = "adma_geo_2_9f2741_21_E1801_AI03_rgba_g_a9428b02"
    
    # GeoServer admin credentials
    USERNAME = "admin"
    PASSWORD = "geoserver123"
    
    auth = HTTPBasicAuth(USERNAME, PASSWORD)
    
    print("🔍 Testing Raster Layer Access on Remote vs Local")
    print("=" * 60)
    
    # Test 1: Check if layer exists
    print("\n1. Testing Layer Existence:")
    for name, base_url in [("Remote", REMOTE_GEOSERVER_URL), ("Local", LOCAL_GEOSERVER_URL)]:
        try:
            layer_url = f"{base_url}/rest/workspaces/{WORKSPACE}/layers/{LAYER_NAME}.json"
            response = requests.get(layer_url, auth=auth, timeout=10)
            print(f"   {name}: Status {response.status_code}")
            if response.status_code == 200:
                layer_info = response.json()
                print(f"   {name}: Layer type: {layer_info.get('layer', {}).get('type', 'unknown')}")
        except Exception as e:
            print(f"   {name}: Error - {e}")
    
    # Test 2: Check coverage store (raster-specific)
    print("\n2. Testing Coverage Store:")
    for name, base_url in [("Remote", REMOTE_GEOSERVER_URL), ("Local", LOCAL_GEOSERVER_URL)]:
        try:
            store_url = f"{base_url}/rest/workspaces/{WORKSPACE}/coveragestores.json"
            response = requests.get(store_url, auth=auth, timeout=10)
            print(f"   {name}: Coverage stores status {response.status_code}")
            if response.status_code == 200:
                stores = response.json().get('coverageStores', {}).get('coverageStore', [])
                if isinstance(stores, dict):
                    stores = [stores]
                print(f"   {name}: Found {len(stores)} coverage stores")
                for store in stores[:3]:  # Show first 3
                    print(f"     - {store.get('name', 'unknown')}")
        except Exception as e:
            print(f"   {name}: Error - {e}")
    
    # Test 3: Test WMS GetCapabilities
    print("\n3. Testing WMS GetCapabilities:")
    for name, base_url in [("Remote", REMOTE_GEOSERVER_URL), ("Local", LOCAL_GEOSERVER_URL)]:
        try:
            wms_url = f"{base_url}/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
            response = requests.get(wms_url, timeout=10)
            print(f"   {name}: GetCapabilities status {response.status_code}")
            if response.status_code == 200:
                # Check if our layer is listed
                if LAYER_NAME in response.text:
                    print(f"   {name}: ✅ Layer found in capabilities")
                else:
                    print(f"   {name}: ❌ Layer NOT found in capabilities")
        except Exception as e:
            print(f"   {name}: Error - {e}")
    
    # Test 4: Test direct WMS tile request
    print("\n4. Testing Direct WMS Tile Request:")
    wms_params = {
        'SERVICE': 'WMS',
        'VERSION': '1.1.1',
        'REQUEST': 'GetMap',
        'LAYERS': f'{WORKSPACE}:{LAYER_NAME}',
        'STYLES': '',
        'FORMAT': 'image/png',
        'TRANSPARENT': 'true',
        'SRS': 'EPSG:3857',
        'BBOX': '-11000000,4900000,-11100000,5000000',  # Adjust for your area
        'WIDTH': '256',
        'HEIGHT': '256'
    }
    
    for name, base_url in [("Remote", REMOTE_GEOSERVER_URL), ("Local", LOCAL_GEOSERVER_URL)]:
        try:
            wms_url = f"{base_url}/wms"
            response = requests.get(wms_url, params=wms_params, timeout=15)
            print(f"   {name}: WMS GetMap status {response.status_code}")
            print(f"   {name}: Content-Type: {response.headers.get('Content-Type', 'unknown')}")
            print(f"   {name}: Content-Length: {len(response.content)} bytes")
            
            # Check for error messages in response
            if 'xml' in response.headers.get('Content-Type', '').lower():
                if 'ServiceException' in response.text or 'Error' in response.text:
                    print(f"   {name}: ❌ WMS Error detected")
                    # Print first 200 chars of error
                    print(f"   {name}: Error snippet: {response.text[:200]}...")
                else:
                    print(f"   {name}: ✅ XML response (likely success)")
            elif 'image' in response.headers.get('Content-Type', '').lower():
                print(f"   {name}: ✅ Image response (success)")
            else:
                print(f"   {name}: ⚠️  Unexpected content type")
                
        except Exception as e:
            print(f"   {name}: Error - {e}")
    
    # Test 5: Check CORS headers
    print("\n5. Testing CORS Headers:")
    for name, base_url in [("Remote", REMOTE_GEOSERVER_URL), ("Local", LOCAL_GEOSERVER_URL)]:
        try:
            wms_url = f"{base_url}/wms"
            headers = {'Origin': 'https://adma.unl.edu'}  # Simulate browser request
            response = requests.get(wms_url, params={'SERVICE': 'WMS', 'REQUEST': 'GetCapabilities'}, 
                                  headers=headers, timeout=10)
            cors_header = response.headers.get('Access-Control-Allow-Origin', 'Not set')
            print(f"   {name}: CORS Allow-Origin: {cors_header}")
        except Exception as e:
            print(f"   {name}: Error - {e}")
    
    print("\n" + "=" * 60)
    print("🔧 Debugging Tips:")
    print("1. If layer doesn't exist on remote: Re-publish the TIFF file")
    print("2. If WMS GetMap fails: Check GeoServer logs for detailed errors")
    print("3. If CORS issues: Configure GeoServer CORS or use proxy")
    print("4. If projection issues: Check if raster CRS matches request CRS")
    print("5. Check browser Network tab for actual error responses")

if __name__ == "__main__":
    test_raster_layer_access()
