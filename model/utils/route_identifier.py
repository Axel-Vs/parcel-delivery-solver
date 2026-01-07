"""
Route Identifier Module
=======================
Centralized route identification and naming system for the parcel delivery optimizer.

This module provides a consistent way to:
- Generate route display names
- Extract route numbers from various formats
- Serialize route metadata for JavaScript/frontend
- Parse route identifiers in a format-independent way

Usage:
    # Python side
    route_id = RouteIdentifier(route_number=28, vendor_name="US Plastics", city="Miami", zip_code="33101")
    display_name = route_id.get_display_name()  # "Route 28: US Plastics (Miami, 33101)"
    geojson_props = route_id.to_geojson_properties()  # {'ROUTE_ID': 28, 'ROUTE_NAME': '...'}
    
    # Parse from string
    parsed = RouteIdentifier.from_string("Route 28: US Plastics (Miami, 33101)")
    
    # JavaScript side (generated helpers)
    const routeNum = extractRouteNumber(layer.feature.properties);  // Returns 28
"""

import re
import logging
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict

# Set up logging
logger = logging.getLogger(__name__)


@dataclass
class RouteIdentifier:
    """
    Immutable route identifier with metadata.
    
    Attributes:
        route_number: Unique route number (1-indexed)
        vendor_name: Primary vendor name for this route (optional)
        city: Primary vendor city (optional)
        zip_code: Primary vendor ZIP/postal code (optional)
        vehicle_id: Vehicle ID assigned to this route (optional)
    """
    route_number: int
    vendor_name: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    vehicle_id: Optional[int] = None
    
    # Display format constants - change these to update formatting everywhere
    DISPLAY_FORMAT = "Route {number}: {vendor} ({location})"
    SHORT_FORMAT = "Route {number}"
    VENDOR_MAX_LENGTH = 50  # Truncate long vendor names
    
    def get_display_name(self, truncate: bool = True) -> str:
        """
        Get human-readable route display name.
        
        Args:
            truncate: Whether to truncate long vendor names
            
        Returns:
            Formatted route name string
        """
        logger.debug(f"🏷️ [RouteIdentifier] get_display_name called for route {self.route_number}")
        logger.debug(f"   vendor_name={self.vendor_name}, city={self.city}, zip={self.zip_code}")
        
        if not self.vendor_name:
            result = self.SHORT_FORMAT.format(number=self.route_number)
            logger.debug(f"   ➡️ No vendor, returning short format: {result}")
            return result
        
        vendor = self.vendor_name
        if truncate and len(vendor) > self.VENDOR_MAX_LENGTH:
            original = vendor
            vendor = vendor[:self.VENDOR_MAX_LENGTH - 3] + '...'
            logger.debug(f"   ✂️ Truncated vendor: '{original}' → '{vendor}'")
        
        # Build location string
        location_parts = []
        if self.city:
            location_parts.append(str(self.city).strip())
        if self.zip_code:
            # Clean ZIP code
            zip_str = str(self.zip_code)
            if '.' in zip_str:
                zip_str = zip_str.split('.')[0]
            location_parts.append(zip_str.strip())
        
        if location_parts:
            location = ', '.join(location_parts)
            result = self.DISPLAY_FORMAT.format(
                number=self.route_number,
                vendor=vendor,
                location=location
            )
            logger.debug(f"   ➡️ Full format: {result}")
            return result
        else:
            result = f"{self.SHORT_FORMAT.format(number=self.route_number)}: {vendor}"
            logger.debug(f"   ➡️ Vendor only format: {result}")
            return result
    
    def get_short_name(self) -> str:
        """Get short route name (just the number)."""
        return self.SHORT_FORMAT.format(number=self.route_number)
    
    def to_geojson_properties(self, distance_km: Optional[float] = None, 
                             stops: Optional[int] = None) -> Dict[str, Any]:
        """
        Convert to GeoJSON properties dictionary.
        
        Args:
            distance_km: Route distance in kilometers
            stops: Number of stops on route
            
        Returns:
            Dictionary suitable for GeoJSON feature properties
        """
        logger.debug(f"🗺️ [RouteIdentifier] to_geojson_properties for route {self.route_number}")
        
        props = {
            'ROUTE_ID': self.route_number,  # Numeric ID for filtering
            'ROUTE_NAME': self.get_display_name(),  # Display name for UI
            'ROUTE_SHORT': self.get_short_name(),  # Short version
        }
        
        if distance_km is not None:
            props['DISTANCE'] = f"{round(distance_km, 2)} km"
            props['DISTANCE_KM'] = round(distance_km, 2)
        
        if stops is not None:
            props['STOPS'] = stops
        
        if self.vehicle_id is not None:
            props['VEHICLE'] = f"Vehicle {self.vehicle_id}"
            props['VEHICLE_ID'] = self.vehicle_id
        
        logger.debug(f"   ➡️ Generated properties: {props}")
        return props
    
    @classmethod
    def from_string(cls, route_string: str) -> Optional['RouteIdentifier']:
        """
        Parse route identifier from string.
        
        Args:
            route_string: String in format "Route X: Vendor (City, ZIP)" or "Route X"
            
        Returns:
            RouteIdentifier instance or None if parsing fails
        """
        logger.debug(f"🔍 [RouteIdentifier] from_string parsing: '{route_string}'")
        
        # Try full format: "Route 28: US Plastics (Miami, 33101)"
        full_pattern = r'^Route (\d+):\s*(.+?)\s*\(([^,]+)(?:,\s*(.+))?\)$'
        match = re.match(full_pattern, route_string.strip())
        
        if match:
            route_num = int(match.group(1))
            vendor = match.group(2).strip()
            city = match.group(3).strip()
            zip_code = match.group(4).strip() if match.group(4) else None
            
            logger.debug(f"   ✅ Full format matched: route={route_num}, vendor={vendor}, city={city}, zip={zip_code}")
            
            return cls(
                route_number=route_num,
                vendor_name=vendor,
                city=city,
                zip_code=zip_code
            )
        
        # Try short format: "Route 28"
        short_pattern = r'^Route (\d+)$'
        match = re.match(short_pattern, route_string.strip())
        
        if match:
            route_num = int(match.group(1))
            logger.debug(f"   ✅ Short format matched: route={route_num}")
            return cls(route_number=route_num)
        
        logger.warning(f"   ❌ Could not parse route string: '{route_string}'")
        return None
    
    @classmethod
    def from_properties(cls, properties: Dict[str, Any]) -> Optional['RouteIdentifier']:
        """
        Extract route identifier from GeoJSON properties.
        
        Args:
            properties: GeoJSON feature properties dictionary
            
        Returns:
            RouteIdentifier instance or None if not found
        """
        logger.debug(f"🗺️ [RouteIdentifier] from_properties: {list(properties.keys())}")
        
        # Prefer direct ROUTE_ID if available
        if 'ROUTE_ID' in properties:
            route_num = properties['ROUTE_ID']
            logger.debug(f"   ✅ Found ROUTE_ID={route_num}")
            return cls(
                route_number=route_num,
                vehicle_id=properties.get('VEHICLE_ID')
            )
        
        # Fall back to parsing ROUTE_NAME or ROUTE
        route_str = properties.get('ROUTE_NAME') or properties.get('ROUTE')
        if route_str:
            logger.debug(f"   ⚠️ No ROUTE_ID, falling back to parsing: {route_str}")
            return cls.from_string(str(route_str))
        
        logger.warning(f"   ❌ No route identifier found in properties")
        return None
    
    def __eq__(self, other):
        """Compare routes by number (primary key)."""
        if not isinstance(other, RouteIdentifier):
            return False
        return self.route_number == other.route_number
    
    def __hash__(self):
        """Hash by route number for use in sets/dicts."""
        return hash(self.route_number)
    
    @staticmethod
    def generate_javascript_helpers() -> str:
        """
        Generate JavaScript helper functions for route identification.
        
        Returns:
            JavaScript code string with helper functions
        """
        return """
        // Auto-generated JavaScript helpers for RouteIdentifier
        // Generated by model/utils/route_identifier.py
        
        /**
         * Extract numeric route ID from GeoJSON properties.
         * @param {Object} properties - GeoJSON feature properties
         * @returns {number|null} Route number or null if not found
         */
        function extractRouteNumber(properties) {
            if (!properties) return null;
            
            // Prefer direct ROUTE_ID
            if (properties.ROUTE_ID !== undefined) {
                return properties.ROUTE_ID;
            }
            
            // Fall back to parsing ROUTE_NAME or ROUTE
            var routeStr = properties.ROUTE_NAME || properties.ROUTE;
            if (routeStr) {
                var match = routeStr.match(/^Route (\\d+)/);
                if (match) {
                    return parseInt(match[1], 10);
                }
            }
            
            return null;
        }
        
        /**
         * Extract vehicle ID from properties.
         * @param {Object} properties - GeoJSON feature properties
         * @returns {number|null} Vehicle ID or null
         */
        function extractVehicleId(properties) {
            if (!properties) return null;
            
            if (properties.VEHICLE_ID !== undefined) {
                return properties.VEHICLE_ID;
            }
            
            // Parse from VEHICLE string
            var vehicleStr = properties.VEHICLE;
            if (vehicleStr) {
                var match = vehicleStr.match(/Vehicle (\\d+)/);
                if (match) {
                    return parseInt(match[1], 10);
                }
            }
            
            return null;
        }
        
        /**
         * Get display name from properties.
         * @param {Object} properties - GeoJSON feature properties
         * @returns {string} Display name
         */
        function getRouteDisplayName(properties) {
            if (!properties) return 'Unknown Route';
            
            if (properties.ROUTE_NAME) {
                return properties.ROUTE_NAME;
            }
            
            if (properties.ROUTE) {
                return properties.ROUTE;
            }
            
            var routeNum = extractRouteNumber(properties);
            return routeNum ? 'Route ' + routeNum : 'Unknown Route';
        }
        
        /**
         * Get short route name from properties.
         * @param {Object} properties - GeoJSON feature properties
         * @returns {string} Short name like "Route 28"
         */
        function getRouteShortName(properties) {
            if (!properties) return 'Unknown';
            
            if (properties.ROUTE_SHORT) {
                return properties.ROUTE_SHORT;
            }
            
            var routeNum = extractRouteNumber(properties);
            return routeNum ? 'Route ' + routeNum : 'Unknown';
        }
        
        // Expose to window for global access
        window.extractRouteNumber = extractRouteNumber;
        window.extractVehicleId = extractVehicleId;
        window.getRouteDisplayName = getRouteDisplayName;
        window.getRouteShortName = getRouteShortName;
        
        console.log('✅ RouteIdentifier JavaScript helpers loaded');
        """
