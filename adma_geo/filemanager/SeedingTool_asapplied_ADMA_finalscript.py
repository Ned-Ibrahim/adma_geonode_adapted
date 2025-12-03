#!/usr/bin/env python3
# SeedingTool_batch_shp_ADMA.py
# Usage:
#   python SeedingTool_batch_shp_ADMA.py /path/to/root_input /path/to/root_output
#
# For each .shp / .gpkg under root_input:
#   - Detect Product + swath/boom width columns
#   - Build per-product polygons (buffer by swath/2) with MEAN numeric attrs
#   - Build auto boundary (coverage + rect + auto margin)
#   - Clip polygons to boundary
#   - Save Shapefiles:
#       <basename>_seeding_polys.shp
#       <basename>_boundary.shp
#     and CSV:
#       <basename>_summary.csv
#
# Notes:
#   - Width units auto-detect: feet if median 6–20, else meters
#   - Shapefile field names truncated to 10 chars (spec limit)
#   - Skips non-Point layers and continues on errors

import argparse
import os
import sys
import warnings
from typing import Dict, List, Tuple, Optional

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import box
import fiona

# =========================
# Core helpers
# =========================

def polygons_where_applied_with_means(
    gdf: gpd.GeoDataFrame, width_units: str = "auto"
) -> Tuple[gpd.GeoDataFrame, Dict]:
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
    df = df.copy()
    df.columns = [c if c == "geometry" else c[:10] for c in df.columns]
    return df

def save_shapefile(gdf: gpd.GeoDataFrame, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if not out_path.lower().endswith(".shp"):
        out_path += ".shp"
    gdf = _ensure_shapefile_columns(gdf)
    gdf.to_file(out_path, driver="ESRI Shapefile")
    return out_path


# =========================
# Batch file discovery
# =========================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch Seeding Tool (Shapefile outputs): process all Point layers under a root folder."
    )
    p.add_argument("root_input", type=str, help="Root directory containing nested job folders with .shp/.gpkg.")
    p.add_argument("root_output", type=str, help="Root directory to write results (mirrors input structure).")
    return p.parse_args()

def find_vector_files(root: str) -> List[str]:
    out = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".shp") or fn.lower().endswith(".gpkg"):
                out.append(os.path.join(dirpath, fn))
    return out

def read_first_point_layer(path: str) -> Optional[gpd.GeoDataFrame]:
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
    except Exception:
        return None

def mirrored_out_paths(in_path: str, root_in: str, root_out: str) -> Dict[str, str]:
    rel_dir = os.path.relpath(os.path.dirname(in_path), root_in)
    base = os.path.splitext(os.path.basename(in_path))[0]
    out_dir = os.path.join(root_out, rel_dir)
    return {
        "polys": os.path.join(out_dir, f"{base}_seeding_polys.shp"),
        "bnd":   os.path.join(out_dir, f"{base}_boundary.shp"),
        "csv":   os.path.join(out_dir, f"{base}_summary.csv"),
    }


# =========================
# Per-dataset processing
# =========================

def process_one_dataset(in_path: str, root_in: str, root_out: str) -> Tuple[bool, str]:
    try:
        gdf = read_first_point_layer(in_path)
        if gdf is None:
            return False, "No readable Point layer found"

        polys_mean, info_poly = polygons_where_applied_with_means(gdf, width_units="auto")
        boundary_gdf, info_bnd = create_boundary_from_points(
            gdf, mode="coverage", method="rect", margin="auto"
        )
        clipped = gpd.clip(polys_mean.to_crs(boundary_gdf.crs), boundary_gdf).to_crs(gdf.crs)

        paths = mirrored_out_paths(in_path, root_in, root_out)

        # Save shapefiles (polys + boundary)
        save_shapefile(clipped, paths["polys"])
        save_shapefile(boundary_gdf.to_crs(gdf.crs), paths["bnd"])

        # Summary CSV
        prod_col = info_poly["product_column"]
        summary = area_summary_by_product(clipped, prod_col)
        os.makedirs(os.path.dirname(paths["csv"]), exist_ok=True)
        summary.to_csv(paths["csv"], index=False)

        msg = (
            f"OK | {os.path.basename(in_path)} → "
            f"{os.path.relpath(paths['polys'], root_out)}, "
            f"{os.path.relpath(paths['bnd'], root_out)}, "
            f"{os.path.relpath(paths['csv'], root_out)} "
            f"[units={info_poly['swath_units']}, med={info_poly['swath_median_in_original_units']:.3f}; "
            f"margin_m={info_bnd['margin_m']:.2f}]"
        )
        return True, msg
    except Exception as e:
        return False, f"ERROR processing {os.path.basename(in_path)}: {e}"


# =========================
# Main
# =========================

def main():
    warnings.filterwarnings("ignore", category=UserWarning)
    args = parse_args()

    root_in = os.path.abspath(args.root_input)
    root_out = os.path.abspath(args.root_output)

    if not os.path.isdir(root_in):
        print(f"Input root does not exist or is not a directory: {root_in}")
        sys.exit(1)
    os.makedirs(root_out, exist_ok=True)

    files = find_vector_files(root_in)
    if not files:
        print("No .shp or .gpkg files found under input root.")
        sys.exit(1)

    print(f"Discovered {len(files)} candidate files. Scanning for Point layers...")
    successes = failures = 0

    for pth in files:
        ok, msg = process_one_dataset(pth, root_in, root_out)
        print(msg)
        successes += int(ok)
        failures += int(not ok)

    print("\n=== Batch Complete ===")
    print(f"Successful: {successes}  |  Skipped/Failed: {failures}")
    if failures:
        print("Review messages above for details.")

if __name__ == "__main__":
    main()
