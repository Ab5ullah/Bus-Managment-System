"""
Django settings for Bus Ticket Management System (busms).
"""

from datetime import timedelta
from decimal import Decimal
import os
from pathlib import Path
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Add apps folder to Python path
sys.path.insert(0, str(BASE_DIR / 'apps'))

SECRET_KEY = os.getenv(
    'SECRET_KEY',
    'django-insecure-bus-ticket-management-system-secret-key-change-in-prod'
)

DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0,testserver').split(',') if h.strip()]
if 'testserver' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('testserver')

CSRF_TRUSTED_ORIGINS = [
    h.strip() for h in os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        'http://localhost:8000,http://127.0.0.1:8000,http://0.0.0.0:8000'
    ).split(',') if h.strip()
]

# Application definition
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_filters',
    'corsheaders',
    'drf_spectacular',
]

LOCAL_APPS = [
    'common.apps.CommonConfig',
    'accounts.apps.AccountsConfig',
    'fleet.apps.FleetConfig',
    'routes.apps.RoutesConfig',
    'trips.apps.TripsConfig',
    'search.apps.SearchConfig',
    'bookings.apps.BookingsConfig',
]

AUTH_USER_MODEL = 'accounts.User'

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

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

WSGI_APPLICATION = 'config.wsgi.application'

# Database configuration: PostgreSQL is the primary engine
DB_ENGINE = os.getenv('DB_ENGINE', 'postgresql').lower()

if DB_ENGINE in ('postgresql', 'postgres', 'psycopg2', 'psycopg'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', 'busms_db'),
            'USER': os.getenv('DB_USER', 'busms_user'),
            'PASSWORD': os.getenv('DB_PASSWORD', 'busms_password'),
            'HOST': os.getenv('DB_HOST', '127.0.0.1'),
            'PORT': os.getenv('DB_PORT', '5432'),
            'OPTIONS': {
                'connect_timeout': 3,
            },
            'CONN_MAX_AGE': 600,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Financial and Monetary Engine Configuration
TAX_RATE_PERCENTAGE = Decimal(os.getenv('TAX_RATE_PERCENTAGE', '5.00'))
SERVICE_FEE_FIXED = Decimal(os.getenv('SERVICE_FEE_FIXED', '50.00'))
CURRENCY_CODE = os.getenv('CURRENCY_CODE', 'PKR')

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Fast password hasher during test execution
if 'pytest' in sys.argv[0] or 'test' in sys.argv:
    PASSWORD_HASHERS = [
        'django.contrib.auth.hashers.MD5PasswordHasher',
    ]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static & Media files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS
CORS_ALLOW_ALL_ORIGINS = os.getenv('CORS_ALLOW_ALL_ORIGINS', 'True').lower() in ('true', '1')

# Django REST Framework Settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'common.authentication.CsrfExemptSessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
    'DEFAULT_PAGINATION_CLASS': 'common.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 20,
    'EXCEPTION_HANDLER': 'common.exceptions.custom_exception_handler',
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
}

# Spectacular OpenAPI Documentation Settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Bus Ticket Management System API',
    'DESCRIPTION': (
        'Enterprise REST API for Bus Ticket Management System: Fleet & Seat Layouts, '
        'Routes & Intermediate Stops, Overlap-Safe Scheduling, Concurrency-Safe Seat Locking, '
        'Pluggable Payments, QR Code Ticketing & Conductor Validation, and Tiered Cancellations.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': True,
    },
}

# SimpleJWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=int(os.getenv('ACCESS_TOKEN_LIFETIME_MINUTES', 60))),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=int(os.getenv('REFRESH_TOKEN_LIFETIME_DAYS', 7))),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}
