# Financial Pandit agents

## FastAPI

### Docker

Compose is defined in `compose.yaml`. From this directory, use the following
PowerShell commands; the URL is transformed in memory so the container uses
`host.docker.internal` while the credential remains in `.env`:

```powershell
$line = Get-Content ..\.env | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
$env:DATABASE_URL_DOCKER = $line.Substring('DATABASE_URL='.Length) -replace '@localhost:', '@host.docker.internal:'
docker compose up --build -d
```

The default host port is 8000. If another API container already owns that
port, set `$env:API_PORT = '8001'` before `docker compose up -d`.

Build from `C:\DEV\FinancialPandit\financial_pandit_agents`:

```powershell
docker build -t financial-pandit-api .
docker run --rm --name financial-pandit-api -p 8000:8000 `
  --env-file .env `
  --mount "type=volume,source=financial-pandit-output,target=/app/financial_pandit_agents/output" `
  financial-pandit-api
```

Use the path to your actual `.env` file if it lives in the workspace root
(`--env-file ..\.env`). It must provide `OPENAI_API_KEY` and can provide your
model settings. Set `DATABASE_URL` to the PostgreSQL container using
`host.docker.internal`, for example `postgresql://pandit:<password>@host.docker.internal:5432/financial_pandit`.
Secrets and financial data are excluded from the build context. The named
output volume keeps generated charts after the container exits.

The container runs as a non-root user, serves FastAPI on port 8000, and checks
`/health`. Open http://localhost:8000/docs or configure LibreChat with the `/v1`
base URL. Docker Desktop LibreChat can use
`http://host.docker.internal:8000/v1` via the published port.
Tracing defaults to disabled in the container. To run the original CLI instead:

```powershell
docker run --rm --env-file .env `
  financial-pandit-api python -m financial_pandit_agents.main
```

The Dockerfile installs locked dependencies with uv using its
[documented Docker integration](https://docs.astral.sh/uv/guides/integration/docker/).

The HTTP interface lives in `src/financial_pandit_agents/api.py` and calls
the existing `run_assignment()` function. From this project directory:

```powershell
uv sync
uv run uvicorn financial_pandit_agents.api:app --app-dir src --reload
```

Open http://127.0.0.1:8000/docs to try the endpoints:

- `GET /health` returns `{"status": "ok"}` without running the crew.
- `POST /ask` accepts a question and optional caller-supplied session context:

  ```json
  {
    "question": "Compare September 2026 spending with August 2026.",
    "session_context": ""
  }
  ```

  It returns `{"answer": "..."}` after the crew finishes. Each request uses
  the configured model and can take time. Existing `.env` settings apply.
  Chart references remain part of the answer; this API does not serve chart files.

Blank or invalid questions return HTTP 422. Crew failures return a generic
HTTP 500 response, with exception details logged on the server.
This development command binds to localhost; the API has no authentication.

Run offline API checks without making model requests:

```powershell
uv run python -m unittest discover -s tests -p test_api.py -v
```

### LibreChat / OpenAI-compatible chat

`POST /v1/chat/completions` accepts `model: "financial-pandit"`, `messages`,
and optional `stream: true`. `GET /v1/models` advertises this model alias;
the underlying model remains controlled by the crew's environment settings.
The final message must be a nonblank user question. Earlier system, developer,
user, and assistant text messages are passed as JSON session context.
Text content arrays are supported; image, audio, and tool messages are not.
Common extra generation parameters are accepted but ignored: this adapter does
not override the crew's temperature, token limits, or tool configuration.
Usage token counts are omitted because the existing function returns only text.

Streaming uses OpenAI-style SSE chunks and ends with `data: [DONE]`.
It sends an initial role chunk and periodic keep-alive comments while the crew
works, then emits the full answer as one content delta. This is not live token
streaming. Disconnecting a client cannot stop an already running crew thread.

For LibreChat running in Docker Desktop on Windows, start the API from this
project directory:

```powershell
uv run uvicorn financial_pandit_agents.api:app --app-dir src --host 0.0.0.0 --port 8000
```

Merge `librechat.example.yaml` into LibreChat's existing `librechat.yaml`, then
restart LibreChat and select **Financial Pandit**. The example uses
`http://host.docker.internal:8000/v1`; native LibreChat on the same machine can
use `http://127.0.0.1:8000/v1`. The placeholder API key is ignored by this local
API, which has no authentication; use the all-interface binding only on a trusted
network. Automatic title generation and summarization are disabled to avoid
extra financial crew runs. Charts are still file references, not hosted images.

Configuration reference:
[LibreChat custom endpoints](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint).

Edit `assignment` in `src/financial_pandit_agents/main.py` to ask a question:

```python
assignment = "Compare August and September 2026 spending and create a chart."
```

