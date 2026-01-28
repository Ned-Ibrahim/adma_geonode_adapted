import os
import warnings
from typing import Dict, Tuple, Optional

import geopandas as gpd
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)


def process_si_tool(
    treatment: str,
    imagery: str,
    si_column_name: str,
    field_column: str,
    buffer_sectors_path: str,
    ndre_path: Optional[str] = None,
    csv_path: Optional[str] = None,
    indicator_block_path: Optional[str] = None,
    nir_tiff_path: Optional[str] = None,
    rededge_tiff_path: Optional[str] = None,
    output_dir: Optional[str] = None
) -> Tuple[bool, str, Dict]:
    """
    Process SI (Stress Index) calculation based on treatment and imagery type.
    
    Currently implements: STANDARD + UAV workflow
    Other workflows (SBF, SATELLITE) are placeholders for future implementation.
    
    Args:
        treatment: Treatment methodology - 'STANDARD' or 'SBF'
        imagery: Imagery type - 'UAV' or 'SATELLITE'
        si_column_name: Column name for SI values (e.g., 'SI_08_01')
        field_column: Field column name for grouping (e.g., 'Plot_Numbe', 'Field_ID')
        buffer_sectors_path: Path to buffer sectors shapefile
        ndre_path: Path to NDRE shapefile (required for UAV)
        csv_path: Path to CSV file to update with SI values
        indicator_block_path: Path to indicator block shapefile (required for SBF)
        nir_tiff_path: Path to NIR TIFF file (required for SATELLITE)
        rededge_tiff_path: Path to RedEdge TIFF file (required for SATELLITE)
        output_dir: Directory for output files
        
    Returns:
        Tuple of (success: bool, message: str, output_files: Dict)
    """
    output_files = {}
    
    try:
        # Validate treatment and imagery
        treatment = treatment.upper()
        imagery = imagery.upper()
        
        if treatment not in ['STANDARD', 'SBF']:
            return False, f"Invalid treatment: {treatment}. Must be 'STANDARD' or 'SBF'.", {}
        
        if imagery not in ['UAV', 'SATELLITE']:
            return False, f"Invalid imagery: {imagery}. Must be 'UAV' or 'SATELLITE'.", {}
        
        # Validate required inputs
        if not buffer_sectors_path or not os.path.isfile(buffer_sectors_path):
            return False, "Buffer sectors shapefile not found", {}
        
        if not si_column_name:
            return False, "SI column name is required", {}
        
        if not field_column:
            return False, "Field column name is required", {}
        
        # STANDARD + UAV workflow (fully implemented)
        if treatment == "STANDARD" and imagery == "UAV":
            return _run_standard_uav_workflow(
                si_column_name=si_column_name,
                field_column=field_column,
                buffer_sectors_path=buffer_sectors_path,
                ndre_path=ndre_path,
                csv_path=csv_path,
                output_dir=output_dir
            )
        
        # SBF + UAV workflow (placeholder)
        elif treatment == "SBF" and imagery == "UAV":
            if not ndre_path or not os.path.isfile(ndre_path):
                return False, "NDRE shapefile is required for SBF + UAV", {}
            if not indicator_block_path or not os.path.isfile(indicator_block_path):
                return False, "Indicator block shapefile is required for SBF", {}
            
            return False, "SBF + UAV workflow is not yet implemented. Coming soon!", {}
        
        # STANDARD + SATELLITE workflow (placeholder)
        elif treatment == "STANDARD" and imagery == "SATELLITE":
            if not nir_tiff_path:
                return False, "NIR TIFF file is required for SATELLITE imagery", {}
            if not rededge_tiff_path:
                return False, "RedEdge TIFF file is required for SATELLITE imagery", {}
            
            return False, "STANDARD + SATELLITE workflow is not yet implemented. Coming soon!", {}
        
        # SBF + SATELLITE workflow (placeholder)
        elif treatment == "SBF" and imagery == "SATELLITE":
            if not indicator_block_path or not os.path.isfile(indicator_block_path):
                return False, "Indicator block shapefile is required for SBF", {}
            if not nir_tiff_path:
                return False, "NIR TIFF file is required for SATELLITE imagery", {}
            if not rededge_tiff_path:
                return False, "RedEdge TIFF file is required for SATELLITE imagery", {}
            
            return False, "SBF + SATELLITE workflow is not yet implemented. Coming soon!", {}
        
        return False, "Invalid treatment/imagery combination", {}
        
    except Exception as e:
        return False, f"SI Tool error: {str(e)}", output_files


