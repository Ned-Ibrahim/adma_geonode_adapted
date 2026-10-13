"""Reading GIS files for their coordinate system and extent, publishing them, and repairing old rows.

The fixtures are a field near Mead, Nebraska, written inside the test with
geopandas and rasterio: a State Plane shapefile with the .prj ArcGIS writes,
the same zipped, a UTM GeoTIFF, a WGS84 GeoJSON, a UTM GeoPackage and a KML.
GeoServer is never reached: its REST and WMS answers are faked.
"""
import io
import json
import os
import shutil
import tempfile
import zipfile
from unittest import mock

import geopandas as gpd
import numpy as np
import rasterio
import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from pyproj import CRS
from pyproj.enums import WktVersion
from rasterio.transform import from_origin
from shapely.geometry import box

from filemanager import adapt_records, gis_read
from filemanager.geoserver_manager import SystematicGeoServerManager
from filemanager.gis_utils import process_gis_file, shapefile_parts
from filemanager.models import File, Folder

User = get_user_model()

FIELD = (-96.495, 41.167, -96.485, 41.173)
ESRI_6880 = CRS.from_epsg(6880).to_wkt(WktVersion.WKT1_ESRI)


def write_fixtures(out):
    """Write every fixture to directory `out`."""
    wgs = gpd.GeoDataFrame({'crop': ['corn']}, geometry=[box(*FIELD)], crs='EPSG:4326')
    wgs.to_crs(6880).to_file(os.path.join(out, 'field_sp.shp'))
    with open(os.path.join(out, 'field_sp.prj'), 'w') as prj:
        prj.write(ESRI_6880)
    with zipfile.ZipFile(os.path.join(out, 'field_zip.zip'), 'w') as archive:
        for ext in ('.shp', '.shx', '.dbf', '.prj'):
            archive.write(os.path.join(out, f'field_sp{ext}'), f'inner/field{ext}')
    with zipfile.ZipFile(os.path.join(out, 'notes.zip'), 'w') as archive:
        archive.writestr('readme.txt', 'no shapefile here')
    wgs.to_file(os.path.join(out, 'field.geojson'), driver='GeoJSON')
    wgs.to_crs(32614).to_file(os.path.join(out, 'field.gpkg'), driver='GPKG')
    wgs.to_file(os.path.join(out, 'field.kml'), driver='KML')
    minx, _, _, maxy = wgs.to_crs(32614).total_bounds
    write_tif(os.path.join(out, 'dem_utm.tif'), 'EPSG:32614', minx, maxy)
    write_tif(os.path.join(out, 'no_crs.tif'), None, 0, 40)
    with open(os.path.join(out, 'broken.geojson'), 'w') as broken:
        broken.write('{"type": "FeatureCollection", "features": [')


def write_tif(path, crs, minx, maxy):
    with rasterio.open(path, 'w', driver='GTiff', width=40, height=40, count=1, dtype='uint8',
                       crs=crs, transform=from_origin(minx, maxy, 20.0, 20.0)) as dst:
        dst.write(np.ones((40, 40), dtype='uint8'), 1)


class FixturesMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixtures = tempfile.mkdtemp()
        write_fixtures(cls.fixtures)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixtures, True)
        super().tearDownClass()

    def fixture(self, name):
        return os.path.join(self.fixtures, name)

    def assertNearField(self, lonlat):
        for got, want in zip(lonlat, FIELD):
            self.assertAlmostEqual(got, want, delta=0.002)