The flow passes that question and optional session context to one coordinator
assignment. The coordinator can delegate to any of three specialists:

- Transaction Analyst: schema inspection and read-only PostgreSQL queries.
- Financial Insights Analyst: findings and spending charts.
- Financial Advisor: explanations and budgeting suggestions.

All four agents share the same configurable OpenAI model. Only the coordinator
has delegation enabled. Specialist instructions live in `agents.yaml`; only
`coordinator_task` is scheduled in `tasks.yaml`.

## How `coordinator_task` runs

The execution path for `crewai run` is:

```text
kickoff()                               main.py entry point
  -> run_assignment(assignment)
  -> FinancialFlow.kickoff(inputs=...)
  -> answer_question()                  @start() flow method
  -> ContentCrew().crew().kickoff(inputs=...)
  -> coordinator executes coordinator_task
  -> specialists are invoked through delegation as needed
  -> coordinator returns the final answer
```

The configuration and Python code connect in three places:

1. [tasks.yaml](src/financial_pandit_agents/crews/content_crew/config/tasks.yaml)
   defines `coordinator_task`, including its instructions, expected output,
   and `agent: coordinator`. Its `{user_question}` and `{session_context}`
   placeholders receive values from the crew kickoff inputs.
2. [content_crew.py](src/financial_pandit_agents/crews/content_crew/content_crew.py)
   defines the matching `@task` method:

   ```python
   @task
   def coordinator_task(self) -> Task:
       return Task(config=self.tasks_config["coordinator_task"])
   ```

   Its `crew()` method includes `tasks=[self.coordinator_task()]` and uses
   `Process.sequential`. This creates a crew with one scheduled task.
3. [main.py](src/financial_pandit_agents/main.py) starts that crew:

   ```python
   result = ContentCrew().crew().kickoff(inputs={
       "user_question": self.state.user_question,
       "session_context": self.state.session_context,
   })
   ```

Calling `coordinator_task()` creates the Task object. Calling the crew's
`kickoff()` executes it. You do not need to call the task method yourself.

The coordinator has `allow_delegation: true` in
[agents.yaml](src/financial_pandit_agents/crews/content_crew/config/agents.yaml).
The three specialists are crew members with delegation disabled. They have no
separately scheduled YAML tasks: the coordinator chooses which specialists to
invoke and supplies their assignments at runtime. Actual delegation depends
on the coordinator's model decisions and the available tools.

## Run

Set `OPENAI_API_KEY` in the project `.env` or your environment. Set
`OPENAI_MODEL_NAME` or `MODEL` to select the model; the fallback is
`openai/gpt-4o`. Project `.env` settings take precedence over the workspace
`.env`, and existing environment variables take precedence over both files.

From the project directory:

```powershell
crewai install
crewai run
```

To use the existing workspace virtual environment instead, run from
`C:\DEV\FinancialPandit`:

```powershell
$env:PYTHONPATH = (Resolve-Path financial_pandit_agents/src).Path
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m financial_pandit_agents.main
```

The final answer is printed to the terminal. Queries use the workspace's
PostgreSQL through `DATABASE_URL` in read-only mode. Chart artifacts are saved under
`financial_pandit_agents/output/charts`. Monetary inputs and outputs use dollars.

For a follow-up question in Python, call
`run_assignment(question, session_context="relevant earlier messages and findings")`.
The caller supplies conversation context; no persistent conversational recall
is enabled. CrewAI may maintain its own local execution/replay files.

For example, from Python with the project installed:

```python
from financial_pandit_agents.main import run_assignment

answer = run_assignment(
    "Compare that month with August 2026.",
    session_context="The previous question concerned September 2026 spending.",
)
print(answer)
```

Session context helps resolve references such as "that month". Supply relevant
verified earlier results as well if they should be considered; the coordinator
can request fresh retrieval when needed.

The root workspace `main.py` remains the standalone monthly-report command.

## Debug the flow in VS Code

Open `C:\DEV\FinancialPandit` as the workspace folder. The local
`.vscode/launch.json` provides three Run and Debug configurations using the
workspace root `.venv\Scripts\python.exe`. Install the Microsoft Python and
Python Debugger extensions if needed.

1. Select **Financial Pandit: offline flow tests** in Run and Debug.
2. Set breakpoints inside `run_assignment()` and `answer_question()` in
   `src/financial_pandit_agents/main.py`, then press **F5**.
3. Inspect `question`, `session_context`, and `self.state` in Variables.
   This test replaces `ContentCrew` with a mock, so it verifies the flow
   inputs and returned answer without entering the real crew or calling an LLM.
4. Select **Financial Pandit: live agent flow** to follow the real crew.
   It runs the editable `assignment` with the configured API key and makes
   billable model calls. It stops on entry; press **F5** to reach your breakpoints.