def _run_standard_uav_workflow(
    si_column_name: str,
    field_column: str,
    buffer_sectors_path: str,
    ndre_path: str,
    csv_path: str,
    output_dir: Optional[str] = None
) -> Tuple[bool, str, Dict]:
    """
    Run the STANDARD + UAV SI calculation workflow.
    
    This workflow:
    1. Reads buffer sectors and NDRE shapefiles
    2. Performs spatial intersection
    3. Calculates SI = avg_ndre / 95th_percentile_ndre for each field
    4. Updates the CSV file with SI values
    """
    output_files = {}
    
    try:
        # Validate inputs
        if not ndre_path or not os.path.isfile(ndre_path):
            return False, "NDRE shapefile is required for UAV imagery", {}
        
        if not csv_path or not os.path.isfile(csv_path):
            return False, "CSV file is required", {}
        
        # Read input files
        buffer_sectors = gpd.read_file(buffer_sectors_path)
        ndre_data = gpd.read_file(ndre_path)
        existing_data = pd.read_csv(csv_path)
        
        if buffer_sectors.empty:
            return False, "Buffer sectors shapefile is empty", {}
        
        if ndre_data.empty:
            return False, "NDRE shapefile is empty", {}
        
        # Align CRS if needed
        if buffer_sectors.crs != ndre_data.crs:
            ndre_data = ndre_data.to_crs(buffer_sectors.crs)
        
        # Perform spatial intersection
        intersected_data = gpd.overlay(buffer_sectors, ndre_data, how="intersection")
        
        if intersected_data.empty:
            return False, "No intersection found between buffer sectors and NDRE data", {}
        
        # Check for ndre column
        if "ndre" not in intersected_data.columns:
            # Try case-insensitive search
            ndre_col = None
            for col in intersected_data.columns:
                if col.lower() == "ndre":
                    ndre_col = col
                    break
            
            if ndre_col is None:
                available_cols = ", ".join(intersected_data.columns.tolist())
                return False, f"Column 'ndre' not found in NDRE shapefile. Available columns: {available_cols}", {}
            
            intersected_data = intersected_data.rename(columns={ndre_col: "ndre"})
        
        # Calculate 95th percentile of NDRE values
        ndre_values = intersected_data["ndre"]
        ndre_95th_percentile = ndre_values.quantile(0.95)
        
        if ndre_95th_percentile == 0:
            return False, "95th percentile of NDRE is 0, cannot calculate SI", {}
        
        # Drop geometry for grouping
        intersected_data_df = intersected_data.drop(columns="geometry")
        
        # Check field column exists
        if field_column not in intersected_data_df.columns:
            available_cols = ", ".join(intersected_data_df.columns.tolist())
            return False, f"Field column '{field_column}' not found. Available columns: {available_cols}", {}
        
        # Group by field and calculate average NDRE
        percentiles = (
            intersected_data_df
            .groupby(field_column, as_index=False)
            .agg(avg_ndre=("ndre", "mean"))
        )
        
        # Calculate SI = avg_ndre / 95th percentile
        percentiles[si_column_name] = percentiles["avg_ndre"] / ndre_95th_percentile
        
        # Merge SI values into existing CSV
        percentiles_subset = percentiles[[field_column, si_column_name]].copy()
        
        # Check if field column exists in CSV
        if field_column not in existing_data.columns:
            available_cols = ", ".join(existing_data.columns.tolist())
            return False, f"Field column '{field_column}' not found in CSV. Available columns: {available_cols}", {}
        
        merged = existing_data.merge(percentiles_subset, on=field_column, how="left")
        
        # Determine output path
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            output_csv_path = os.path.join(output_dir, os.path.basename(csv_path))
        else:
            # Overwrite original CSV
            output_csv_path = csv_path
        
        # Save updated CSV
        merged.to_csv(output_csv_path, index=False)
        output_files['updated_csv'] = output_csv_path
        
        # Build success message
        num_fields = len(percentiles)
        avg_si = percentiles[si_column_name].mean()
        message = (
            f"SI calculation complete. "
            f"Processed {num_fields} fields. "
            f"Average SI: {avg_si:.4f}. "
            f"Updated CSV saved."
        )
        
        return True, message, output_files
        
    except Exception as e:
        return False, f"STANDARD + UAV workflow error: {str(e)}", output_files
