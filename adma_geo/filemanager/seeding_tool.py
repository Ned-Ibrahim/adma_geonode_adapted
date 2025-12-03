#!/usr/bin/env python3
"""
Seeding Tool for ADMA - Processes Point GIS files (.shp/.gpkg) to generate:
  - Seeding polygons (buffered by swath/boom width)
  - Boundary polygons (coverage + rect + auto margin)
  - Summary CSV with area statistics

Adapted from SeedingTool_asapplied_ADMA_finalscript.py for use as a Celery task.
"""

import os
import logging
import warnings
from typing import Dict, List, Tuple, Optional

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import box
import fiona

logger = logging.getLogger(__name__)

# =========================
# Core helpers
# =========================

def polygons_where_applied_with_means(
    gdf: gpd.GeoDataFrame, width_units: str = "auto"
) -> Tuple[gpd.GeoDataFrame, Dict]:
    """
    Create polygons from point data by buffering based on swath/boom width.
    Calculates mean values for numeric columns per product.
    """
    if gdf.empty:
        raise ValueError("Input GeoDataFrame is empty.")
    if not np.all(gdf.geometry.geom_type == "Point"):
        raise AssertionError("Layer must contain Point geometries.")

    crs_m = gdf.estimate_utm_crs()
    df = gdf.to_crs(crs_m).copy()

    def _find(cols, pred, required=True, label="column"):
        for c in cols:
            if pred(c):
                return c
        if required:
            raise KeyError(f"Could not find required {label}.")
        return None

    prod_col = _find(df.columns, lambda c: c.lower() == "product", True, "`Product`")
    width_col = _find(
        df.columns,
        lambda c: any(k in c.lower() for k in ["swath", "width", "boom"]),
        True,
        "swath/width/boom column",
    )
    rate_col = _find(
        df.columns,
        lambda c: c.lower() in {"appliedrate", "appliedrat", "rate"},
        False,
        "rate",
    )

    df[prod_col] = df[prod_col].astype(str).str.strip()
    valid = df[prod_col].ne("").fillna(False)
    if rate_col is not None:
        df[rate_col] = pd.to_numeric(df[rate_col], errors="coerce")
        valid &= df[rate_col].gt(0).fillna(False)
    df = df.loc[valid].copy()
    if df.empty:
        raise ValueError("No applied points after filtering (check Product/AppliedRate).")

    df[width_col] = pd.to_numeric(df[width_col], errors="coerce")
    swath_med_orig = float(df[width_col].median())
    if width_units == "auto":
        width_units = "feet" if 6 <= swath_med_orig <= 20 else "meters"
    use_feet = width_units.lower().startswith("ft")
    df["swath_m"] = df[width_col] * (0.3048 if use_feet else 1.0)

    radius = (df["swath_m"] / 2.0) + 0.02
    df["geometry"] = df.geometry.buffer(radius, cap_style=1)
    polys = df[[prod_col, "geometry"]].dissolve(by=prod_col, as_index=False)
    polys["geometry"] = polys.buffer(0)
    polys = polys.to_crs(gdf.crs)

    exclude = {prod_col, "geometry"}
    num_cols: List[str] = [
        c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])
    ]
    per_prod_mean = (
        df.groupby(prod_col)[num_cols]
          .mean(numeric_only=True)
          .reset_index()
          .rename(columns={c: f"{c}_mean" for c in num_cols})
    )
    polys_with_means = polys.merge(per_prod_mean, on=prod_col, how="left")

    info = {
        "product_column": prod_col,
        "width_column": width_col,
        "rate_column": rate_col,
        "swath_units": width_units,
        "swath_median_in_original_units": swath_med_orig,
        "averaged_columns": num_cols,
    }
    return polys_with_means, info


