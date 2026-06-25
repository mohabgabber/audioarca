# AudioArca

Server-rendered Django toolkit for forensic phonetics and forensic linguistics. The app keeps the dashboard shell from `dash`, adds case and evidence workflows in `forensics`, and uses Celery for background analysis and mandatory report generation.

The default UX is standard Django templates, forms, and views. HTMX is used only as progressive enhancement for dashboard fragments and case progress refreshes. Full-page navigation and form submissions remain the primary interaction model.

## Architecture

- `core`
  Django settings, URL routing, WSGI/ASGI entrypoints, Celery bootstrap, storage configuration.
- `dash`
  Authentication, dashboard shell, user settings, invitation onboarding, theme persistence, admin-facing user management.
- `forensics`
  Cases, artifacts, analysis jobs, phonetic and linguistic evidence workflows, reports, monitoring, and case detail workspaces.

## Main Features

- Normal multipage Django UI for dashboard, case intake, case review, reports, users, and settings.
- Phonetic workflow with `.wav` / `.mp3` validation, upload progress, immutable evidence storage, hashing, transcripts, acoustic features, warnings, and report generation.
- Linguistic workflow with hashed text ingest, stylometric feature storage, warnings, calibrated results, and report generation.
- Versioned report generation with OpenAI narrative drafting, HTML rendering, PDF generation, immutable report artifacts, and secure downloads.
- Background processing via Celery worker and beat.
- Dashboard monitoring for case counts, failures, queue state, and integrity.

## Public Repository Safety

- `.env`, virtual environments, uploaded evidence, private media, generated reports, model assets, generated static output, and local sample recordings are ignored and should remain local only.
- `example.env` is the committed environment template. Copy it to `.env` for local or deployment-specific values.
- Do not commit case evidence, human voice recordings, transcripts, downloaded model weights, generated PDFs, or credentials unless every file has explicit public redistribution rights and has been reviewed for identifying information.
- Raw files under `samples/` are ignored by default. Keep only documentation and sanitized metadata there unless the sample data is explicitly licensed for public release.
- Review third-party dashboard/static asset licenses before the first public push. The repository vendors UI libraries, fonts, images, and template assets under `static/dash/assets`.

## Runtime Stack

- Web: Django 6
- Database: PostgreSQL `18.2-alpine3.22`
- Broker/result backend: Redis
- Background jobs: Celery worker and Celery beat
- Monitoring: Flower (optional `ops` profile)
- PDF rendering: WeasyPrint

## Environment Files

The repo commits `example.env` only. Create `.env` locally from that template and replace placeholders before running the app:

```bash
cp example.env .env
```

`core/settings.py` now auto-loads `.env` on startup, so local shell runs and Docker Compose use the same variable names.

### Required environment variables

- `DJANGO_SECRET_KEY`
- `DEBUG`
- `APP_DOMAIN`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `CORS_ALLOWED_ORIGINS`
- `TIME_ZONE`
- `DATABASE_NAME`
- `DATABASE_USER`
- `DATABASE_PASSWORD`
- `DATABASE_HOST`
- `DATABASE_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CELERY_BEAT_SCHEDULE_FILENAME`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `DEFAULT_FROM_EMAIL`
- `MEDIA_ROOT`
- `STATIC_ROOT`
- `PRIVATE_MEDIA_ROOT`
- `FORENSICS_MODEL_ROOT`
- `FORENSICS_FASTTEXT_MODEL_PATH`
- `FORENSICS_WHISPER_MODEL_SIZE`
- `FORENSICS_SPEECHBRAIN_SOURCE`

### Optional S3 variables

- `USE_S3_STORAGE`
- `S3_BUCKET_NAME`
- `S3_REGION_NAME`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
- `CLOUDFRONT_DOMAIN`

S3 is opt-in. Filesystem storage is the default for local and Compose usage.

## Local Run

1. Copy `example.env` to `.env` and replace placeholders for your machine.
2. Create and activate a virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Ensure PostgreSQL and Redis are available.
5. Run migrations:

```bash
python manage.py migrate
```

6. Collect static files:

```bash
python manage.py collectstatic --noinput
```

7. Start the web server:

```bash
python manage.py runserver
```

8. Start the worker:

```bash
celery -A core worker -l info
```

9. Start beat:

```bash
celery -A core beat -l info --schedule=.runtime/celerybeat-schedule
```

10. Optional: start Flower:

```bash
celery -A core flower --port=5555
```

## Docker Compose

Development stack:

```bash
docker compose -f dev-compose.yml up --build
```

The development stack:

- runs Django with `DEBUG=true`
- skips `collectstatic`
- bind-mounts the repository into `/app` for code hot reload on the `web` service

