import os
from pathlib import Path

from corsheaders.defaults import default_headers
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-email-marketing-dev-key")

try:
    DEBUG = os.environ.get("production", "False") != "True"
except Exception:
    DEBUG = True

ALLOWED_HOSTS = ["*"]

CORS_ORIGIN_ALLOW_ALL = True
CORS_ALLOW_HEADERS = list(default_headers) + [
    "token",
    "authorization",
    "x-store-id",
    "x-client-id",
    "x-access-key",
    "User-Agent",
]
CORS_ORIGIN_WHITELIST = [
    'http://localhost:3000',
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "Accounts",
    "EmailMarketing",
]

AUTH_USER_MODEL = "Accounts.User"

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "email_marketing_ms_django_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "email_marketing_ms_django_backend.wsgi.application"

# Use SQLite for development (no MySQL server needed)
# For production, configure MySQL in .env with DATABASE_TYPE=mysql

print(os.environ.get("DATABASE_TYPE"),
      os.environ.get("dbName", "email_marketing_db"), os.environ.get("username", "root"),
      os.environ.get("password", ""))
if os.environ.get("DATABASE_TYPE") == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("dbName", "email_marketing_db"),
            "USER": os.environ.get("username", "root"),
            "HOST": os.environ.get("hostName", "localhost"),
            "PASSWORD": os.environ.get("password", ""),
            "PORT": os.environ.get("port", "3306"),
        }
    }
else:
    # Default to SQLite for local development
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = False

STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
}

EMAIL_MARKETING_BATCH_SIZE = int(os.environ.get("EMAIL_MARKETING_BATCH_SIZE", 100))
EMAIL_MARKETING_BATCH_SLEEP = float(os.environ.get("EMAIL_MARKETING_BATCH_SLEEP", 0.5))
EMAIL_MARKETING_PUBLIC_URL = os.environ.get("EMAIL_MARKETING_PUBLIC_URL", "")
