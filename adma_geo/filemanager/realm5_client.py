"""
Realm5 API Client

This module provides a client for interacting with the Realm5 API
to fetch device information and weather station observations.

API Documentation: https://app.realmfive.com/api
"""

import requests
from datetime import datetime, date
from typing import List, Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)


class Realm5Client:
    """
    Client for Realm5 API interactions.
    
    Usage:
        client = Realm5Client(api_key="your_api_key")
        devices = client.get_devices()
        observations = client.get_observations(dev_eui="device_eui", start_date=date(2026, 1, 1))
    """
    
    BASE_URL = "https://app.realmfive.com/api"
    
    def __init__(self, api_key: str):
        """
        Initialize the Realm5 client.
        
        Args:
            api_key: The API key for authentication
        """
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-Key': api_key,  # Realm5 uses X-API-Key header for authentication
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
    
    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, 
                      data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make an API request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            data: Request body data
            
        Returns:
            JSON response as dictionary
            
        Raises:
            requests.exceptions.RequestException: If the request fails
        """
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=data,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Realm5 API HTTP error: {e.response.status_code} - {e.response.text}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Realm5 API request error: {e}")
            raise
    
    def get_devices(self) -> List[Dict[str, Any]]:
        """
        Get all devices from Realm5.
        
        Returns:
            List of device dictionaries, each containing at least 'dev_eui'
            
        Example response:
            [
                {"dev_eui": "A84041000181234", "name": "Weather Station 1", ...},
                {"dev_eui": "A84041000181235", "name": "Weather Station 2", ...}
            ]
        """
        logger.info("Fetching devices from Realm5 API...")
        response = self._make_request('GET', '/v2/devices')
        
        # Handle different response formats
        if isinstance(response, list):
            devices = response
        elif isinstance(response, dict):
            # Response is wrapped in 'items' field (Realm5 actual format)
            # Also check for 'data' or 'devices' for compatibility
            devices = response.get('items', response.get('data', response.get('devices', [])))
        else:
            devices = []
        
        logger.info(f"Found {len(devices)} devices from Realm5")
        return devices
    
    def get_observations_by_day(self, dev_eui: str, target_date: date, timeout: int = 60) -> Dict[str, Dict[str, Any]]:
        """
        Get weather station observations for a specific device on a specific day.
        
        Uses the occurred_after and occurred_before parameters to filter by date.
        
        Args:
            dev_eui: The device EUI identifier (numeric format, e.g., 26218821)
            target_date: The specific date to fetch observations for
            timeout: Request timeout in seconds
            
        Returns:
            Dict with timestamps as keys and observation data as values
            
        Example response:
            {
                "2026-01-28T00:30:00.000+0000": {
                    "temperature": -4.67,
                    "humidity": 50.37,
                    "wind_speed": 3.98,
                    ...
                },
                ...
            }
        """
        date_str = target_date.strftime("%Y-%m-%d")
        logger.info(f"Fetching observations for device {dev_eui} on {date_str}")
        
        url = f"{self.BASE_URL}/v2/weather_stations/observations/{dev_eui}"
        params = {
            'occurred_after': date_str,
            'occurred_before': date_str,
        }
        
        try:
            response = self.session.request(
                method='GET',
                url=url,
                params=params,
                timeout=timeout
            )
            response.raise_for_status()
            data = response.json()
            
            # Response is a dict with timestamps as keys
            if isinstance(data, dict):
                logger.info(f"Found {len(data)} observations for device {dev_eui} on {date_str}")
                return data
            else:
                logger.warning(f"Unexpected response format for observations: {type(data)}")
                return {}
                
        except requests.exceptions.HTTPError as e:
            logger.error(f"Realm5 API HTTP error: {e.response.status_code} - {e.response.text}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Realm5 API request error: {e}")
            raise
    
    def get_observations(self, dev_eui: str, start_date: date, 
                         end_date: Optional[date] = None, timeout: int = 60) -> Dict[str, Dict[str, Any]]:
        """
        Get weather station observations for a specific device in a date range.
        
        Uses the occurred_after and occurred_before parameters to filter by date.
        
        Args:
            dev_eui: The device EUI identifier (numeric format)
            start_date: Start date for observations (inclusive)
            end_date: End date for observations (inclusive, defaults to today)
            timeout: Request timeout in seconds
            
        Returns:
            Dict with timestamps as keys and observation data as values
        """
        if end_date is None:
            end_date = date.today()
        
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        
        logger.info(f"Fetching observations for device {dev_eui} from {start_str} to {end_str}")
        
        url = f"{self.BASE_URL}/v2/weather_stations/observations/{dev_eui}"
        params = {
            'occurred_after': start_str,
            'occurred_before': end_str,
        }
        
        try:
            response = self.session.request(
                method='GET',
                url=url,
                params=params,
                timeout=timeout
            )
            response.raise_for_status()
            data = response.json()
            
            # Response is a dict with timestamps as keys
            if isinstance(data, dict):
                logger.info(f"Found {len(data)} observations for device {dev_eui} in date range")
                return data
            else:
                logger.warning(f"Unexpected response format for observations: {type(data)}")
                return {}
                
        except requests.exceptions.HTTPError as e:
            logger.error(f"Realm5 API HTTP error: {e.response.status_code} - {e.response.text}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Realm5 API request error: {e}")
            raise
    
    def get_markers(self, organization_id: int, page: int = 1, per_page: int = 50) -> Dict[str, Any]:
        """
        Get markers (instrument installation points) for an organization.
        
        Markers contain location information and link to installed instruments/devices.
        
        Args:
            organization_id: The organization ID
            page: Page number (starting from 1)
            per_page: Number of results per page (1-200, default 50)
            
        Returns:
            Dict containing 'summary' and 'items' fields
            
        Example response:
            {
                "summary": {"total": 10, "page": 1, "per_page": 50, "pages": 1},
                "items": [
                    {
                        "id": 123,
                        "organizationId": "456",
                        "name": "Field Station 1",
                        "location": "40.1234, -96.5678",
                        "currentInstalledInstrument": {
                            "installable_type": "weather_station",
                            "installable_id": "26218821",
                            ...
                        }
                    },
                    ...
                ]
            }
        """
        logger.info(f"Fetching markers for organization {organization_id}...")
        
        params = {
            'page': page,
            'per_page': per_page,
        }
        
        response = self._make_request('GET', f'/v2/organizations/{organization_id}/markers', params=params)
        
        # Log the response for debugging
        if isinstance(response, dict):
            summary = response.get('summary', {})
            items = response.get('items', [])
            logger.info(f"Found {summary.get('total', len(items))} markers for organization {organization_id}")
        
        return response
    
    def get_all_markers(self, organization_id: int) -> List[Dict[str, Any]]:
        """
        Get all markers for an organization (handles pagination).
        
        Args:
            organization_id: The organization ID
            
        Returns:
            List of all marker dictionaries
        """
        all_markers = []
        page = 1
        per_page = 200  # Max allowed
        
        while True:
            response = self.get_markers(organization_id, page=page, per_page=per_page)
            
            if isinstance(response, dict):
                items = response.get('items', [])
                if isinstance(items, list):
                    all_markers.extend(items)
                elif isinstance(items, dict):
                    # Single item returned as dict
                    all_markers.append(items)
                
                summary = response.get('summary', {})
                total_pages = summary.get('pages', 1)
                
                if page >= total_pages:
                    break
                page += 1
            else:
                break
        
        logger.info(f"Fetched total of {len(all_markers)} markers for organization {organization_id}")
        return all_markers
    
    def test_connection(self) -> bool:
        """
        Test the API connection and authentication.
        
        Returns:
            True if connection is successful, False otherwise
        """
        try:
            self.get_devices()
            return True
        except Exception as e:
            logger.error(f"Realm5 API connection test failed: {e}")
            return False


# Default client instance (will be initialized with settings)
def get_realm5_client() -> Realm5Client:
    """
    Get a Realm5 client instance using settings from Django settings.
    
    Returns:
        Configured Realm5Client instance
    """
    from django.conf import settings
    
    api_key = getattr(settings, 'REALM5_API_KEY', None)
    if not api_key:
        raise ValueError("REALM5_API_KEY not configured in Django settings")
    
    return Realm5Client(api_key=api_key)
