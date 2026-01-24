import os
import warnings
from typing import Dict, Tuple

import geopandas as gpd
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)


def process_seeding_tool(input_path: str, output_dir: str) -> Tuple[bool, str, Dict]:
    """
    Process a seeding shapefile to create seeding polygons, boundary, and summary.
    
    Args:
        input_path: Full path to the input seeding POINT shapefile (.shp)
        output_dir: Directory where output files will be saved
        
    Returns:
        Tuple of (success: bool, message: str, output_files: Dict)
        output_files contains keys: 'polygons', 'boundary', 'summary'
    """
    output_files = {}
    
    try:
        # Validate input file
        if not input_path.lower().endswith(".shp"):
            return False, "Input must be a .shp file", {}

        if not os.path.isfile(input_path):
            return False, "Shapefile not found", {}

        os.makedirs(output_dir, exist_ok=True)

        # READ INPUT
        gdf = gpd.read_file(input_path)

        if gdf.empty or not np.all(gdf.geometry.geom_type == "Point"):
            return False, "Input shapefile must contain Point geometries", {}

        # BUILD POLYGONS WHERE APPLIED (WITH MEANS)
        crs_m = gdf.estimate_utm_crs()
        df = gdf.to_crs(crs_m).copy()

        def find_col(cols, pred, label):
            for c in cols:
                if pred(c):
                    return c
            raise KeyError(f"Missing required column: {label}")

        prod_col = find_col(df.columns, lambda c: c.lower() == "product", "Product")
        width_col = find_col(
            df.columns,
            lambda c: any(k in c.lower() for k in ["swath", "width", "boom"]),
            "swath/width/boom",
        )

        rate_col = next(
            (c for c in df.columns if c.lower() in {"appliedrate", "appliedrat", "rate"}),
            None,
        )

        df[prod_col] = df[prod_col].astype(str).str.strip()
        valid = df[prod_col].ne("").fillna(False)

        if rate_col:
            df[rate_col] = pd.to_numeric(df[rate_col], errors="coerce")
            valid &= df[rate_col].gt(0).fillna(False)

        df = df.loc[valid].copy()
        if df.empty:
            return False, "No applied points after filtering", {}

        df[width_col] = pd.to_numeric(df[width_col], errors="coerce")
        swath_med_orig = float(df[width_col].median())
        use_feet = 6 <= swath_med_orig <= 20

        df["swath_m"] = df[width_col] * (0.3048 if use_feet else 1.0)

        radius = (df["swath_m"] / 2.0) + 0.02
        df["geometry"] = df.geometry.buffer(radius, cap_style=1)

        polys = df[[prod_col, "geometry"]].dissolve(by=prod_col, as_index=False)
        polys["geometry"] = polys.buffer(0)
        polys = polys.to_crs(gdf.crs)

        num_cols = [
            c for c in df.columns
            if c not in {prod_col, "geometry"}
            and pd.api.types.is_numeric_dtype(df[c])
        ]

        means = (
            df.groupby(prod_col)[num_cols]
            .mean(numeric_only=True)
            .reset_index()
            .rename(columns={c: f"{c}_mean" for c in num_cols})
        )

        polys = polys.merge(means, on=prod_col, how="left")

        # CREATE AUTO BOUNDARY
        pts = gdf.to_crs(crs_m).copy()
        pts[width_col] = pd.to_numeric(pts[width_col], errors="coerce")
        swath_m = pts[width_col] * (0.3048 if use_feet else 1.0)

        margin_m = 0.9144 if use_feet else 0.5

        coverage = gpd.GeoSeries(
            pts.geometry.buffer(swath_m / 2.0, cap_style=1), crs=crs_m
        )
        union_geom = (
            coverage.union_all() if hasattr(coverage, "union_all") else coverage.unary_union
        )

        base = union_geom.convex_hull.minimum_rotated_rectangle
        boundary = gpd.GeoDataFrame(
            geometry=[base.buffer(margin_m, join_style=2)], crs=crs_m
        ).to_crs(gdf.crs)

        # CLIP POLYGONS
        clipped = gpd.clip(polys, boundary)

        # SAVE OUTPUTS (SHAPEFILE ONLY)
        base_name = os.path.splitext(os.path.basename(input_path))[0]

        polys_path = os.path.join(output_dir, f"{base_name}_seeding_polys.shp")
        boundary_path = os.path.join(output_dir, f"{base_name}_boundary.shp")
        csv_path = os.path.join(output_dir, f"{base_name}_summary.csv")

        clipped.columns = [c if c == "geometry" else c[:10] for c in clipped.columns]
        boundary.columns = ["geometry"]

        clipped.to_file(polys_path)
        boundary.to_file(boundary_path)

        output_files['polygons'] = polys_path
        output_files['boundary'] = boundary_path

        # AREA SUMMARY
        m = clipped.to_crs(clipped.estimate_utm_crs()).copy()
        m["area_m2"] = m.geometry.area

        summary = (
            m.groupby(prod_col)
            .agg(area_m2=("area_m2", "sum"))
            .reset_index()
            .rename(columns={prod_col: "Product"})
        )

        summary["hectares"] = summary["area_m2"] / 10_000.0
        summary["acres"] = summary["area_m2"] / 4046.8564224
        summary.to_csv(csv_path, index=False)

        output_files['summary'] = csv_path

        # Build success message
        units = 'feet' if use_feet else 'meters'
        num_cols_str = ", ".join(num_cols) if num_cols else "(none)"
        message = (
            f"Completed successfully. "
            f"Detected swath units: {units}. "
            f"Averaged numeric columns: {num_cols_str}"
        )

        return True, message, output_files

    except KeyError as e:
        return False, str(e), output_files
    except Exception as e:
        return False, f"Processing error: {str(e)}", output_files
