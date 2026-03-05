import os
from pathlib import Path

try:
    import dj_database_url
except ImportError:  # pragma: no cover - fallback para ambiente sem dependencias de deploy instaladas
    dj_database_url = None

try:
    import whitenoise  # noqa: F401
    USE_WHITENOISE = True
except ImportError:  # pragma: no cover - fallback para ambiente sem dependencias de deploy instaladas
    USE_WHITENOISE = False

BASE_DIR = Path(__file__).resolve().parent.parent


def _csv_env(name: str, default: str = '') -> list[str]:
    raw_value = os.getenv(name, default)
    return [item.strip() for item in raw_value.split(',') if item.strip()]


SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'dev-only-change-me')
DEBUG = os.getenv('DJANGO_DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = _csv_env('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
CSRF_TRUSTED_ORIGINS = _csv_env('DJANGO_CSRF_TRUSTED_ORIGINS', '')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'inspecoes',
]
if USE_WHITENOISE:
    INSTALLED_APPS.insert(0, 'whitenoise.runserver_nostatic')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'inspecoes.middleware.PortalAdminGuardMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
if USE_WHITENOISE:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

ROOT_URLCONF = 'verificacao_web.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'inspecoes.context_processors.portal_user_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'verificacao_web.wsgi.application'
ASGI_APPLICATION = 'verificacao_web.asgi.application'

database_url = os.getenv('DATABASE_URL', '').strip()
if database_url and dj_database_url:
    DATABASES = {
        'default': dj_database_url.parse(
            database_url,
            conn_max_age=600,
            ssl_require=not DEBUG,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Belem'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
if USE_WHITENOISE:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv('DJANGO_SECURE_SSL_REDIRECT', 'true').lower() == 'true'
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

SAP_API_BASE_URL = os.getenv('SAP_API_BASE_URL', '').rstrip('/')
SAP_API_ATTACH_ENDPOINT = os.getenv('SAP_API_ATTACH_ENDPOINT', '/maintenance-orders/attachments')
SAP_API_TOKEN = os.getenv('SAP_API_TOKEN', '')
SAP_VERIFY_SSL = os.getenv('SAP_VERIFY_SSL', 'true').lower() == 'true'
HYDRO_LOGO_PATH = os.getenv('HYDRO_LOGO_PATH', '')
EQUIPMENT_DUE_SOON_DAYS = int(os.getenv('EQUIPMENT_DUE_SOON_DAYS', '7'))

EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '25'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'false').lower() == 'true'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'false').lower() == 'true'
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'nao-responda@empresa.local')
PORTAL_NOTIFY_EMAIL_ENABLED = os.getenv('PORTAL_NOTIFY_EMAIL_ENABLED', 'false')
