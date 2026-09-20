import sys
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

ENVIRONMENT = config("ENVIRONMENT", default="development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"

# Mirrors the DEV_DB_*/PROD_DB_* pattern: dev gets a convenient default,
# production has none and fails fast at startup if it isn't set for real.
SECRET_KEY = (
    config("PROD_DJANGO_SECRET_KEY")
    if IS_PRODUCTION
    else config("DEV_DJANGO_SECRET_KEY", default="dev-secret-key-not-for-production-use-only")
)
DEBUG = config("DJANGO_DEBUG", cast=bool, default=not IS_PRODUCTION)
TESTING = "test" in sys.argv

ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", cast=Csv(), default="localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = config(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    cast=Csv(),
    default="http://127.0.0.1:8000,http://localhost:8000",
)

# Render exposes the service's public hostname; trust it automatically.
_RENDER_HOST = config("RENDER_EXTERNAL_HOSTNAME", default="")
if _RENDER_HOST:
    if _RENDER_HOST not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_RENDER_HOST)
    if f"https://{_RENDER_HOST}" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://{_RENDER_HOST}")

SITE_NAME = "Business Analytics Intelligence"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.organizations",
    "apps.accounts",
    "apps.billing",
    "apps.finance",
    "apps.core",  # provides the `humanize` template library (Indian digit grouping)
    "apps.superadmin",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.organizations.middleware.TenantSchemaMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.site_meta",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ENVIRONMENT selects which credential set "default" connects with, so one
# .env can point the whole app at a dev database or a prod database without
# touching any code. The "dev"/"prod" aliases below always point at BOTH
# databases regardless of ENVIRONMENT — the superadmin dashboard needs to
# see organizations in both at once, which "default" alone can't do.
_DB_PREFIX = "PROD_DB" if IS_PRODUCTION else "DEV_DB"


def _db_config(prefix, default_name):
    cfg = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config(f"{prefix}_NAME", default=default_name),
        "USER": config(f"{prefix}_USER", default="postgres"),
        "PASSWORD": config(f"{prefix}_PASSWORD", default="postgres"),
        "HOST": config(f"{prefix}_HOST", default="localhost"),
        "PORT": config(f"{prefix}_PORT", default="5432"),
        "OPTIONS": {},
        "CONN_MAX_AGE": config(f"{prefix}_CONN_MAX_AGE", cast=int, default=60),
        "CONN_HEALTH_CHECKS": True,
    }
    sslmode = config(f"{prefix}_SSLMODE", default="")
    if sslmode:
        cfg["OPTIONS"]["sslmode"] = sslmode
    return cfg


DATABASES = {
    "default": _db_config(_DB_PREFIX, "bai_dev"),
    "dev": _db_config("DEV_DB", "bai_dev"),
    "prod": _db_config("PROD_DB", "bai_prod"),
}

# "default" is always a duplicate of whichever of "dev"/"prod" ENVIRONMENT
# selects — same real database, just addressed under two alias names. Under
# `manage.py test`, Django opens every alias as its own connection/
# transaction regardless, so without this, writes made via .using("dev")
# would be invisible to a plain (default-alias) read in the same test —
# e.g. auth middleware's user lookup, which never takes an explicit alias.
# MIRROR makes the matching alias reuse "default"'s actual test connection;
# the other one keeps its own independent test database.
DATABASES["prod" if IS_PRODUCTION else "dev"]["TEST"] = {"MIRROR": "default"}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = config("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if IS_PRODUCTION
            else "whitenoise.storage.CompressedStaticFilesStorage"
        )
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "finance:dashboard"
LOGOUT_REDIRECT_URL = "core:landing"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
    ),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = config("SESSION_COOKIE_AGE", cast=int, default=60 * 60 * 12)

_SSL_ENABLED = config("DJANGO_SECURE_SSL", cast=bool, default=not DEBUG and not TESTING)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _SSL_ENABLED
SESSION_COOKIE_SECURE = _SSL_ENABLED
CSRF_COOKIE_SECURE = _SSL_ENABLED
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Only meaningful once the whole site is actually served over HTTPS (true in
# production, behind the reverse proxy that sets SECURE_PROXY_SSL_HEADER).
# Starts at 1 hour so a misconfiguration doesn't lock out browsers for long;
# raise DJANGO_HSTS_SECONDS once HTTPS is confirmed solid in production.
SECURE_HSTS_SECONDS = config("DJANGO_HSTS_SECONDS", cast=int, default=3600 if _SSL_ENABLED else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _SSL_ENABLED
SECURE_HSTS_PRELOAD = _SSL_ENABLED

# Razorpay — Pay Now on the Billing page. Empty by default so the app
# still boots without them; RazorpayClient.is_configured() gates the
# checkout flow, and the button is hidden entirely until it's set. Get
# these from the Razorpay dashboard (Settings -> API Keys, and
# Settings -> Webhooks for the webhook secret, pointed at
# /billing/webhooks/razorpay/).
RAZORPAY_KEY_ID = config("RAZORPAY_KEY_ID", default="")
RAZORPAY_KEY_SECRET = config("RAZORPAY_KEY_SECRET", default="")
RAZORPAY_WEBHOOK_SECRET = config("RAZORPAY_WEBHOOK_SECRET", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {process:d} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": config("DJANGO_LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
