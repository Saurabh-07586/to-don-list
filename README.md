# TaskMaster Pro

TaskMaster Pro is a React + Flask productivity platform with JWT authentication, user-isolated SQLite/PostgreSQL data, AI task planning/chat, natural-language task extraction, AI task breakdown, next-task recommendations, daily briefings, recurring tasks, collaborative projects and task assignments, task dependencies, global search, file attachments, CSV/JSON/calendar export, deterministic scheduling, Kanban, analytics, persistent reminders, goals and achievements, focus/break history, secure password recovery, and responsive UI.

## Run locally

1. Copy `backend/.env.example` to `backend/.env`, set long `SECRET_KEY` and `JWT_SECRET_KEY`, and set `AI_API_KEY` if using AI features.
2. In `backend`: `python -m venv .venv`, activate it, then run `pip install -r requirements.txt` and `python run.py`.
3. In `frontend`: `npm install` then `npm run dev`.
4. Open the Vite address (normally `http://localhost:5173`). Register a new account.

The frontend reads `VITE_API_URL` (defaults to `http://localhost:5000/api`). For free local AI, install [Ollama](https://ollama.com), run `ollama pull qwen3:4b`, and use the included `AI_PROVIDER=ollama` configuration. Keep Ollama running while using AI features. Ollama’s local OpenAI-compatible server accepts the placeholder `ollama` key. Alternatively, set `AI_PROVIDER=openai`, `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL` for an OpenAI-compatible provider. Keys never enter frontend code. Missing/unavailable AI credentials return a clear error; no fabricated AI response is used.

## Database and migrations

SQLite is the development default. Set `DATABASE_URL=postgresql+psycopg://...` in production. Flask-Migrate is initialized, so after changing models run from `backend`:

`flask --app run:app db init` (once), `flask --app run:app db migrate -m "description"`, then `flask --app run:app db upgrade`.

`create_all()` provides a zero-configuration local first run; use migrations for deployed environments.

## Account recovery and collaboration

Password reset sends a one-time, 30-minute link through SMTP. Configure `SMTP_HOST`, `SMTP_FROM`, optional username/password, and TLS in `backend/.env`; set `FRONTEND_URL` to the production HTTPS app origin. Reset links are not returned by the API. Project owners can share with existing TaskMaster accounts, add comments, and assign project tasks; collaborators can update only tasks explicitly assigned to them.

## Tests and build

Run backend tests with `pytest tests` from `backend`. They use an in-memory SQLite database and do not need an AI key. Run `npm run build` from `frontend` for a production frontend build.

## Notifications, voice input, and calendar

Task creation and completion notifications, plus due/overdue reminders, are persisted to the account inbox. Due reminders are generated idempotently and checked at sign-in and periodically while the app is open; they can be marked read individually or all at once. Browser desktop alerts remain optional. Settings store work hours, reminder enablement, and daily/weekly task goals; Analytics shows goal progress, points, completion streaks, and earned badges. Focus and break sessions are saved to history, and task-linked focus time updates actual task time.

The app caches its production shell and the signed-in user’s task list, and queues task create/status/delete changes in account-scoped local storage for idempotent sync when connectivity returns. This device cache is not end-to-end encrypted; use it only on trusted, access-controlled devices. Use **Calendar** in the task toolbar to export `.ics` events for importing into calendar applications (this is file-based import, not a live two-way provider sync). Browser Web Speech integration and drag/drop interactions require browser testing; Chrome/Edge provide the broadest speech support and ask for microphone permission. Production service workers and speech features require HTTPS.

## Security notes

Passwords are Werkzeug-hashed. All task, focus, analytics, scheduler, and AI-context queries derive ownership from the JWT identity on the server. Sensitive and AI endpoints have rate limits, CORS is origin-restricted through `CORS_ORIGINS`, and AI proposals require a user action before task creation.

See [docs/API.md](docs/API.md) for the endpoint summary.
