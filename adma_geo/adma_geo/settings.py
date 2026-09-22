import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-this-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0', 'adma.unl.edu', '*']

# CSRF and CORS settings for production
CSRF_TRUSTED_ORIGINS = [
    'https://adma.unl.edu',
    'http://adma.unl.edu',
    'http://localhost',
    'https://localhost',
]

# Proxy settings for HTTPS detection
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # 'django.contrib.gis',  # GeoDjango support (disabled for now)

    # Third party apps
    'crispy_forms',
    'crispy_bootstrap5',
    'rest_framework',
    'rest_framework.authtoken',
    'rest_framework_simplejwt',

    # Local apps
    'filemanager',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'adma_geo.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'adma_geo.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',  # Regular PostgreSQL backend
        'NAME': os.environ.get('POSTGRES_DB', 'adma_geo'),
        'USER': os.environ.get('POSTGRES_USER', 'adma_geo'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'adma_geo123'),
        'HOST': os.environ.get('POSTGRES_HOST', 'db'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Chicago'  # Central Time (Nebraska)
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication settings
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Celery Configuration
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# Celery Beat Schedule (for periodic tasks)
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'sync-realm5-daily': {
        'task': 'filemanager.tasks.sync_realm5_task',
        'schedule': crontab(hour=2, minute=0),  # Run at 2:00 AM daily
    },
    'sync-johndeere-daily': {
        'task': 'filemanager.tasks.sync_johndeere_task',
        'schedule': crontab(hour=3, minute=0),  # Run at 3:00 AM daily
    },
    # The ADAPT permission model is only as fresh as these two. Folder ACLs
    # change when SNR18 admins change them; group membership changes in AD.
    'sync-adapt-acls-daily': {
        'task': 'filemanager.tasks.sync_adapt_acls_task',
        'schedule': crontab(hour=4, minute=0),
    },
    'refresh-directory-identities-daily': {
        'task': 'filemanager.tasks.refresh_directory_identities_task',
        'schedule': crontab(hour=4, minute=30),
    },
}

# Realm5 API Configuration
# API key should be set via environment variable (loaded from .env file)
REALM5_API_KEY = os.environ.get('REALM5_API_KEY')

# John Deere API Configuration
# Credentials should be set via environment variables
JD_CLIENT_ID = os.environ.get('JD_CLIENT_ID')
JD_CLIENT_SECRET = os.environ.get('JD_CLIENT_SECRET')
JD_REFRESH_TOKEN = os.environ.get('JD_REFRESH_TOKEN')
JD_ORG_ID = os.environ.get('JD_ORG_ID', '4193081')  # Default organization ID

# File Upload Settings
FILE_UPLOAD_MAX_MEMORY_SIZE = 500 * 1024 * 1024  # 500MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 500 * 1024 * 1024  # 500MB
DATA_UPLOAD_MAX_NUMBER_FILES = 1000  # Allow up to 1000 files per upload for folder uploads

# GeoServer Configuration
GEOSERVER_URL = os.environ.get('GEOSERVER_URL', 'http://geoserver:8080/geoserver')
GEOSERVER_ADMIN_USER = os.environ.get('GEOSERVER_ADMIN_USER', 'admin')
GEOSERVER_ADMIN_PASSWORD = os.environ.get('GEOSERVER_ADMIN_PASSWORD', 'geoserver123')
GEOSERVER_WORKSPACE = 'adma_geo'

# Supported GIS file formats
# GIS file extensions that should be automatically processed and published to GeoServer
GIS_FILE_EXTENSIONS = [
    '.geojson', '.shp', '.tiff', '.tif', '.geotiff', '.geotif'
]

# All spatial file extensions (including ones that don't auto-process)
ALL_SPATIAL_EXTENSIONS = [
    '.gpkg', '.geojson', '.shp', '.kml', '.kmz',
    '.tiff', '.tif', '.geotiff', '.geotif', '.zip'
]


