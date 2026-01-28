import os
import warnings
from typing import Dict, Tuple

import geopandas as gpd

warnings.filterwarnings("ignore", category=UserWarning)


def process_shape_to_json(input_path: str, output_dir: str) -> Tuple[bool, str, Dict]:
    """
    Convert a shapefile to GeoJSON format.
    
    Args:
        input_path: Full path to the input shapefile (.shp)
        output_dir: Directory where output GeoJSON will be saved
        
    Returns:
        Tuple of (success: bool, message: str, output_files: Dict)
        output_files contains key: 'geojson'
    """
    output_files = {}
    
    try:
        # Validate input file
        if not input_path.lower().endswith(".shp"):
            return False, "Input must be a .shp file", {}

        if not os.path.isfile(input_path):
            return False, "Shapefile not found", {}

        os.makedirs(output_dir, exist_ok=True)

        # Read the shapefile
        gdf = gpd.read_file(input_path)
        
        if gdf.empty:
            return False, "Shapefile is empty", {}

        # Build output GeoJSON path
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(output_dir, f"{base_name}.geojson")

        # Convert to WGS84 (EPSG:4326) for GeoJSON compatibility
        if gdf.crs and gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        # Write GeoJSON
        gdf.to_file(output_path, driver="GeoJSON")

        output_files['geojson'] = output_path

        # Build success message
        geom_type = gdf.geometry.geom_type.unique().tolist()
        feature_count = len(gdf)
        message = (
            f"Conversion complete. "
            f"Converted {feature_count} features ({', '.join(geom_type)}) to GeoJSON."
        )

        return True, message, output_files

    except Exception as e:
        return False, f"Conversion error: {str(e)}", output_files