class ReadTests(FixturesMixin, SimpleTestCase):
    def test_each_type_reads_its_real_epsg_code_and_extent(self):
        for name, epsg in [('field_sp.shp', 6880), ('field_zip.zip', 6880), ('dem_utm.tif', 32614),
                           ('field.geojson', 4326), ('field.gpkg', 32614), ('field.kml', 4326)]:
            with self.subTest(name):
                info = gis_read.read(self.fixture(name))
                self.assertEqual(info.crs, f'EPSG:{epsg}')
                self.assertNearField(info.lonlat)
                self.assertEqual(json.loads(info.envelope_json())['crs'], 'EPSG:4326')

    def test_esri_state_plane_wkt_matches_epsg_6880(self):
        self.assertTrue(ESRI_6880.startswith('PROJCS["NAD_1983_2011_StatePlane_Nebraska_FIPS_2600_Ft_US"'))
        self.assertEqual(gis_read.epsg_code(ESRI_6880), 6880)

    def test_unreadable_inputs_say_why(self):
        for name, words in [('notes.zip', 'holds no shapefile'), ('no_crs.tif', 'no coordinate system'),
                            ('broken.geojson', 'cannot read')]:
            with self.subTest(name), self.assertRaisesRegex(gis_read.GisReadError, words):
                gis_read.read(self.fixture(name))

    def test_shapefile_without_prj_is_an_error(self):
        with tempfile.TemporaryDirectory() as bare:
            for ext in ('.shp', '.shx', '.dbf'):
                shutil.copy(self.fixture(f'field_sp{ext}'), os.path.join(bare, f'f{ext}'))
            with self.assertRaisesRegex(gis_read.GisReadError, 'no coordinate system'):
                gis_read.read(os.path.join(bare, 'f.shp'))

    def test_unsupported_types(self):
        self.assertIsNone(gis_read.kind_of('table.csv'))
        with self.assertRaisesRegex(gis_read.GisReadError, 'cannot show .csv'):
            gis_read.read(self.fixture('table.csv'))


