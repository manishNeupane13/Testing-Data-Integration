# PyFlow API Studio

A general-purpose, configuration-driven Python integration studio. FastAPI owns business logic; Streamlit is an HTTP client. SQLite persists reusable blueprints, revisions, job states and node outputs. Each endpoint uses the same asynchronous HTTP executor.

## Run locally

```sh
cd pyflow-api-studio
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Edit .env and export its values into both server processes.
set -a
source .env
set +a
uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
# In another terminal with the same environment:
streamlit run frontend/app.py
```

Open http://localhost:8501 and the API reference at http://localhost:8000/docs. If PYFLOW_API_TOKEN is set, API callers must send `Authorization: Bearer <token>`. The UI reads that token from its environment. Docker alternative: `docker compose up --build` after creating `.env`; both ports bind only to localhost.

## First request (Phase 1)

1. In Connections, set a name, base URL, exact approved hostname and authentication type. The HTTP executor rejects disabled connections and private/reserved IPs. No private network exception exists in production.
2. For credentials, set a backend environment variable containing a JSON object, then set `credential_ref` to its name. Examples: API keys use `{"api_key":"..."}`, bearer uses `{"token":"..."}`, basic uses `{"username":"...","password":"..."}`. Configure `auth_name` for API-key headers/query keys.
3. In Endpoint Blueprint Editor, choose that connection, method, relative path, query/path parameters, headers, body and pagination. Save and test. Blueprint JSON contains references, never credential values. Add any number of blueprints without endpoint-specific Python code.
4. Refresh the returned execution ID in HTTP Request Tester or Execution History. Copy result JSON into JSON Explorer or Transformation Builder. Stored Results provides JSONL downloads.

## Workflows and transforms

The builder provides editable node lists, dependency multiselection and a graph. All nodes share an asynchronous BaseNode contract, returning an output object with `data`. Dependencies run in topological waves; independent nodes run concurrently. Missing dependencies and cycles fail validation before persistence. A failed wave cancels its other running tasks; already completed node outputs remain retrievable.

References are JMESPath expressions: `{{ nodes.fetch.output.data[0].id }}`. Whole references preserve JSON types; embedded references become strings. HTTP nodes accept `endpoint_id`, optional `overrides`, and `foreach` resolving to an array. Endpoint templates can use `{{ item.id }}`. Fan-out runs in bounded chunks. Batch nodes split arrays into finite batches; they do not create graph cycles. Conditional nodes emit `matched` and either the input data or null; downstream nodes can reference `matched` explicitly.

Transform configurations use `operation`. `filter`, `map`, and `extract` accept a JMESPath `expression`; filter example: `[?active == `true`]`. `select` maps output field names to expressions; `rename` maps old names to new names. Other operations: flatten, sort, deduplicate, group, aggregate (count/sum/mean/min/max), merge, inner join, JSON-schema validate, batch. Export nodes write JSONL, CSV or Parquet and return an `artifact_id`; download via `/artifacts/{id}`.

`examples/blueprints.json` contains four runnable examples: active drivers to JSON; IDs to detail fan-out; pagination/flatten/select to Parquet; parallel APIs with normalization and merge. These use an in-process HTTPX mock transport and a fictitious `.example` domain. They cannot contact a real vendor.

```sh
python -m examples.run_mock
pytest -q
```

## API

GET/POST `/connections`, `/endpoints`, `/workflows`: list/save (same ID updates and increments version). GET/DELETE `/{kind}/{id}`: retrieve/delete. Export/import configurations by GET and POST; the registry UI exports/imports endpoint arrays. POST `/endpoints/{id}/duplicate` creates a new ID.

POST `/connections/{id}/test`; POST `/endpoints/{id}/test` or `/execute`; POST `/workflows/{id}/execute` and `/webhooks/{id}` (body: `{"input": ..., "idempotency_key": "..."}`). Endpoint/workflow executions return 202 and a job ID. GET `/jobs`, `/jobs/{id}`, `/jobs/{id}/nodes/{node_id}`, `/results/{id}`; POST `/jobs/{id}/cancel`. GET `/results/{id}/download` streams a JSONL file. POST `/nodes/test` accepts a node and a context. POST `/json/transform` accepts data/config; POST `/json/inspect` accepts data/search. `/settings` exposes request metrics.

