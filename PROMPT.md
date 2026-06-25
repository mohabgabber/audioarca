# AudioArca Application Prompt

You are working on AudioArca, a server-rendered Django application for forensic phoneticians and forensic linguists. The app helps specialists create, track, analyze, review, and report on forensic comparison cases involving audio samples and text samples.

## Product Goal

Build and maintain a forensic analysis toolkit that supports structured case workflows for identifying and comparing speakers or authors. The application should preserve evidence integrity, expose measurable forensic signals, support review decisions, and generate traceable report versions.

## Preferred Stack

Prefer this stack unless there is a strong reason to deviate:

- Docker and Docker Compose, with Docker Swarm as the preferred orchestration target when swarm deployment is needed.
- Python and Django for the web application.
- PostgreSQL for primary persistence.
- Redis and Celery for background processing.
- GitHub Actions for CI and image publishing.
- Go only for clearly isolated tooling or services where it is a better fit than Python.

## Core Users

- Analysts create phonetic and linguistic cases, upload evidence, monitor pipeline progress, retry failed analysis, and regenerate reports.
- Reviewers inspect structured evidence, report readiness, audit trails, and final case disposition.
- Admins manage invitations, users, account state, and roles.
- Viewers can access case workflows and review-ready case information according to the current product policy.

## Main Workflows

- Phonetic analysis accepts two WAV or MP3 samples, validates MIME type and extension, stores immutable originals with SHA-256 hashes, normalizes audio, extracts acoustic features, transcribes speech, compares speaker signals, calibrates a score, and generates a mandatory report.
- Linguistic analysis accepts questioned and known text samples, stores hashes and normalized text, extracts stylometric features, compares authorship signals, calibrates a score, and generates a mandatory report.
- Case detail pages show live job status, evidence artifacts, extracted features, transcripts, warnings, report versions, and review actions.
- Reports are versioned, generated from stored structured evidence, rendered as HTML, exported to PDF, and downloaded through authenticated views.
- Dashboard pages summarize case counts, queue health, failures, integrity records, and adverse-condition flags.

## Runtime Stack

- Python 3.12 in Docker.
- Django 6 with server-rendered templates.
- PostgreSQL 18 for the primary database.
- Redis for Celery broker and result backend.
- Celery worker and beat for analysis, reporting, cleanup, and stuck-job maintenance.
- Gunicorn for production web serving.
- WeasyPrint for PDF report output.
- OpenAI report narrative drafting, defaulting to `gpt-5.4` unless overridden by `OPENAI_MODEL`.
- Local ML/audio tooling includes faster-whisper, fastText, SpeechBrain, torch, TorchCodec, torchaudio, librosa, openSMILE, Parselmouth, scipy, scikit-learn, numpy, and pandas.

## Important Implementation Rules

- Keep evidence artifacts immutable and private. Store hashes, original filenames, MIME types, file sizes, processing steps, and derivation links.
- Do not invent forensic conclusions or report numbers. Reports must use only stored structured evidence and reject generated numeric claims that are not present in the evidence payload.
- Preserve full-page Django navigation. HTMX and JavaScript are progressive enhancements only.
- Keep job state transitions consistent between `AnalysisJob` and `Case`. Use `JobTracker` for start, progress, retry, failure, cancellation, and success updates.
- Queue Celery work only after the database commit with `transaction.on_commit`.
- Avoid breaking the optional storage abstraction. Download files through Django storage file handles instead of assuming local filesystem paths.
- Keep `PROMPT.md`, `README.md`, `example.env`, Docker files, GitHub workflows, and dependency pins aligned with the actual runtime.

## Public Repository Rules

- `.env` is ignored and must remain local. Commit only placeholder configuration in `example.env`.
- Uploaded evidence, generated reports, transcripts, private media, model weights, generated static output, virtual environments, caches, and raw local sample files must not be committed.
- The `samples/` directory is for local/manual QA data. Raw `.wav`, `.mp3`, `.flac`, `.m4a`, `.ogg`, and `.txt` files under `samples/` are ignored by default.
- Do not paste secrets, case material, voice recordings, transcripts, report PDFs, or identifying evidence metadata into public issues, pull requests, docs, or fixtures.
- Review third-party dashboard/static asset licenses before public release. The repo vendors UI libraries, fonts, images, and template assets under `static/dash/assets`.
- No project license has been selected yet. Do not add one without an explicit owner decision.

