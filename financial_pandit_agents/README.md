# Financial Pandit agents

Edit `assignment` in `src/financial_pandit_agents/main.py` to ask a question:

```python
assignment = "Compare August and September 2026 spending and create a chart."
```

The flow passes that question and optional session context to one coordinator
assignment. The coordinator can delegate to any of three specialists:

- Transaction Analyst: schema inspection and read-only SQLite queries.
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
`financial_pandit.db` in read-only mode. Chart artifacts are saved under
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

## Tracing

CrewAI tracing is enabled by default for the flow and crew. To view exported
traces in CrewAI AMP, authenticate with `crewai login`, then run `crewai run`.
Follow any trace-sharing prompt shown by CrewAI. Traces can include questions,
tool results, and financial data. Enabling collection does not guarantee that
an upload or dashboard trace has succeeded.

Set `CREWAI_TRACING_ENABLED=false` in the environment or `.env` to disable it.
The offline tests disable tracing and do not upload execution data.