Production-like stack:

```bash
docker compose -f docker-compose.yml up --build
```

Included services:

- `web`
- `postgres`
- `redis`
- `celery_worker`
- `celery_beat`
- `flower` via the optional `ops` profile

Healthchecks are configured for the web server, PostgreSQL, Redis, worker, beat, and Flower.

To start Flower in Compose:

```bash
docker compose -f dev-compose.yml --profile ops up flower
docker compose -f docker-compose.yml --profile ops up flower
```

## GitHub Actions

- `.github/workflows/ci.yml` runs linting, migration checks, Django checks, tests, static collection, and Compose validation on pull requests and pushes to `main` or `codex/**`.
- `.github/workflows/workflow.yaml` publishes GHCR images on pushes to `main`: `audioarca`, `audioarca-web`, `audioarca-celery-worker`, `audioarca-celery-beat`, and `audioarca-flower`.
- `.github/workflows/docker-build.yml` validates Docker builds and can optionally publish `audioarca` to Docker Hub when `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` repository secrets are configured.
- `.github/dependabot.yml` checks Python dependencies, GitHub Actions, and the Docker base image weekly.

## Reports

Report generation is mandatory once structured evidence exists.

Flow:

1. Analysis pipeline stores structured evidence in `AnalysisResult`.
2. `ensure_report_version()` assembles a deterministic prompt from stored evidence.
3. OpenAI generates the narrative text.
4. The HTML report is rendered server-side.
5. WeasyPrint generates the PDF artifact.
6. A new immutable `ReportVersion` row is stored.
7. The version is downloadable from the case workspace and report detail page.

Report filenames follow this pattern:

```text
<slugified-case-name>-<report-number>-v<version>.pdf
```

## Progress Tracking

- Case creation queues Celery work with `transaction.on_commit`, so workers do not start before the database commit is safe.
- `AnalysisJob.is_current` is rotated explicitly.
- `JobTracker` centralizes job status, progress, retry, failure, success, and cancellation updates.
- Dashboard and case detail fragments poll for updated status via HTMX, but core navigation still works without it.

## Users, Roles, Invitations, and Theme

Roles:

- `Admin`
- `Analyst`
- `Reviewer`
- `Viewer`

Permissions:

- All authenticated staff can view cases, reports, and artifact downloads.
- All authenticated staff can create phonetic and linguistic cases.
- All authenticated staff can perform review actions.
- `Admin` manages users and invitations.

Invitation flow:

1. Admin creates an invitation in User Management.
2. A tokenized acceptance URL is generated.
3. Invitee sets a password and activates the account.
4. Expired invitations automatically transition to `Expired`.

Theme persistence:

- Theme changes are saved through `api/user/settings/theme/`.
- Preference is stored on `dash.UserModel.theme_preference`.

## Test and Verification Commands

```bash
python -m ruff check .
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py check --deploy --fail-level ERROR
python manage.py test dash.tests forensics.tests
python manage.py collectstatic --noinput
docker compose -f docker-compose.yml config
docker compose -f dev-compose.yml config
```

## Manual QA Checklist

- Sign in and confirm dashboard, settings, reports, and case lists load without frontend errors.
- Create a phonetic case with valid `.wav` or `.mp3` files and confirm upload progress, redirect, queued state, and artifact hashing.
- Confirm invalid phonetic uploads show validation errors and do not create a case.
- Create a linguistic case and confirm text hashes, queued state, and redirect to case detail.
- Open case detail and confirm evidence, transcripts, features, warnings, report versions, task progress, and audit trail are readable in both light and dark themes.
- Confirm HTMX fragments refresh status and report panels without breaking full-page navigation.
- Regenerate a report and confirm a new report version is created.
- Download a report PDF and verify filename and rendering.
- Confirm reviewer approval is blocked until a ready report exists.
- Confirm viewer accounts can create and review cases, but cannot access user management.
- Create, revoke, accept, and expire invitations.
- Verify theme preference persists after refresh and re-login.
- Start the Compose stack and confirm `web`, `postgres`, `redis`, `celery_worker`, and `celery_beat` stay healthy.
- If you explicitly start the `ops` profile, confirm `flower` is healthy as well.

## Known Limitations

- Real phonetic and linguistic pipelines depend on heavyweight local ML and audio libraries and appropriate model assets.
- Report generation requires a valid `OPENAI_API_KEY`; tests mock that dependency and do not perform live model calls.
- Cancellation is persisted immediately and a revoke signal is sent when a Celery task id exists, but already-running long native processing steps still depend on cooperative cancellation points inside the pipeline.

## License

No project license has been selected yet. Add a `LICENSE` file before public release if the project should grant explicit reuse rights.