Pagination supports none, cursor, offset, page, Link-header and next-URL. Configure item/next expressions, parameter names, page size, max pages/records and a stop expression. Defaults for page/offset parameter names must be changed from cursor to page/offset. Errors after a successful page return partial results and structured pagination errors. Repeated request/cursor pairs terminate loops. Every next URL passes the same security check; redirects are rejected. Responses retain parsed pages, optional raw text, extraction, timestamps, status, headers, byte counts and pagination metadata.

## Authentication and operations

OAuth client credentials use `token_url`, `client_id`, `client_secret`; authorization-code connections require an externally provisioned `refresh_token` plus those fields. Token URLs must be on the approved-host list. This MVP does not host browser consent/callback routes. Access tokens are cached and renewed; rotated refresh tokens require durable secret-store provisioning before restart. Secrets are environment-only, with known-value and sensitive-key redaction before result persistence. Never place credentials in arbitrary bodies, URLs, or expressions; configuration validation detects sensitive field names but cannot recognize arbitrary literal secrets.

Retry policies honor Retry-After and exponential backoff/jitter. POST/PATCH retry only with explicit `retry_unsafe`; HTTP workflow nodes do not add an extra retry layer. Per-connection rate limits and global HTTP concurrency apply. API idempotency keys deduplicate submissions for a resource; they do not guarantee exactly-once third-party side effects. Job failures are categorized, and structured failure logs include execution IDs. Startup marks previously running/queued jobs interrupted; rerun explicitly to avoid replaying unsafe requests. Cancellation is cooperative.

## Scope and deployment limits

This is a runnable local MVP spanning the requested phases, not a production multi-tenant service. Use one backend process: jobs, token caches, concurrency controls and rate limiters are process-local. Distributed workers, interactive OAuth consent, durable rotated-refresh-token storage and resumable node checkpoints remain future work. Persistent interval schedules are available in Settings or `/schedules`: set `workflow_id`, `interval_seconds`, `enabled`, and optional epoch `next_run`. The single-process scheduler skips overlapping runs and missed ticks; it does not implement cron/timezone calendars.

Responses are streamed into a bounded buffer (10 MiB by default); pagination and transformations currently retain bounded collections/pages in memory. JSONL writes records incrementally; large result retrieval is available as a file download. SQLite stores result snapshots too, so this is not an unbounded streaming data warehouse. Tune maximum pages/records conservatively.

Host approval and DNS public-address checks block private destinations. Connections pin the validated public IP and preserve the original HTTP Host and TLS server name to prevent DNS rebinding. An egress firewall adds defense in depth. HTTPS is recommended; HTTP is supported for public services. OAuth responses use the same streamed body cap. Keep the UI/API on trusted networks; the optional shared API token is not user isolation. Do not expose an unauthenticated instance publicly.

All persistent data is under PYFLOW_DATA_DIR (default data). Protect this directory and credential environment using OS permissions. Run tests before connecting production services.


## Data Workbench: dynamic fetch, import, cleaning and validation

Open **Data Workbench** in the sidebar. Import a UTF-8 CSV, JSON array or JSONL file, fetch from a saved endpoint using dynamic input JSON, load a stored result, or try the demo. Imports support up to 10 MiB and 100,000 object records. Use a records expression to extract API arrays; flatten nested objects to select their fields from dropdowns. Google Sheets row arrays require a mapping into object records first.

Add ordered cleaning steps (trim, lowercase, uppercase, fill blanks, replace text, convert types). Add checks (required, type, unique, minimum, maximum, allowed values, conditional IF/THEN JMESPath expressions). Each row is checked against every configured rule. Failed type conversions keep the original value and add a cleaning issue. Unique checks flag every record sharing a duplicate value. Rule fields target literal top-level keys, including flattened keys.

Run the pipeline to view cleaned records, passed records, failed records with source row numbers, and every failure reason. Source records are copied before processing. Search and mixed-type sorting affect the report view; validation totals cover the full input. The report reflects the last run: rerun after changing controls. Download the full JSON report or passed/failed JSONL files. Enable storage to retrieve the report later through Stored Results and `/results/{id}`.

