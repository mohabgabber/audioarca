from datetime import timedelta
from pathlib import Path
import os
import secrets

from django.templatetags.static import static

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE_VALUES: dict[str, str] = {}
BOOLEAN_TRUE_VALUES = {"1", "true", "yes", "on"}
BOOLEAN_FALSE_VALUES = {"0", "false", "no", "off"}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        ENV_FILE_VALUES[key] = value
        os.environ.setdefault(key, value)


load_env_file(BASE_DIR / ".env")


def env(name: str, default=None, legacy_names: tuple[str, ...] = ()):
    for candidate in (name, *legacy_names):
        value = os.getenv(candidate)
        if value not in {None, ""}:
            return value
    return default


def env_bool(name: str, default: bool = False, legacy_names: tuple[str, ...] = ()) -> bool:
    value = env(name, legacy_names=legacy_names)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in BOOLEAN_TRUE_VALUES:
        return True
    if normalized in BOOLEAN_FALSE_VALUES:
        return False
    for candidate in (name, *legacy_names):
        if candidate in ENV_FILE_VALUES:
            fallback = ENV_FILE_VALUES[candidate].lower()
            if fallback in BOOLEAN_TRUE_VALUES:
                return True
            if fallback in BOOLEAN_FALSE_VALUES:
                return False
    return default


def env_int(name: str, default: int, legacy_names: tuple[str, ...] = ()) -> int:
    value = env(name, legacy_names=legacy_names)
    if value is None:
        return default
    return int(value)


def env_path(name: str, default: Path, legacy_names: tuple[str, ...] = ()) -> Path:
    value = env(name, legacy_names=legacy_names)
    return Path(value) if value else Path(default)


SECRET_KEY = env("DJANGO_SECRET_KEY", legacy_names=("SECRET_KEY",))
DEBUG = env_bool("DEBUG", default=False)
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = secrets.token_hex()
    else:
        raise ValueError("DJANGO_SECRET_KEY must be set when DEBUG is False")

APP_DOMAIN = env("APP_DOMAIN", default="localhost", legacy_names=("DOMAIN",))
SITE_ID = 1
ALLOWED_HOSTS = [host.strip() for host in env("ALLOWED_HOSTS", default="").split(",") if host.strip()]
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["*"] if DEBUG else [APP_DOMAIN]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.flatpages",
    "django.contrib.humanize",
    "corsheaders",
    "storages",
    "axes",
    "auditlog",
    "rest_framework",
    "allauth",
    "allauth.account",
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "unfold.contrib.import_export",
    "dash.apps.DashConfig",
    "forensics.apps.ForensicsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.sites.middleware.CurrentSiteMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.flatpages.middleware.FlatpageFallbackMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DATABASE_NAME", env("POSTGRES_DB", "forensics"), ("DB_NAME",)),
        "HOST": env("DATABASE_HOST", "postgres", ("DB_HOST",)),
        "PASSWORD": env("DATABASE_PASSWORD", env("POSTGRES_PASSWORD", "postgres"), ("DB_PASSWORD",)),
        "USER": env("DATABASE_USER", env("POSTGRES_USER", "postgres"), ("DB_USER",)),
        "PORT": env("DATABASE_PORT", "5432", ("DB_PORT",)),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = env_path("MEDIA_ROOT", BASE_DIR / "media")
STATIC_ROOT = env_path("STATIC_ROOT", BASE_DIR / "staticroot")
STATICFILES_DIRS = [BASE_DIR / "static"]
PRIVATE_MEDIA_ROOT = env_path("PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media")
CLOUDFRONT_DOMAIN = env("CLOUDFRONT_DOMAIN", "")
USE_S3_STORAGE = env_bool("USE_S3_STORAGE", default=False)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
if USE_S3_STORAGE:
    STORAGES = {
        "default": {
            "BACKEND": "core.storages_backend.MediaStorage",
            "OPTIONS": {
                "bucket_name": env("S3_BUCKET_NAME", ""),
                "use_ssl": True,
                "region_name": env("S3_REGION_NAME", ""),
                "access_key": env("S3_ACCESS_KEY", ""),
                "secret_key": env("S3_SECRET_KEY", ""),
                "custom_domain": CLOUDFRONT_DOMAIN or None,
            },
        },
        "staticfiles": {
            "BACKEND": "core.storages_backend.StaticStorage",
            "OPTIONS": {
                "bucket_name": env("S3_BUCKET_NAME", ""),
                "use_ssl": True,
                "region_name": env("S3_REGION_NAME", ""),
                "access_key": env("S3_ACCESS_KEY", ""),
                "secret_key": env("S3_SECRET_KEY", ""),
                "custom_domain": CLOUDFRONT_DOMAIN or None,
            },
        },
    }

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_EMAIL_CONFIRMATION_HMAC = True
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 2
ACCOUNT_SIGNUP_FIELDS = ["first_name*", "last_name*", "email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_EMAIL_SUBJECT_PREFIX = "Forensic Toolkit - "
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_DISPLAY = "dash.auth.user_display"
ACCOUNT_PRESERVE_USERNAME_CASING = False
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_LOGIN_ON_PASSWORD_RESET = False
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_RATE_LIMITS = {
    "change_password": "5/d/user",
    "manage_email": "2/m/user",
    "reset_password": "5/d/ip,5/d/key",
    "signup": "5/d/ip",
    "login": "10/d/ip",
    "login_failed": "3/5m/ip",
    "confirm_email": "1/5m/key",
}
ACCOUNT_FORMS = {
    "signup": "utilities.forms.user.CustomSignupForm",
}

AUTH_USER_MODEL = "dash.UserModel"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGIN_URL = "account_login"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "dash.auth.AuthBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5
AXES_LOCK_OUT_AT_FAILURE = True
AXES_COOLOFF_TIME = timedelta(hours=5)
AXES_RESET_ON_SUCCESS = True
AXES_ONLY_ADMIN_SITE = False
AXES_ENABLE_ADMIN = True
AXES_VERBOSE = True
AXES_LOCKOUT_PARAMETERS = ["ip_address", ["username", "user_agent"]]
AXES_USERNAME_FORM_FIELD = "login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "120/min",
    },
}

