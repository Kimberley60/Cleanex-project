from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from decouple import config, Csv
from django.core.exceptions import ImproperlyConfigured


# =========================================================
# BASE DIRECTORY
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# =========================================================
# SECURITY AND ENVIRONMENT SETTINGS
# =========================================================

# Always provide a strong SECRET_KEY through the environment
# in production. The fallback is for local development only.
SECRET_KEY = config(
    "SECRET_KEY",
    default="django-insecure-change-this-for-local-development",
)

# DEBUG should be True locally and False in production.
DEBUG = config(
    "DEBUG",
    default=True,
    cast=bool,
)

if not DEBUG and SECRET_KEY == "django-insecure-change-this-for-local-development":
    raise ImproperlyConfigured(
        "SECRET_KEY must be set through the environment when DEBUG=False."
    )

# Read allowed hosts from .env.
# The fallback supports local development only.
ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1,[::1]",
    cast=Csv(),
)

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="",
    cast=Csv(),
)

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "customer_profile"
LOGOUT_REDIRECT_URL = "home"


# =========================================================
# EMAIL
# =========================================================

EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=False, cast=bool)
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=10, cast=int)
DEFAULT_FROM_EMAIL = config(
    "DEFAULT_FROM_EMAIL",
    default="CLEANEX <no-reply@gmail.com>",
)
CONTACT_RECIPIENT_EMAIL = config(
    "CONTACT_RECIPIENT_EMAIL",
    default="",
)


# =========================================================
# M-PESA / DARAJA CONFIGURATION
# =========================================================

MPESA_CONSUMER_KEY = config("MPESA_CONSUMER_KEY", default="")
MPESA_CONSUMER_SECRET = config("MPESA_CONSUMER_SECRET", default="")
MPESA_SHORTCODE = config("MPESA_SHORTCODE", default="")
MPESA_PASSKEY = config("MPESA_PASSKEY", default="")
MPESA_CALLBACK_URL = config("MPESA_CALLBACK_URL", default="")
MPESA_ENVIRONMENT = config("MPESA_ENVIRONMENT", default="sandbox")


# =========================================================
# CLOUDINARY MEDIA STORAGE
# =========================================================

CLOUDINARY_CLOUD_NAME = config("CLOUDINARY_CLOUD_NAME", default="")
CLOUDINARY_API_KEY = config("CLOUDINARY_API_KEY", default="")
CLOUDINARY_API_SECRET = config("CLOUDINARY_API_SECRET", default="")

_cloudinary_values = (
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
)

if any(_cloudinary_values) and not all(_cloudinary_values):
    raise ImproperlyConfigured(
        "Cloudinary media storage requires CLOUDINARY_CLOUD_NAME, "
        "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
    )

CLOUDINARY_CONFIGURED = all(_cloudinary_values)




# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'cleanexapp',
    'apps_core.services',
    'apps_core.bookings',
    'apps_core.core',
    'apps_core.customers',
    'apps_core.staff',
    'apps_core.quotes',
    'apps_core.notifications.apps.NotificationsConfig',
    'apps_core.gallery',
    'apps_core.website',
    'apps_core.payments',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
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
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps_core.website.context_processors.website_content',
            ],
        },
    },
]


WSGI_APPLICATION = 'config.wsgi.application'


# Database

# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASE_URL = config("DATABASE_URL", default="")

if DATABASE_URL:
    parsed_database_url = urlparse(DATABASE_URL)

    if (
        parsed_database_url.scheme not in {"postgres", "postgresql"}
        or not parsed_database_url.hostname
        or not parsed_database_url.path.lstrip("/")
    ):
        raise ImproperlyConfigured(
            "DATABASE_URL must be a PostgreSQL connection URL."
        )

    database_query = parse_qs(parsed_database_url.query)
    database_options = {}
    sslmode = database_query.get("sslmode", [None])[0]
    if sslmode:
        database_options["sslmode"] = sslmode

    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(parsed_database_url.path.lstrip("/")),
            'USER': unquote(parsed_database_url.username or ""),
            'PASSWORD': unquote(parsed_database_url.password or ""),
            'HOST': parsed_database_url.hostname,
            'PORT': str(parsed_database_url.port or 5432),
            'OPTIONS': database_options,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config("DB_NAME", default="cleanex_db"),
            'USER': config("DB_USER", default="cleanex_user"),
            'PASSWORD': config("DB_PASSWORD", default=""),
            'HOST': config("DB_HOST", default="localhost"),
            'PORT': config("DB_PORT", default="5432"),
        }
    }


# Password validation

# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

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

# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Africa/Nairobi'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)

# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"


MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / "media"


# Django 6 storage backends. Cloudinary is used for uploaded media only
# when all required credentials are configured; static files remain local.
STATICFILES_STORAGE_BACKEND = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
    if not DEBUG
    else "django.contrib.staticfiles.storage.StaticFilesStorage"
)

if CLOUDINARY_CONFIGURED:
    STORAGES = {
        "default": {
            "BACKEND": "apps_core.core.storage.CloudinaryMediaStorage",
        },
        "staticfiles": {
            "BACKEND": STATICFILES_STORAGE_BACKEND,
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": STATICFILES_STORAGE_BACKEND,
        },
    }

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = config(
        "SECURE_SSL_REDIRECT",
        default=True,
        cast=bool,
    )
    SESSION_COOKIE_SECURE = config(
        "SESSION_COOKIE_SECURE",
        default=True,
        cast=bool,
    )
    CSRF_COOKIE_SECURE = config(
        "CSRF_COOKIE_SECURE",
        default=True,
        cast=bool,
    )
    SECURE_HSTS_SECONDS = config(
        "SECURE_HSTS_SECONDS",
        default=31536000,
        cast=int,
    )
    SECURE_HSTS_INCLUDE_SUBDOMAINS = config(
        "SECURE_HSTS_INCLUDE_SUBDOMAINS",
        default=True,
        cast=bool,
    )
    SECURE_HSTS_PRELOAD = config(
        "SECURE_HSTS_PRELOAD",
        default=True,
        cast=bool,
    )
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = config(
        "SECURE_REFERRER_POLICY",
        default="same-origin",
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "apps_core": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

SESSION_COOKIE_HTTPONLY = True
