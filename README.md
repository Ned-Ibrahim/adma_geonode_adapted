# ADMA - Agricultural Data Management & Analytics

A comprehensive web-based platform for managing, visualizing, and analyzing agricultural geospatial data. Built on Django with GeoServer integration for advanced GIS capabilities.

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Third-Party Integrations](#third-party-integrations)
- [API Documentation](#api-documentation)
- [Development](#development)
- [Deployment](#deployment)

## Features

### File & Folder Management
- Hierarchical folder structure with unlimited nesting
- Support for multiple file types (GIS, documents, images, spreadsheets)
- Public/private visibility controls
- Drag-and-drop file uploads
- Folder and file renaming

### GIS Data Processing
- Automatic detection and processing of spatial files (Shapefiles, GeoTIFF, GeoJSON, KML)
- Integration with GeoServer for spatial data publishing
- Interactive map visualization using OpenLayers
- Multiple base map options (OpenStreetMap, Satellite, Terrain, Topographic)
- Support for both vector and raster data

### Custom Maps
- Create composite maps by combining multiple spatial layers
- Layer ordering and styling controls
- Map location tracking with navigation overview
- Public map sharing

### Analysis Tools
- **Seeding Tool**: Process agricultural seeding data with GIS outputs
- **Shape to JSON**: Convert shapefiles to GeoJSON format
- **SI Tool**: Sustainability index calculations

### Third-Party Integrations
- **John Deere Operations Center**: Sync field boundaries, operations data, and metadata
- **Realm5 Weather Stations**: Daily weather observations with data visualization

### User Features
- User registration and authentication
- Token-based API access
- Dashboard with file statistics
- Search functionality across files and folders

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Nginx (Reverse Proxy)                 │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Django App     │  │   GeoServer     │  │   Static Files  │
│  (Port 8000)    │  │   (Port 8080)   │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
         │                    │
         │                    │
         ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│              PostgreSQL + PostGIS (Port 5432)               │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐  ┌─────────────────┐
│  Celery Worker  │◄─│     Redis       │
│  (Background)   │  │   (Broker)      │
└─────────────────┘  └─────────────────┘
```

### Services

| Service    | Description                                      | Port  |
|------------|--------------------------------------------------|-------|
| nginx      | Reverse proxy, SSL termination, static files     | 80/443|
| django     | Main application server                          | 8000  |
| geoserver  | Spatial data server (WMS/WFS)                    | 8080  |
| postgres   | Database with PostGIS extension                  | 5432  |
| redis      | Celery message broker and caching                | 6379  |
| celery     | Background task processing                       | -     |

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Git

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd adma_geonode_project
   ```

2. Create environment file:
   ```bash
   cp adma_geo/.env.sample adma_geo/.env
   # Edit .env with your configuration
   ```

3. Build and start services:
   ```bash
   cd adma_geo
   docker-compose build
   docker-compose up -d
   ```

4. Run initial setup:
   ```bash
   docker-compose exec django python manage.py migrate
   docker-compose exec django python manage.py create_system_tools
   docker-compose exec django python manage.py createsuperuser
   ```

5. Access the application:
   - Main site: http://localhost/
   - GeoServer: http://localhost/geoserver/
   - Admin: http://localhost/admin/

## Configuration

### Environment Variables

Key environment variables in `.env`:

```bash
# Django
DEBUG=True
SECRET_KEY=your-secret-key
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
POSTGRES_DB=adma_db
POSTGRES_USER=adma_user
POSTGRES_PASSWORD=your-password

# GeoServer
GEOSERVER_URL=http://geoserver:8080/geoserver
GEOSERVER_ADMIN_USER=admin
GEOSERVER_ADMIN_PASSWORD=geoserver

# Third-Party APIs (optional)
REALM5_API_KEY=your-realm5-api-key
JOHNDEERE_CLIENT_ID=your-client-id
JOHNDEERE_CLIENT_SECRET=your-client-secret
```

### Timezone

The system uses `America/Chicago` timezone by default. Configure in `settings.py`:

```python
TIME_ZONE = 'America/Chicago'
```

## Third-Party Integrations

### John Deere Operations Center

Syncs field data, boundaries, and operations from John Deere.

**Setup:**
1. Register application at [John Deere Developer Portal](https://developer.deere.com/)
2. Configure OAuth2 credentials in `.env`
3. Set organization ID in settings

**Sync Schedule:** Daily at 3:00 AM

**Data Synced:**
- Field metadata and boundaries (as shapefiles)
- Field operations with boundaries
- Organization hierarchy

### Realm5 Weather Stations

Syncs weather observation data from Realm5 IoT sensors.

**Setup:**
1. Obtain API key from Realm5
2. Add `REALM5_API_KEY` to `.env`

**Sync Schedule:** Daily at 2:00 AM

**Data Synced:**
- Daily weather observations (JSON files)
- Aggregated daily averages (`all.json`)
- Variables: temperature, humidity, wind speed, dew point, etc.

**Manual Sync:**
```bash
docker-compose exec django python manage.py shell -c "
from filemanager.tasks import sync_realm5_task
sync_realm5_task()
"
```

## API Documentation

ADMA provides RESTful APIs with token-based authentication.

### Authentication

```bash
# Get auth token
curl -X POST http://localhost/api/auth/token/ \
  -d "username=your_username&password=your_password"

# Use token in requests
curl -H "Authorization: Token YOUR_TOKEN" \
  http://localhost/api/files/
```

### Key Endpoints

| Endpoint                          | Method | Description                    |
|-----------------------------------|--------|--------------------------------|
| `/api/auth/token/`                | POST   | Get authentication token       |
| `/api/files/`                     | GET    | List user's files              |
| `/api/files/upload/`              | POST   | Upload a file                  |
| `/api/files/<id>/download/`       | GET    | Download a file                |
| `/api/folders/`                   | GET    | List user's folders            |
| `/api/folders/<id>/download/`     | GET    | Download folder as ZIP         |

See `API_DOCUMENTATION.md` for complete API reference.

## Development

### Running Locally

```bash
cd adma_geo
docker-compose up -d

# View logs
docker-compose logs -f django

# Django shell
docker-compose exec django python manage.py shell

# Run tests
docker-compose exec django python manage.py test
```

### Code Structure

```
adma_geo/
├── adma_geo/           # Project settings
│   ├── settings.py
│   ├── urls.py
│   └── celery.py
├── filemanager/        # Main application
│   ├── models.py       # File, Folder, Map models
│   ├── views.py        # View functions
│   ├── tasks.py        # Celery tasks
│   ├── api_views.py    # REST API views
│   ├── realm5_client.py
│   └── johndeere_client.py
├── templates/          # HTML templates
├── static/             # Static assets
├── media/              # User uploads
└── docker-compose.yml
```

### Adding New Features

1. Create/modify models in `filemanager/models.py`
2. Run migrations: `docker-compose exec django python manage.py makemigrations && docker-compose exec django python manage.py migrate`
3. Add views in `filemanager/views.py`
4. Create templates in `templates/filemanager/`
5. Update URLs in `filemanager/urls.py`

## Deployment

### Production Setup

1. Update `.env` for production:
   ```bash
   DEBUG=False
   ALLOWED_HOSTS=your-domain.com
   ```

2. Configure SSL in Nginx

3. Build and deploy:
   ```bash
   docker-compose -f docker-compose.yml up -d --build
   ```

4. Collect static files:
   ```bash
   docker-compose exec django python manage.py collectstatic --noinput
   ```

### Backup

```bash
# Database backup
docker-compose exec postgres pg_dump -U adma_user adma_db > backup.sql

# Media files backup
tar -czvf media_backup.tar.gz adma_geo/media/
```

### Monitoring

- Check service status: `docker-compose ps`
- View logs: `docker-compose logs -f <service>`
- Celery tasks: Check Django admin or Celery logs

## License

ADMA 2021-2026. All rights reserved.

## Support

For questions or issues, contact the development team.