def create_boundary_from_points(
    gdf: gpd.GeoDataFrame, mode: str = "coverage", method: str = "rect", margin: str = "auto"
) -> Tuple[gpd.GeoDataFrame, Dict]:
    """
    Create a boundary polygon from point data.
    
    Args:
        gdf: GeoDataFrame with Point geometries
        mode: 'coverage' (buffer by swath) or 'points' (just the points)
        method: 'rect' (minimum rotated rectangle), 'bbox' (axis-aligned), or 'hull' (convex hull)
        margin: Buffer margin in meters, or 'auto' to calculate from swath
    """
    if gdf.empty:
        raise ValueError("Input GeoDataFrame is empty.")

    crs_m = gdf.estimate_utm_crs()
    pts = gdf.to_crs(crs_m).copy()

    width_col = next(
        (c for c in pts.columns if any(k in c.lower() for k in ["swath", "width", "boom"])),
        None,
    )
    if width_col is None:
        raise KeyError("Could not find swath/width/boom column for boundary building.")

    pts[width_col] = pd.to_numeric(pts[width_col], errors="coerce")
    swath_med_orig = float(pts[width_col].median())
    is_feet = 6 <= swath_med_orig <= 20
    swath_m = pts[width_col] * (0.3048 if is_feet else 1.0)

    margin_m = 0.9144 if (margin == "auto" and is_feet) else (0.5 if margin == "auto" else float(margin))

    if mode == "coverage":
        cov = gpd.GeoSeries(pts.geometry.buffer(swath_m / 2.0, cap_style=1), crs=crs_m)
        union_cov = cov.union_all() if hasattr(cov, "union_all") else cov.unary_union
        base_geom = union_cov
    elif mode == "points":
        union_pts = pts.geometry.union_all() if hasattr(pts.geometry, "union_all") else pts.unary_union
        base_geom = union_pts
    else:
        raise ValueError("boundary_mode must be 'coverage' or 'points'.")

    base = gpd.GeoSeries([base_geom.convex_hull], crs=crs_m)
    m = method.lower()
    if m == "rect":
        base = gpd.GeoSeries([base.iloc[0].minimum_rotated_rectangle], crs=crs_m)
    elif m == "bbox":
        minx, miny, maxx, maxy = base.total_bounds
        base = gpd.GeoSeries([box(minx, miny, maxx, maxy)], crs=crs_m)
    elif m == "hull":
        pass
    else:
        raise ValueError("boundary_method must be one of: rect, bbox, hull")

    boundary = base.buffer(margin_m, join_style=2)  # square corners
    boundary_gdf = gpd.GeoDataFrame(geometry=boundary, crs=crs_m).to_crs(gdf.crs)

    info = {
        "mode": mode,
        "method": method,
        "margin_m": margin_m,
        "swath_units_detected": "feet" if is_feet else "meters",
        "swath_median_original_units": swath_med_orig,
    }
    return boundary_gdf, info


def area_summary_by_product(polys: gpd.GeoDataFrame, prod_col: str) -> pd.DataFrame:
    """Calculate area statistics per product."""
    crs_m = polys.estimate_utm_crs()
    m = polys.to_crs(crs_m).copy()
    m["area_m2"] = m.geometry.area
    summary = (
        m.groupby(m[prod_col])
         .agg(area_m2=("area_m2", "sum"))
         .reset_index()
         .rename(columns={prod_col: "Product"})
    )
    summary["hectares"] = summary["area_m2"] / 10_000.0
    summary["acres"] = summary["area_m2"] / 4046.8564224
    return summary


# =========================
# I/O helpers (Shapefile)
# =========================