Place additional breakpoints on executable lines inside these methods:

| File | Method | What to inspect |
| --- | --- | --- |
| `main.py` | `kickoff()` | The editable `assignment` |
| `main.py` | `run_assignment()` | Inputs before `flow.kickoff()`; `flow.state` after it |
| `main.py` | `FinancialFlow.answer_question()` | `self.state.user_question`; `result.raw` after crew kickoff |
| `crews/content_crew/content_crew.py` | `shared_llm()` | Selected `model` |
| `crews/content_crew/content_crew.py` | `coordinator()`, specialist factory methods, `coordinator_task()`, `crew()` | Agent roles, tools, delegation flags, and scheduled tasks |
| `tools/sqlite_query_tool.py` | `SQLiteSchemaTool._run()` | Schema discovery requested by the Transaction Analyst |
| `tools/sqlite_query_tool.py` | `SQLiteQueryTool._run()` | PostgreSQL SQL, bound parameters, and returned rows |
| `tools/spending_chart_tool.py` | `SpendingChartTool._run()` | Chart inputs supplied by the Insights Analyst |

Use **F10** to step over a line, **F11** to step into a call, **Shift+F11**
to step out, and **F5** to continue to the next breakpoint. Enable **Raised
Exceptions** in the Breakpoints pane when investigating a failure. The live
profile sets `justMyCode: false`, allowing F11 to enter CrewAI's installed
Python code. Decorators may lead through framework wrappers before reaching
your methods; breakpoints inside method bodies are the easiest way to follow
your own code. You can step through the Python that sends a model request and
receives its response; the remote model's reasoning is not Python code.

The methods decorated with `@agent` and `@task` construct objects. Hitting
`financial_advisor()` means that agent was constructed, not that it executed
an assignment. The coordinator's model chooses delegation at runtime; only
`coordinator_task` is scheduled. Specialist tool breakpoints fire only when
the model requests those tools. The advisor has no custom tool, so use
CrewAI's execution code or tracing to observe its execution. CrewAI may cache
decorated factory results, so a factory breakpoint need not fire on every
access. Also, `verbose=False` in `crew()` limits terminal progress output.

Both debug test profiles disable tracing; the live debug profile also disables
tracing for a local walkthrough. Normal `crewai run` retains the tracing
behavior described below. The launch file is local because this repository
ignores `.vscode/`.

### Test each layer

**Financial Pandit: all offline tests** exercises flow wiring, read-only SQL,
monthly calculations, and chart rendering. To debug a tool deterministically,
use this profile and set a breakpoint in its `_run()` method. The tests use
synthetic data and do not rely on the model choosing a particular tool.

The equivalent terminal command, from `C:\DEV\FinancialPandit`, is:

```powershell
$env:PYTHONPATH = "$PWD\financial_pandit_agents\src;$PWD"
$env:PYTHONUTF8 = "1"
$env:CREWAI_TRACING_ENABLED = "false"
$env:CREWAI_STORAGE_DIR = "$PWD\.cache\crewai-tests"
.\.venv\Scripts\python.exe -m unittest discover -s financial_pandit_agents/tests -v
```

Passing offline tests verifies code behavior and wiring, not the quality of
real delegation or advice. For the live September assignment, inspect whether
the coordinator retrieves August and September 2026 expenses, obtains category
totals in dollars, passes verified figures to the chart specialist, and asks
the advisor for recommendations based on those findings. Check that reported
totals match the retrieved rows and that chart references point to actual files.

If execution never reaches `kickoff()`, check the exception and interpreter
first. During setup, the project-local
`financial_pandit_agents\.venv\Scripts\python.exe` failed to import
`pydantic_core` with `DLL load failed: Access is denied`, while the workspace
root `.venv` imported CrewAI 1.15.28 successfully. The debug configurations
explicitly use that root interpreter. This observation does not establish
whether the project environment fails outside the current execution context.
Running the workspace root `main.py`, running only selected editor text, or
using **Run Python File** instead of F5 also bypasses these debug settings.

References: [VS Code Python debugging](https://code.visualstudio.com/docs/python/debugging)
and [CrewAI crews](https://docs.crewai.com/en/concepts/crews).

## Tracing

CrewAI tracing is enabled by default for the flow and crew. To view exported
traces in CrewAI AMP, authenticate with `crewai login`, then run `crewai run`.
Follow any trace-sharing prompt shown by CrewAI. Traces can include questions,
tool results, and financial data. Enabling collection does not guarantee that
an upload or dashboard trace has succeeded.

Set `CREWAI_TRACING_ENABLED=false` in the environment or `.env` to disable it.
The offline tests disable tracing and do not upload execution data.