class GisCase(FixturesMixin, TestCase):
    """A superuser's uploads under a temporary MEDIA_ROOT, and a temporary ADAPT mount."""

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.mount = os.path.realpath(tempfile.mkdtemp())
        for path in (self.media, self.mount):
            self.addCleanup(shutil.rmtree, path, True)
        patch = override_settings(MEDIA_ROOT=self.media, ADAPT_MOUNT=self.mount)
        patch.enable()
        self.addCleanup(patch.disable)
        self.user = User.objects.create_superuser('admin', password='pw')
        Folder.objects.create(name='ADAPT', owner=self.user, is_third_party=True,
                              third_party_source='adapt', third_party_id='adapt_root')
        self.client.force_login(self.user)

    def upload(self, name, as_name=None):
        with open(self.fixture(name), 'rb') as src:
            content = SimpleUploadedFile(as_name or name, src.read())
        return File.objects.create(name=as_name or name, file=content, owner=self.user)

    def upload_shapefile(self):
        for ext in ('.shx', '.dbf', '.prj'):
            self.upload(f'field_sp{ext}')
        return self.upload('field_sp.shp')

    def put_on_share(self, rel, fixture):
        path = os.path.join(self.mount, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(self.fixture(fixture), path)


class ProcessTests(GisCase):
    def test_uploaded_state_plane_shapefile_records_epsg_6880_and_its_true_extent(self):
        shp = self.upload_shapefile()
        ok, message = process_gis_file(shp)
        self.assertTrue(ok, message)
        shp.refresh_from_db()
        self.assertEqual((shp.gis_status, shp.crs), ('processed', 'EPSG:6880'))
        extent = json.loads(shp.spatial_extent)
        self.assertEqual(extent['crs'], 'EPSG:4326')
        self.assertNearField(extent['coordinates'][0] + extent['coordinates'][1])

    def test_unreadable_file_ends_with_no_invented_values(self):
        broken = self.upload('broken.geojson')
        broken.crs, broken.spatial_extent = 'EPSG:4326', '{"type": "envelope", "coordinates": [[-180, -90], [180, 90]]}'
        ok, message = process_gis_file(broken)
        self.assertFalse(ok)
        self.assertIn('cannot read', message)
        broken.refresh_from_db()
        self.assertIsNone(broken.crs)
        self.assertIsNone(broken.spatial_extent)

    def test_shapefile_waits_for_its_missing_parts(self):
        shp = self.upload('field_sp.shp')
        with self.assertRaises(gis_read.ShapefilePartsMissing) as caught:
            process_gis_file(shp)
        self.assertEqual(caught.exception.missing, ['.shx', '.dbf', '.prj'])

    def test_adapt_shapefile_parts_are_found_by_path_without_case(self):
        self.put_on_share('Soils/GIS/Field.SHP', 'field_sp.shp')
        self.put_on_share('Soils/GIS/field.shx', 'field_sp.shx')
        self.put_on_share('Soils/GIS/FIELD.dbf', 'field_sp.dbf')
        self.put_on_share('Soils/GIS/field.prj', 'field_sp.prj')
        self.put_on_share('Soils/GIS/field_other.dbf', 'field_sp.dbf')
        self.put_on_share('Soils/field.shx', 'field_sp.shx')
        record = adapt_records.record_for('Soils/GIS/Field.SHP')
        parts = shapefile_parts(record)
        self.assertEqual(sorted(parts), ['.dbf', '.prj', '.shp', '.shx'])
        self.assertTrue(parts['.dbf'].endswith('/Soils/GIS/FIELD.dbf'))
        ok, message = process_gis_file(record)
        self.assertTrue(ok, message)
        self.assertEqual(record.crs, 'EPSG:6880')


class MapPageTests(GisCase):
    def test_adapt_map_queues_processing_once_and_shows_it_preparing(self):
        self.put_on_share('Soils/dem.tif', 'dem_utm.tif')
        record = adapt_records.record_for('Soils/dem.tif')
        self.assertEqual(record.gis_status, 'pending')
        url = reverse('filemanager:map_viewer', args=[record.id])
        with mock.patch('filemanager.tasks.process_gis_file_task.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                first = self.client.get(url)
            with self.captureOnCommitCallbacks(execute=True):
                self.client.get(url)
        delay.assert_called_once_with(str(record.id))
        self.assertContains(first, 'Preparing the map')
        record.refresh_from_db()
        self.assertEqual(record.gis_status, 'processing')

    def test_opening_an_adapt_file_does_not_process_it(self):
        self.put_on_share('Soils/dem.tif', 'dem_utm.tif')
        with mock.patch('filemanager.tasks.process_gis_file_task.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.get('/adapt/open/Soils/dem.tif')
        delay.assert_not_called()

    def test_pending_upload_is_not_queued_again(self):
        upload = self.upload('dem_utm.tif')
        with mock.patch('filemanager.tasks.process_gis_file_task.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.get(reverse('filemanager:map_viewer', args=[upload.id]))
        delay.assert_not_called()

    def test_file_in_error_says_why_and_offers_no_map(self):
        upload = self.upload('notes.zip')
        upload.gis_status = 'error'
        upload.processing_log = 'Starting GIS file processing...\n✗ Processing failed: The zip archive holds no shapefile'
        upload.save()
        page = self.client.get(reverse('filemanager:map_viewer', args=[upload.id]))
        self.assertContains(page, 'cannot be shown on the map')
        self.assertContains(page, 'The zip archive holds no shapefile')
        detail = self.client.get(reverse('filemanager:file_detail', args=[upload.id]))
        self.assertContains(detail, 'The zip archive holds no shapefile')
        self.assertNotContains(detail, reverse('filemanager:map_viewer', args=[upload.id]))


class FakeGeoServer:
    """GeoServer REST and WMS for one feature type, as `requests` sees them."""

    RequestException = requests.RequestException

    def __init__(self, srs=None, enabled=False):
        self.state = {'name': 'layer', 'srs': srs, 'enabled': enabled, 'projectionPolicy': 'NONE'}
        if srs:
            self.state['latLonBoundingBox'] = dict(zip(('minx', 'miny', 'maxx', 'maxy'), FIELD))
        self.puts = []

    def get(self, url, **kwargs):
        if url.endswith('/wms'):
            drawn = self.state.get('srs') and self.state.get('enabled')
            return self.answer(200, content_type='image/png' if drawn else 'application/vnd.ogc.se_xml')
        if url.endswith('/featuretypes.json'):
            return self.answer(200, {'featureTypes': {'featureType': [{'name': kwargs.get('name', 'store')}]}})
        if '/rest/layers/' in url:
            return self.answer(200, {'layer': {'enabled': True}})
        return self.answer(200, {'featureType': dict(self.state)})

    def put(self, url, json=None, params=None, **kwargs):
        self.puts.append((url, json, params))
        change = json.get('featureType', {})
        self.state.update(change)
        if self.state.get('srs'):
            self.state['latLonBoundingBox'] = dict(zip(('minx', 'miny', 'maxx', 'maxy'), FIELD))
        return self.answer(200)

    def answer(self, status, body=None, content_type='application/json'):
        response = mock.Mock(status_code=status, headers={'Content-Type': content_type}, text='')
        response.json.return_value = body or {}
        return response


class PublishTests(GisCase):
    def publish(self, fake):
        shp = self.upload_shapefile()
        process_gis_file(shp)
        manager = SystematicGeoServerManager()
        store = manager.generate_systematic_layer_name(shp)
        original_get = fake.get
        fake.get = lambda url, **kw: original_get(url, name=store, **kw)
        with mock.patch('filemanager.geoserver_manager.requests', fake), \
                mock.patch.object(manager.geoserver_api, 'create_workspace', return_value=True), \
                mock.patch.object(manager.geoserver_api, 'upload_shapefile', return_value=True) as upload:
            result = manager.publish_file_to_geoserver(shp)
        return shp, store, result, upload

    def test_unknown_native_crs_is_declared_forced_and_enabled(self):
        fake = FakeGeoServer(srs=None, enabled=False)
        shp, store, (ok, message, layer), upload = self.publish(fake)
        self.assertTrue(ok, message)
        self.assertEqual(layer, store)
        bundle = upload.call_args.args[1]
        self.assertTrue(bundle.endswith(f'{store}.shp'))
        url, body, params = fake.puts[0]
        self.assertTrue(url.endswith(f'/datastores/{store}/featuretypes/{store}.json'))
        self.assertEqual(body, {'featureType': {'enabled': True, 'srs': 'EPSG:6880', 'projectionPolicy': 'FORCE_DECLARED'}})
        self.assertEqual(params, {'recalculate': 'nativebbox,latlonbbox'})
        shp.refresh_from_db()
        self.assertEqual((shp.gis_status, shp.geoserver_layer_name), ('published', store))

    def test_crs_geoserver_knows_is_left_alone(self):
        fake = FakeGeoServer(srs='EPSG:6880', enabled=True)
        shp, _, (ok, message, _), _ = self.publish(fake)
        self.assertTrue(ok, message)
        self.assertEqual(fake.puts, [])


class RepairCommandTests(GisCase):
    def run_command(self, *args):
        out = io.StringIO()
        call_command('fix_gis_layers', *args, stdout=out)
        return out.getvalue()

    def guessed_shapefile(self):
        shp = self.upload_shapefile()
        shp.gis_status = 'published'
        shp.crs = 'EPSG:4326'
        shp.spatial_extent = json.dumps({'type': 'envelope', 'coordinates': [[-180, -90], [180, 90]]})
        shp.geoserver_layer_name = shp.geoserver_datastore_name = 'old_layer'
        shp.save()
        return shp

    def test_dry_run_reports_and_changes_nothing(self):
        shp = self.guessed_shapefile()
        out = self.run_command()
        self.assertIn(f'{shp.id} field_sp.shp: would reprocess (guessed EPSG:4326)', out)
        shp.refresh_from_db()
        self.assertEqual(shp.crs, 'EPSG:4326')

    def test_apply_reads_the_file_and_repairs_the_layer_then_finds_nothing_to_do(self):
        shp = self.guessed_shapefile()
        manager = SystematicGeoServerManager
        with mock.patch.object(manager, 'layer_problem', return_value='srs null'), \
                mock.patch.object(manager, 'ensure_layer_srs', return_value=(True, 'declared EPSG:6880')) as ensure, \
                mock.patch.object(manager, 'layer_draws', return_value=True):
            out = self.run_command('--apply')
        self.assertIn(f'{shp.id} field_sp.shp: reprocessed (EPSG:6880; srs null: declared EPSG:6880)', out)
        ensure.assert_called_once_with('old_layer', 'old_layer', 'featureType', 6880)
        shp.refresh_from_db()
        self.assertEqual((shp.gis_status, shp.crs), ('published', 'EPSG:6880'))
        with mock.patch.object(manager, 'layer_problem', return_value=None):
            self.assertIn(f'{shp.id} field_sp.shp: ok (EPSG:6880)', self.run_command('--apply'))

    def test_pending_adapt_files_are_left_for_their_map(self):
        self.put_on_share('Soils/dem.tif', 'dem_utm.tif')
        record = adapt_records.record_for('Soils/dem.tif')
        self.assertIn(f'{record.id} dem.tif: left alone (pending)', self.run_command('--apply'))