def _ensure_shapefile_columns(df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Truncate column names to 10 characters for Shapefile compatibility."""
    df = df.copy()
    df.columns = [c if c == "geometry" else c[:10] for c in df.columns]
    return df


def save_shapefile(gdf: gpd.GeoDataFrame, out_path: str) -> str:
    """Save GeoDataFrame as Shapefile."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if not out_path.lower().endswith(".shp"):
        out_path += ".shp"
    gdf = _ensure_shapefile_columns(gdf)
    gdf.to_file(out_path, driver="ESRI Shapefile")
    return out_path


# =========================
# Validation helpers
# =========================

def validate_seeding_data(gdf: gpd.GeoDataFrame) -> Tuple[bool, str, dict]:
    """
    Validate that a GeoDataFrame has the required columns for seeding tool processing.
    
    Returns:
        Tuple of (is_valid, error_message, column_info)
    """
    if gdf is None or gdf.empty:
        return False, "No data found in file", {}
    
    # Check geometry type
    geom_types = gdf.geometry.geom_type.unique()
    if 'Point' not in geom_types:
        return False, f"Seeding Tool requires Point geometries. Found: {', '.join(geom_types)}", {}
    
    columns = [c.lower() for c in gdf.columns]
    column_info = {
        'available_columns': list(gdf.columns),
        'product_column': None,
        'width_column': None,
        'rate_column': None,
    }
    
    # Check for Product column
    product_col = None
    for c in gdf.columns:
        if c.lower() == 'product':
            product_col = c
            column_info['product_column'] = c
            break
    
    if not product_col:
        return False, (
            f"Missing required 'Product' column. "
            f"Available columns: {', '.join(gdf.columns[:10])}{'...' if len(gdf.columns) > 10 else ''}"
        ), column_info
    
    # Check for swath/width/boom column
    width_col = None
    for c in gdf.columns:
        if any(k in c.lower() for k in ['swath', 'width', 'boom']):
            width_col = c
            column_info['width_column'] = c
            break
    
    if not width_col:
        return False, (
            f"Missing required swath/width/boom column. "
            f"Available columns: {', '.join(gdf.columns[:10])}{'...' if len(gdf.columns) > 10 else ''}"
        ), column_info
    
    # Check for rate column (optional)
    for c in gdf.columns:
        if c.lower() in {'appliedrate', 'appliedrat', 'rate'}:
            column_info['rate_column'] = c
            break
    
    return True, "Valid seeding data", column_info


# =========================
# File reading helpers
# =========================

def read_first_point_layer(path: str) -> Optional[gpd.GeoDataFrame]:
    """
    Read the first Point layer from a shapefile or geopackage.
    Returns None if no Point layer is found.
    """
    try:
        if path.lower().endswith(".gpkg"):
            layers = fiona.listlayers(path)
            # Try first, then scan
            for lyr in layers:
                try:
                    gdf = gpd.read_file(path, layer=lyr)
                    if not gdf.empty and np.all(gdf.geometry.geom_type == "Point"):
                        return gdf
                except Exception:
                    continue
            return None
        else:
            gdf = gpd.read_file(path)
            return gdf if (not gdf.empty and np.all(gdf.geometry.geom_type == "Point")) else None
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        return None


# =========================
# Main processing function
# =========================

def process_seeding_tool(input_path: str, output_dir: str) -> Tuple[bool, str, Dict]:
    """
    Process a single GIS file with the Seeding Tool.
    
    Args:
        input_path: Path to the input .shp or .gpkg file
        output_dir: Directory to save output files (same as input folder)
    
    Returns:
        Tuple of (success: bool, message: str, output_files: dict)
        output_files contains paths to generated files: 'polys', 'boundary', 'csv'
    """
    warnings.filterwarnings("ignore", category=UserWarning)
    
    output_files = {}
    
    try:
        # Read the input file - try to read any layer first for validation
        try:
            gdf = gpd.read_file(input_path)
        except Exception as e:
            return False, f"Could not read file: {str(e)}", {}
        
        if gdf is None or gdf.empty:
            return False, "File is empty or could not be read.", {}
        
        # Validate the data has required columns
        is_valid, validation_msg, column_info = validate_seeding_data(gdf)
        if not is_valid:
            return False, (
                f"This file is not compatible with the Seeding Tool. {validation_msg}. "
                f"The Seeding Tool requires Point data with 'Product' and 'swath/width/boom' columns "
                f"(typically from seeding/application equipment)."
            ), {}
        
        # Now check if it's Point geometry
        if not np.all(gdf.geometry.geom_type == "Point"):
            geom_types = gdf.geometry.geom_type.unique()
            return False, (
                f"Seeding Tool requires Point geometries. "
                f"This file contains: {', '.join(geom_types)}."
            ), {}

        # Get base name for output files
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        
        # Process: create polygons with means
        polys_mean, info_poly = polygons_where_applied_with_means(gdf, width_units="auto")
        
        # Process: create boundary
        boundary_gdf, info_bnd = create_boundary_from_points(
            gdf, mode="coverage", method="rect", margin="auto"
        )
        
        # Clip polygons to boundary
        clipped = gpd.clip(polys_mean.to_crs(boundary_gdf.crs), boundary_gdf).to_crs(gdf.crs)

        # Define output paths
        polys_path = os.path.join(output_dir, f"{base_name}_seeding_polys.shp")
        boundary_path = os.path.join(output_dir, f"{base_name}_boundary.shp")
        csv_path = os.path.join(output_dir, f"{base_name}_summary.csv")

        # Save shapefiles (polys + boundary)
        save_shapefile(clipped, polys_path)
        save_shapefile(boundary_gdf.to_crs(gdf.crs), boundary_path)

        # Summary CSV
        prod_col = info_poly["product_column"]
        summary = area_summary_by_product(clipped, prod_col)
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        summary.to_csv(csv_path, index=False)

        output_files = {
            'polys': polys_path,
            'boundary': boundary_path,
            'csv': csv_path,
        }

        msg = (
            f"Successfully processed {os.path.basename(input_path)} | "
            f"Units={info_poly['swath_units']}, Median swath={info_poly['swath_median_in_original_units']:.3f}, "
            f"Margin={info_bnd['margin_m']:.2f}m | "
            f"Output: {base_name}_seeding_polys.shp, {base_name}_boundary.shp, {base_name}_summary.csv"
        )
        
        return True, msg, output_files
        
    except KeyError as e:
        return False, f"Missing required column: {e}", {}
    except ValueError as e:
        return False, f"Data validation error: {e}", {}
    except AssertionError as e:
        return False, f"Geometry error: {e}", {}
    except Exception as e:
        logger.exception(f"Error processing seeding tool for {input_path}")
        return False, f"Processing error: {str(e)}", {}