AUDITLOG_LOGENTRY_MODEL = "auditlog.LogEntry"
AUDITLOG_INCLUDE_ALL_MODELS = True

X_FRAME_OPTIONS = "DENY"
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [origin.strip() for origin in env("CORS_ALLOWED_ORIGINS", default="").split(",") if origin.strip()]
if not CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS = [f"http://{APP_DOMAIN}", f"https://{APP_DOMAIN}"]
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in env("CSRF_TRUSTED_ORIGINS", default="").split(",") if origin.strip()]
if not CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS = [f"http://{APP_DOMAIN}", f"https://{APP_DOMAIN}"]

EMAIL_HOST = env("EMAIL_HOST", "", ("SMTP_HOST",))
EMAIL_PORT = env_int("EMAIL_PORT", 587, ("SMTP_PORT",))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "", ("SMTP_USERNAME",))
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "", ("SMTP_PASSWORD",))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True, ("SMTP_USE_TLS",))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "forensics@example.com")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)

REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 50 * 60
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_BEAT_SCHEDULE_FILENAME = env(
    "CELERY_BEAT_SCHEDULE_FILENAME",
    str(BASE_DIR / ".runtime" / "celerybeat-schedule"),
)
CELERY_BEAT_SCHEDULE = {
    "cleanup-expired-invitations": {
        "task": "forensics.tasks.cleanup_expired_invitations",
        "schedule": timedelta(hours=6),
    },
    "mark-stuck-analysis-jobs": {
        "task": "forensics.tasks.mark_stuck_jobs",
        "schedule": timedelta(hours=1),
    },
}

OPENAI_API_KEY = env("OPENAI_API_KEY", "")
FORENSICS_REPORT_MODEL = env("OPENAI_MODEL", "gpt-5.4", ("FORENSICS_REPORT_MODEL",))
FORENSICS_ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3"}
FORENSICS_ALLOWED_AUDIO_MIME_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/x-mpeg",
    "audio/vnd.wave",
}
FORENSICS_INVITATION_EXPIRY_DAYS = 7
FORENSICS_MODEL_ROOT = env_path("FORENSICS_MODEL_ROOT", BASE_DIR / "model_assets")
FORENSICS_FASTTEXT_MODEL_PATH = env_path(
    "FORENSICS_FASTTEXT_MODEL_PATH",
    FORENSICS_MODEL_ROOT / "fasttext" / "lid.176.bin",
)
FORENSICS_WHISPER_MODEL_SIZE = env("FORENSICS_WHISPER_MODEL_SIZE", "small")
FORENSICS_SPEECHBRAIN_SOURCE = env(
    "FORENSICS_SPEECHBRAIN_SOURCE",
    "speechbrain/spkrec-ecapa-voxceleb",
)

FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

UNFOLD = {
    "SITE_TITLE": "Forensic Toolkit Administration",
    "SITE_HEADER": "Forensic Toolkit Administration",
    "SITE_URL": "/",
    "SITE_ICON": {
        "light": lambda request: static("dash/assets/images/logo-dark.png"),
        "dark": lambda request: static("dash/assets/images/logo-light.png"),
    },
    "SITE_LOGO": {
        "light": lambda request: static("dash/assets/images/logo-dark.png"),
        "dark": lambda request: static("dash/assets/images/logo-light.png"),
    },
    "SITE_SYMBOL": "monitoring",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "THEME": "dark",
    "COLORS": {
        "font": {
            "subtle-light": "107 114 128",
            "subtle-dark": "156 163 175",
            "default-light": "75 85 99",
            "default-dark": "209 213 219",
            "important-light": "17 24 39",
            "important-dark": "243 244 246",
        },
        "primary": {
            "50": "244 248 251",
            "100": "231 240 247",
            "200": "197 220 235",
            "300": "147 188 214",
            "400": "98 156 195",
            "500": "52 116 160",
            "600": "40 92 129",
            "700": "31 71 100",
            "800": "24 53 75",
            "900": "17 38 54",
            "950": "9 21 31",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
    },
}
