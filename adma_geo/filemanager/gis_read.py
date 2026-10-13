"""Where a GIS file says it is: its coordinate system and its extent.

ADMA reads both from the file itself before it publishes the file to GeoServer,
and never guesses. A file whose coordinate system or extent cannot be read is
an error the user is told about, not a layer placed somewhere invented.

The coordinate system is kept as an EPSG code, the form GeoServer and the map
pages understand; an ESRI .prj is matched to its EPSG code. The extent is kept
in longitude and latitude (EPSG:4326), the form both the single file map and
the custom Map pages zoom to.

Vectors are read with pyogrio and rasters with rasterio; both bring their own
GDAL.
"""
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path

RASTER_EXTENSIONS = ('.tif', '.tiff', '.geotif', '.geotiff')
# Vector formats GeoServer has no store for: publishing converts them to a GeoPackage.
CONVERTED_EXTENSIONS = ('.geojson', '.gpkg', '.kml', '.kmz')
# The parts of a shapefile ADMA needs to map it. Without the .prj there is no
# coordinate system to read.
SHAPEFILE_REQUIRED = ('.shp', '.shx', '.dbf', '.prj')
# Parts used when present: the encoding of the .dbf and the spatial indexes.
SHAPEFILE_OPTIONAL = ('.cpg', '.qix', '.sbn', '.sbx')
SHAPEFILE_PARTS = SHAPEFILE_REQUIRED + SHAPEFILE_OPTIONAL

# How sure pyproj must be that a coordinate system is a given EPSG code when
# the exact match finds none. A lower match is taken only when it is the one
# best match.
FALLBACK_CONFIDENCE = 25


class GisReadError(Exception):
    """ADMA cannot place this file on the map. The message says why, in words for the user."""


class ShapefilePartsMissing(GisReadError):
    """A shapefile whose other parts are not there (yet): they may still be on their way up."""

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__(f"The shapefile has no {', '.join(self.missing)} next to it")


@dataclass(frozen=True)
class SpatialInfo:
    """The EPSG code of a file's coordinate system, and its extent in longitude and latitude."""

    epsg: int
    lonlat: tuple  # (west, south, east, north)

    @property
    def crs(self):
        return f'EPSG:{self.epsg}'

    def envelope_json(self):
        """The extent as File.spatial_extent keeps it."""
        west, south, east, north = self.lonlat
        return json.dumps({
            'type': 'envelope',
            'crs': 'EPSG:4326',
            'coordinates': [[west, south], [east, north]],
        })


def kind_of(name):
    """'raster', 'shapefile', 'zip' or 'vector' for a file ADMA can map, else None."""
    ext = Path(name).suffix.lower()
    if ext in RASTER_EXTENSIONS:
        return 'raster'
    if ext == '.shp':
        return 'shapefile'
    if ext == '.zip':
        return 'zip'
    if ext in CONVERTED_EXTENSIONS:
        return 'vector'
    return None


def epsg_code(crs):
    """The EPSG code of coordinate system `crs` (WKT, ESRI WKT, or anything pyproj reads).

    GisReadError when pyproj cannot read it or cannot match it to one EPSG code.
    """
    from pyproj import CRS
    from pyproj.exceptions import CRSError

    try:
        crs = CRS.from_user_input(crs)
    except CRSError:
        raise GisReadError('ADMA cannot read the coordinate system of this file')
    code = crs.to_epsg()
    if code is not None:
        return code
    matches = crs.list_authority(auth_name='EPSG', min_confidence=FALLBACK_CONFIDENCE)
    best = [m for m in matches if m.confidence == matches[0].confidence] if matches else []
    if len(best) == 1:
        return int(best[0].code)
    raise GisReadError(f'ADMA cannot match the coordinate system "{crs.name}" to an EPSG code')


def read(path, name=None):
    """The SpatialInfo of the GIS file at `path`; `name` gives its type when the path does not.

    A shapefile is read from `path` with its other parts beside it under the
    same name. GisReadError says why a file cannot be mapped.
    """
    kind = kind_of(name or path)
    if kind == 'raster':
        return read_raster(path)
    if kind == 'zip':
        return read_vector(f'/vsizip/{path}/{zip_shapefile(path)}')
    if kind in ('shapefile', 'vector'):
        return read_vector(path)
    raise GisReadError(f'ADMA cannot show {Path(name or path).suffix or "this kind of"} files on a map')


def read_vector(path):
    """The SpatialInfo of a vector file holding one layer."""
    import pyogrio

    try:
        layers = pyogrio.list_layers(path)
        if len(layers) != 1:
            raise GisReadError(f'The file holds {len(layers)} layers; ADMA maps a file with exactly one layer')
        info = pyogrio.read_info(path, force_total_bounds=True)
    except GisReadError:
        raise
    except Exception as exc:
        raise GisReadError(f'ADMA cannot read this file: {exc}')
    if not info.get('crs'):
        raise GisReadError('The file has no coordinate system (a shapefile needs its .prj)')
    if not info.get('features'):
        raise GisReadError('The file holds no features')
    return _info(epsg_code(info['crs']), info.get('total_bounds'))


def read_raster(path):
    """The SpatialInfo of a raster."""
    import rasterio

    try:
        with rasterio.open(path) as src:
            crs, bounds = src.crs, tuple(src.bounds)
    except Exception as exc:
        raise GisReadError(f'ADMA cannot read this raster: {exc}')
    if crs is None:
        raise GisReadError('The raster has no coordinate system')
    return _info(epsg_code(crs.to_wkt()), bounds)


def zip_shapefile(path):
    """The name of the one shapefile in zip archive `path`, or GisReadError."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        raise GisReadError('The file is not a zip archive ADMA can open')
    shapefiles = [n for n in names if n.lower().endswith('.shp') and not n.startswith('__MACOSX/')]
    if not shapefiles:
        raise GisReadError('The zip archive holds no shapefile')
    if len(shapefiles) > 1:
        raise GisReadError(f'The zip archive holds {len(shapefiles)} shapefiles; ADMA maps a zip with exactly one')
    shp = shapefiles[0]
    stem = shp[:-4].lower()
    present = {n[len(stem):].lower() for n in names if n.lower().startswith(stem) and len(n) > len(stem)}
    missing = [ext for ext in SHAPEFILE_REQUIRED if ext not in present]
    if missing:
        raise GisReadError(f"The shapefile in the zip archive has no {', '.join(missing)}")
    return shp


def _info(epsg, bounds):
    from pyproj import Transformer

    if not bounds or not all(math.isfinite(v) for v in bounds):
        raise GisReadError('ADMA cannot read the extent of this file')
    try:
        to_lonlat = Transformer.from_crs(epsg, 4326, always_xy=True)
        west, south, east, north = to_lonlat.transform_bounds(*bounds, densify_pts=21)
    except Exception:
        raise GisReadError(f'The extent of this file does not fit its coordinate system EPSG:{epsg}')
    if not (all(math.isfinite(v) for v in (west, south, east, north))
            and -180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        raise GisReadError(f'The extent of this file does not fit its coordinate system EPSG:{epsg}')
    return SpatialInfo(epsg=epsg, lonlat=(west, south, east, north))
