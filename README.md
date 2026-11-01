# Document Generator

Uploads a `.docx` template (with Jinja-style `{{ placeholder }}` fields), then
generates a filled document from free-form user text using an LLM to write
the content for each placeholder.

## How it works

1. **Upload a template** — a `.docx` file containing fields like `{{ client_name }}`,
   `{{ project_summary }}`, `{{ date }}`. The API auto-detects these fields.
2. **Generate** — send the template's id plus free-form text (e.g. a user's
   rough notes/brief). The backend:
   - builds a prompt from the agent's system prompt + the field list + the user's text
   - calls the LLM, which returns a JSON object mapping each field to written content
   - renders the `.docx` template with that content via `docxtpl`
   - stores the result and returns a download URL

## Storage

Two interchangeable storage backends (`app/storage/`), selected via
`STORAGE_BACKEND` in `.env`:

- `local` (default, for development) — files live on disk under `STORAGE_DIR`
  (default `./storage`), served back via `GET /files/{key}`.
- `minio` — files live in a MinIO bucket, with presigned download URLs.
  Requires the `MINIO_*` settings in `.env`.

## LLM

Calls an Anthropic-Messages-API-compatible endpoint (e.g. a self-hosted vLLM
server), configured via `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in
`.env`. Uses the `anthropic` Python SDK's `auth_token` (sends
`Authorization: Bearer`) rather than `api_key` (`x-api-key`), since that's
what the vLLM gateway expects.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL, and STORAGE_BACKEND
# (defaults to "local" — no MinIO needed for development)
```

If using `STORAGE_BACKEND=minio` without a hosted instance, start a local
MinIO container:

```bash
docker compose up -d
```

Run the API:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Docs available at `http://127.0.0.1:8000/docs`.

## Running as a standalone Docker container

No local Python install needed — build the image and run it directly. This
uses the `local` storage backend (files persisted to a named volume), so no
MinIO container is required:

```bash
docker build -t docgen .

docker run -d --name docgen -p 8000:8000 \
  -e STORAGE_BACKEND=local \
  -e PUBLIC_BASE_URL=http://<host-ip-or-domain>:8000 \
  -e LLM_BASE_URL=https://your-runpod-endpoint.proxy.runpod.net \
  -e LLM_API_KEY=sk-... \
  -e LLM_MODEL=org/model-name \
  -v docgen_storage:/app/storage \
  docgen
```

Set `PUBLIC_BASE_URL` to whatever host/port the customer will actually reach
the container on — it's used to build the `download_url`s returned by
`/generate`.

To bring up the app together with a MinIO backend instead, use
`docker compose up -d --build`, which builds the `app` image from the
`Dockerfile` and starts it alongside the `minio` service defined in
`docker-compose.yml`. In that mode, set `STORAGE_BACKEND=minio` in `.env`
(the compose file already points `MINIO_ENDPOINT` at the sibling `minio`
container for you).

Use `127.0.0.1`, not `localhost`, everywhere below — on some machines
`localhost` resolves to `::1` (IPv6) first, which can hit a *different*
process if one happens to already be listening there. `PUBLIC_BASE_URL`
(used to build `download_url`s) defaults to `http://127.0.0.1:8000` for the
same reason.

## Creating a template

In Word (or LibreOffice), just type `{{ field_name }}` anywhere you want
generated content to go, e.g.:

```
Dear {{ client_name }},

{{ intro_paragraph }}

Project scope: {{ scope_summary }}

Regards,
{{ sender_name }}
```

Basic Jinja logic (`{% if %}`, `{% for %}`) also works since this uses
`docxtpl` under the hood.

## Using a fixed default template (no template_id per request)

If your customer always fills the same template, seed it into storage once
and set `DEFAULT_TEMPLATE_ID` so `/generate` requests only need `user_text`:

1. Upload the template once (via the running API):
   ```bash
   curl -X POST http://127.0.0.1:8000/templates/upload -F "file=@your_template.docx"
   ```
   Copy the `template_id` from the response (e.g.
   `templates/a6c2215d..._your_template.docx`).
2. Set `DEFAULT_TEMPLATE_ID=<that value>` in `.env` and restart the app.
3. From then on:
   ```bash
   curl -X POST http://127.0.0.1:8000/generate \
     -H "Content-Type: application/json" \
     -d '{"user_text": "free-form notes..."}'
   ```
   `template_id` is optional — when omitted, `/generate` falls back to
   `DEFAULT_TEMPLATE_ID`. You can still pass `template_id` explicitly per
   request to override it for a one-off document from a different template.

## API usage

**1. Upload a template**

```bash
curl -X POST http://127.0.0.1:8000/templates/upload \
  -F "file=@proposal_template.docx"
```

Response:
```json
{
  "template_id": "8f3a1c.._proposal_template.docx",
  "filename": "proposal_template.docx",
  "placeholders": ["client_name", "intro_paragraph", "scope_summary", "sender_name"]
}
```

**2. Generate a filled document**

```bash
curl -X POST http://127.0.0.1:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "8f3a1c.._proposal_template.docx",
    "user_text": "This is for Acme Corp. We are proposing a 3-month website redesign, focused on mobile UX and checkout conversion. Sender is Jamie Lee, Head of Delivery."
  }'
```

Response:
```json
{
  "document_id": "b91e77....docx",
  "download_url": "http://127.0.0.1:8000/files/generated/b91e77....docx",
  "filled_fields": {
    "client_name": "Acme Corp",
    "intro_paragraph": "...",
    "scope_summary": "...",
    "sender_name": "Jamie Lee"
  },
  "missing_fields": []
}
```

Open `download_url` to get the finished `.docx`.

## Customizing the agent's behavior

- Edit `DEFAULT_AGENT_PROMPT` in `.env` (or `app/config.py`) to change the
  house style/tone globally.
- Or pass `"agent_prompt": "..."` in the `/generate` request body to override
  it per-request (e.g. different prompts per document type).

## Notes / next steps you may want to add

- **Auth**: this API has no authentication built in — add an API key or OAuth
  layer before exposing it publicly.
- **Validation**: `missing_fields` in the response tells you if the LLM didn't
  populate every placeholder (e.g. because the user's text didn't cover it) —
  you may want to reject/retry or ask the user for more info in that case.
- **File types**: PDF/plain-text template support would need a different
  rendering path (`docxtpl` is Word-specific); ask if you want that added.
- **Storage cleanup**: generated documents are never deleted — consider a
  MinIO lifecycle policy to expire objects in the `generated` bucket.
