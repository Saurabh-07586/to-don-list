# API

All protected endpoints require `Authorization: Bearer <JWT>` and return JSON errors as `{"error":{"message":"…"}}`.

| Area | Endpoint |
|---|---|
| Auth | `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `POST /api/auth/password-reset/request`, `POST /api/auth/password-reset/confirm` |
| Tasks | `GET, POST /api/tasks` (GET supports `q`, `status`, `priority`, `category`; POST accepts optional idempotency key `client_request_id`); `GET, PUT, DELETE /api/tasks/:id`; `PATCH /api/tasks/:id/status`; `POST /api/tasks/:id/subtasks`, `/api/tasks/:id/subtasks/bulk`; `GET, PUT /api/tasks/:id/dependencies`; create/update accepts optional `recurrence: {frequency, interval}` |
| Projects | `GET, POST /api/projects`; `GET, PUT, DELETE /api/projects/:id`; `PUT /api/projects/:id/tasks`; `GET, POST /api/projects/:id/milestones`; `PATCH, DELETE /api/projects/:id/milestones/:milestone_id`; owner-only `GET, POST /api/projects/:id/members`, `DELETE /api/projects/:id/members/:user_id`, `GET, PUT /api/projects/:id/assignments`; project owner/member comments with `GET, POST /api/projects/:id/comments`; collaborator views via `GET /api/shared/projects`; assigned-task status via `GET /api/assigned-tasks`, `PATCH /api/assigned-tasks/:task_id/status` |
| AI | `POST /api/ai/generate-tasks`, `POST /api/ai/chat`, `POST /api/ai/schedule`, `POST /api/ai/parse-task`, `POST /api/ai/breakdown`, `POST /api/ai/prioritize`, `GET /api/ai/briefing` |
| Calendar | `POST /api/calendar/conflicts`; `POST /api/schedule/apply`; task `scheduled_start` updates reject overlaps |
| Analytics | `GET /api/analytics/overview` (daily/weekly goals, points, streak, badges), `GET /api/analytics/trends` |
| Focus | `GET, POST /api/focus-sessions` (accepts `session_type: focus|break`; optional task association for focus) |
| Notifications | `GET /api/notifications` (materializes idempotent due/overdue reminders; supports `?unread=true`); `PATCH /api/notifications/:id/read`; `POST /api/notifications/read-all` |
| Preferences | `GET, PUT /api/preferences` (validated work hours, reminder preference, daily/weekly task goals) |
| Backup | `GET /api/export/tasks.csv`, `GET /api/export/backup`, `POST /api/import/backup` |
| Calendar export | `GET /api/export/calendar.ics` (RFC 5545-compatible task events for importing into calendar clients) |
| Offline sync | `POST /api/tasks` accepts per-user `client_request_id`; this makes queued create retries idempotent |
| Attachments | `GET, POST /api/tasks/:id/attachments`; `GET, DELETE /api/attachments/:id` (maximum 20 MB per upload; allow-listed document/image formats) |

`GET /api/openapi.json` exposes a lightweight OpenAPI discovery document. Task creation accepts `title`, `description`, `status`, `priority`, `category`, `tags`, ISO-8601 `start_date`/`due_date`, estimated/actual minutes, and optional `subtasks`. Offline task creation includes a per-user `client_request_id` so retrying a sync will not create duplicates.

Password-reset email requires `SMTP_HOST`, `SMTP_FROM`, and a production HTTPS `FRONTEND_URL`; SMTP authentication and TLS/SSL can be configured with the `SMTP_*` settings in `backend/.env.example`. Reset tokens are hashed at rest, single-use, expire after 30 minutes, and changing a password revokes prior access tokens.