Save and import/export reusable recipes. Create a reusable workflow directly from a recipe: it captures a recipe snapshot, fetches a saved endpoint (or accepts imported records as manual trigger input), validates, then saves the report. Workflow Transform nodes can also use `{"operation":"validate_records","recipe":{...}}`; downstream nodes can reference `{{ nodes.check.output.data.passed }}` or `.failed`.

API additions: GET/POST `/recipes`, GET/DELETE `/recipes/{id}`; POST `/data/import` with `text` and `format`; POST `/data/process` with `data`, `recipe`, optional `search`, `sort_field`, `descending`, and `save`. Rules are configured checks, not arbitrary Python execution. Dedicated spreadsheet authentication and automatic cross-source reconciliation remain separate features.

## Custom Python nodes

Start Docker Desktop and download the official runtime image once:

```sh
docker build -t pyflow-python:1 runtime
```

Open **Custom Python** in the sidebar. Write Python in the editor, supply sample JSON (or choose Workbench records), then click **Run Test**. The input is named `data`; assign a JSON-compatible output to `result`. The default runtime includes pandas 2.2.3, NumPy 2.2.6, openpyxl 3.1.5, and standard-library modules. Printed output and errors appear below the editor. Change runtime/requirements.txt and rebuild the image to provision other packages; installation during execution remains disabled. Use 256 MiB for larger data-library jobs. DataFrames, NumPy scalars and workbook objects must be converted to JSON-compatible output before assigning result.

```python
result = []
for row in data:
    cleaned = dict(row)
    cleaned['name'] = str(row.get('name', '')).strip().title()
    cleaned['valid'] = bool(cleaned['name'])
    result.append(cleaned)
```

Save the code as a reusable workflow, or select `python` in Workflow Builder and choose the preceding HTTP/transform node as a dependency. Python receives the first dependency's `data` by default; an optional `input` reference in its node configuration can select another JSON value. Code text is never interpolated as a workflow expression. Output is exposed as `{{ nodes.custom_python.output.data }}`; console text as `.stdout`. Workflow runs preserve node output and errors through the existing execution history.

Each execution uses a disposable Docker container with network disabled, no host mounts or forwarded application environment, read-only root filesystem, an unprivileged user, dropped capabilities, no privilege escalation, one CPU, 32 PIDs, and 64–512 MiB memory without swap. `/tmp` is a disposable 16 MiB tmpfs. Defaults: 5 seconds and 128 MiB; maximum configured time is 30 seconds, plus a 2-second startup allowance. Code is limited to 50,000 characters, input to 10 MiB, print capture to 64 KiB, and JSON result to 2 MiB. Two code containers can run at once per backend process. Cancellation and timeouts remove the execution container. There is no fallback to executing code inside the backend.

This protects the API's files and credentials from normal custom scripts; data deliberately passed as input is accessible to the script. Use trusted local operators and keep Docker patched. Container isolation is not a guarantee against kernel/runtime vulnerabilities or a production hostile multi-tenant sandbox. The backend needs Docker CLI and engine access. The supplied Compose deployment does not grant Docker socket access or install Docker CLI, so custom nodes require the local backend setup or a separately provisioned execution service. Do not mount the Docker socket into public-facing deployments as a shortcut.

API: GET `/python/status`; POST `/python/test` with `{"config":{"code":"result=data","timeout":5,"memory_mb":128},"input":[...]}`. Tests return `success`, output or categorized errors. Runtime images are never pulled automatically; set `PYFLOW_PYTHON_IMAGE` only to an administrator-controlled Python image compatible with the runner.

Run real container integration tests explicitly after preparing Docker:

```sh
PYFLOW_TEST_DOCKER=1 pytest -q tests/test_python_node.py
```


Pandas example (Custom Python editor):

```python
import pandas as pd
df = pd.DataFrame(data)
df["name"] = df["name"].str.strip()
result = df.to_dict(orient="records")
```

The runtime image is `pyflow-python:1` by default. Thread counts for numerical libraries are limited to one to fit container PID/CPU limits.