# ADAPT warehouse share: where ADMA reads folder security descriptors from.
# The mount in docker-compose.adapt.yml carries the bytes; these settings let
# sync_adapt_acls open a second, metadata-only SMB session to read each
# folder's DACL. Same account as the mount.
ADAPT_MOUNT = os.environ.get('ADAPT_MOUNT', '/adapt')
ADAPT_SMB_HOST = os.environ.get('ADAPT_HOST', 'snr18')
ADAPT_SMB_SHARE = os.environ.get('ADAPT_SHARE', 'Adapt')
_adapt_domain = os.environ.get('ADAPT_DOMAIN', '')
_adapt_user = os.environ.get('ADAPT_USER', '')
# smbprotocol takes DOMAIN\\user or user@domain; the mount takes them separately.
ADAPT_SMB_USERNAME = _adapt_user
if _adapt_domain and _adapt_user and '\\' not in _adapt_user and '@' not in _adapt_user:
    ADAPT_SMB_USERNAME = f'{_adapt_domain}\\{_adapt_user}'
ADAPT_SMB_PASSWORD = os.environ.get('ADAPT_PASS', '')
ADAPT_SMB_PORT = int(os.environ.get('ADAPT_SMB_PORT', '445'))
ADAPT_SMB_TIMEOUT = int(os.environ.get('ADAPT_SMB_TIMEOUT', '30'))

# Active Directory login (django-auth-ldap). Off unless LDAP_URI is set, so a
# development stack without a domain keeps plain Django accounts.
AUTH_LDAP_SERVER_URI = os.environ.get('LDAP_URI', '')
if AUTH_LDAP_SERVER_URI:
    import ldap
    from django_auth_ldap.config import ActiveDirectoryGroupType, LDAPSearch

    AUTHENTICATION_BACKENDS = [
        'django_auth_ldap.backend.LDAPBackend',
        'django.contrib.auth.backends.ModelBackend',  # local superuser keeps working
    ]
    _ldap_base = os.environ.get('LDAP_BASE_DN', '')
    AUTH_LDAP_BIND_DN = os.environ.get('LDAP_BIND_DN', '') or (
        f'{_adapt_user}@{os.environ.get("LDAP_UPN_SUFFIX", "")}' if os.environ.get('LDAP_UPN_SUFFIX') else _adapt_user
    )
    AUTH_LDAP_BIND_PASSWORD = os.environ.get('LDAP_BIND_PASSWORD', '') or ADAPT_SMB_PASSWORD
    AUTH_LDAP_USER_SEARCH = LDAPSearch(_ldap_base, ldap.SCOPE_SUBTREE, '(sAMAccountName=%(user)s)')
    AUTH_LDAP_USER_ATTR_MAP = {'first_name': 'givenName', 'last_name': 'sn', 'email': 'mail'}
    AUTH_LDAP_GROUP_SEARCH = LDAPSearch(_ldap_base, ldap.SCOPE_SUBTREE, '(objectClass=group)')
    AUTH_LDAP_GROUP_TYPE = ActiveDirectoryGroupType(name_attr='cn')
    AUTH_LDAP_MIRROR_GROUPS = os.environ.get('LDAP_MIRROR_GROUPS', 'True').lower() == 'true'
    AUTH_LDAP_ALWAYS_UPDATE_USER = True
    _require_group = os.environ.get('LDAP_REQUIRE_GROUP', '')
    if _require_group:
        AUTH_LDAP_REQUIRE_GROUP = _require_group
    AUTH_LDAP_CONNECTION_OPTIONS = {
        ldap.OPT_REFERRALS: 0,  # AD returns referrals that python-ldap cannot chase
    }
    _ldap_ca = os.environ.get('LDAP_CA_CERT_FILE', '')
    if _ldap_ca:
        AUTH_LDAP_CONNECTION_OPTIONS[ldap.OPT_X_TLS_CACERTFILE] = _ldap_ca
        AUTH_LDAP_CONNECTION_OPTIONS[ldap.OPT_X_TLS_NEWCTX] = 0
    AUTH_LDAP_START_TLS = os.environ.get('LDAP_START_TLS', 'False').lower() == 'true'

# Proxy Settings for HTTPS detection
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Django REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',  # Keep for web interface
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
}

# ChromaDB and Embedding Settings
# NOTE: ChromaDB and embedding settings removed
# Search now uses PostgreSQL text matching instead of semantic embeddings