## Key Modules

- `core`: settings, URL routing, WSGI/ASGI, Celery bootstrap, storage backend configuration.
- `dash`: authentication, dashboard shell, settings, invitations, user management, and theme preference API.
- `forensics`: case models, evidence artifacts, analysis jobs, pipeline services, report generation, monitoring, forms, views, and templates.
- `utilities`: forms and helper utilities used by dashboard/account surfaces.
- `templates`: server-rendered UI for auth, dashboard, cases, reports, and partial refresh panels.
- `static/dash/assets`: dashboard CSS and JavaScript assets, including theme persistence and case refresh helpers.
- `scripts`: helper scripts for model asset preparation and documentation rendering.
- `docs`: publication and operational documentation.

## Repository Files

- `README.md`: public project overview, setup, Compose instructions, test commands, GitHub Actions, limitations, and license status.
- `PROMPT.md`: this application prompt for future chatbot work. Keep it updated whenever app behavior, stack, workflows, or repo policy changes.
- `example.env`: committed placeholder environment template.
- `.gitignore`: excludes secrets, virtual environments, caches, generated output, model assets, evidence storage, and raw local samples.
- `.dockerignore`: keeps secrets, generated output, raw samples, and local-only files out of Docker build contexts.
- `SECURITY.md`: public security and sensitive-data handling policy.
- `docs/PUBLIC_RELEASE.md`: checklist for pushing the repository publicly.
- `.github/workflows/ci.yml`: lint, migration, Django, static, test, and Compose checks.
- `.github/workflows/workflow.yaml`: GHCR image publishing on pushes to `main`.
- `.github/workflows/docker-build.yml`: optional Docker Hub build/publish workflow when Docker Hub secrets are configured.
- `.github/dependabot.yml`: weekly update checks for pip, GitHub Actions, and Docker.

## GitHub Image Publishing

The GHCR workflow builds the shared Dockerfile and publishes these image references on pushes to `main`:

- `ghcr.io/<owner>/audioarca`
- `ghcr.io/<owner>/audioarca-web`
- `ghcr.io/<owner>/audioarca-celery-worker`
- `ghcr.io/<owner>/audioarca-celery-beat`
- `ghcr.io/<owner>/audioarca-flower`

The Docker Hub workflow uses the image name `audioarca` and only pushes when `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` repository secrets are present.

## Verification Commands

Run these before claiming the app is healthy:

```bash
python -m ruff check .
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py check --deploy --fail-level ERROR
python manage.py test dash.tests forensics.tests
python manage.py collectstatic --noinput
docker compose --env-file example.env -f docker-compose.yml config --quiet
docker compose --env-file example.env -f dev-compose.yml config --quiet
```

For rendered frontend validation, start the app and verify:

- `/signin/` renders without errors.
- Authenticated `/dashboard/`, `/phonetic/`, `/linguistic/`, `/reports/`, `/settings/`, and `/users/` pages render.
- Theme toggling persists through `api/user/settings/theme/`.
- Case detail partial refresh panels load without breaking full-page navigation.
- Phonetic and linguistic case creation show validation errors for invalid inputs and redirect to case detail for valid inputs.

## Current Operational Notes

- Real report generation requires a valid `OPENAI_API_KEY`.
- Real phonetic analysis requires writable model assets and available local ML/audio dependencies.
- Heavy model downloads should happen in controlled runtime environments, not in lightweight unit tests.
- `core/settings.py` auto-loads `.env` if present, so local shell commands and Docker Compose use the same variable names.
- `example.env` must contain placeholders only.
- `README.md`, `SECURITY.md`, and `docs/PUBLIC_RELEASE.md` are part of the public-release documentation and should remain synchronized with repository policy.
