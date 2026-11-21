# Building Financial Pandit with CrewAI: From Spending Questions to Verified Explanations

Financial Pandit started with a practical question: can an AI assistant help someone understand how their spending changes from month to month without inventing numbers or hiding the calculation behind a fluent paragraph?

The first useful example was deliberately simple:

> Compare September spending with August by category.

That question contains more work than it first appears. The system has to interpret the months, retrieve the right transactions, separate expenses from income and transfers, aggregate the amounts by category, compare the periods, choose a useful visualization, and explain the result in language a person can act on.

CrewAI provided a way to learn those ideas in a concrete project: agents with distinct responsibilities, tasks that define the work, tools that connect agents to data and outputs, and a crew that coordinates the process.

## The problem: useful explanations need trustworthy numbers

The model is good at interpreting a question and explaining a result. It is not the source of truth for a bank transaction total. That distinction shaped the design.

Financial Pandit treats the database and deterministic tools as the evidence layer. The language model coordinates the work and explains the evidence, but it should not manufacture transactions, totals, chart references, or percentage changes.

For a month comparison, the reliable path is:

1. Identify the two periods and the intended transaction type.
2. Query the transaction store with read-only SQL.
3. Return the rows or aggregates together with filters, units, and limitations.
4. Calculate the comparison from those verified values.
5. Render a chart from the calculated values.
6. Ask the model to turn the evidence into a concise answer.

This makes it possible to say which parts are measured, which parts are calculated, and which parts are interpretation.

## Why CrewAI?

The goal was not only to answer one financial question. It was also to understand how an agent application is assembled.

CrewAI made the boundaries visible:

- An **agent** has a role, goal, backstory, model, and—when needed—tools.
- A **task** describes a unit of work and its expected output.
- A **crew** coordinates agents and tasks through a process.
- A **tool** is the controlled bridge from model reasoning to an external capability, such as a database query or chart renderer.

That separation was useful because financial analysis has different kinds of work. Retrieving data, calculating a comparison, and giving budgeting advice are related, but they should not all be performed by one unconstrained prompt.

## The agents in Financial Pandit

The current crew has four agents, configured in `agents.yaml` and assembled in `content_crew.py`.

### Financial Analysis Coordinator

The coordinator owns the user-facing question. It interprets the request, decides which specialists are needed, delegates focused assignments, checks whether the returned evidence is complete, and assembles the final Markdown response.

It is the only agent with delegation enabled. Its instructions also make an important distinction: conversation history helps resolve references such as “that month,” but only verified tool results justify financial amounts.

### Transaction Analyst

The Transaction Analyst is responsible for evidence retrieval. It can inspect the transaction schema and execute a single parameterized, read-only query.

The query tool enforces several safeguards:

- only one `SELECT` or `WITH` query is accepted;
- write and administrative statements are rejected;
- parameters are bound instead of interpolated into SQL;
- results are capped and truncation is reported;
- amounts are identified as dollars;
- empty results, query errors, and successful results remain distinct.

The class names still contain `SQLite` for compatibility with earlier work, but the current implementation is PostgreSQL-backed through `DATABASE_URL`. That naming mismatch is documented here because it is easy for a future contributor to otherwise infer the wrong storage layer.

### Financial Insights Analyst

The Financial Insights Analyst receives retrieved evidence from the coordinator. It calculates month-to-month differences, category rankings, trends, and other derived values. It can also call `create_spending_chart`.

This agent does not query the database itself. That restriction keeps the data boundary clear: retrieval belongs to the Transaction Analyst, while analysis and visualization operate on supplied evidence.

### Financial Advisor

The Financial Advisor explains the findings in practical terms. When the user asks for recommendations, it can suggest budgeting actions tied to observed spending and explicitly supplied goals.

It is instructed to distinguish measured facts from suggestions and to treat possible savings as estimates rather than promises. It also does not query transactions or generate charts.

## The task: one coordinator task, dynamic specialist work

The implementation may look like a conventional CrewAI pipeline at first, but it is not a fixed sequence of four scheduled tasks.

There is one scheduled task: `coordinator_task`. It receives the user question and optional session context. The coordinator then delegates to the specialists dynamically when the question requires them.

That means the actual execution for “Compare September spending with August by category” is conceptually:

```text
coordinator_task
    ├── delegate: retrieve August and September expense evidence
    │       └── Transaction Analyst → schema inspection + read-only query
    ├── delegate: compare categories and create a chart
    │       └── Financial Insights Analyst → calculations + chart tool
    └── delegate: explain implications or recommendations, if requested
            └── Financial Advisor
```

The coordinator can choose not to use every specialist. A simple lookup may need only the Transaction Analyst. A request for spending reductions may need all three. This is why the project uses delegation rather than making every question pay the cost of every step.

## Tracing one question through the system

Consider the question:

> Compare September spending with August by category and show me where the increase came from.

The coordinator first resolves the date range and the requested grouping. It tells the Transaction Analyst to work with `EXPENSE` rows and exclude `INCOME` and `TRANSFER` rows.

The Transaction Analyst inspects the available schema if necessary, then runs a parameterized query that returns category totals for both months. The important output is not just a list of numbers. It also includes the applied filters, query parameters, dollar unit, row count, and any limitations.

The coordinator passes those verified totals to the Financial Insights Analyst. That agent calculates, for each category:

```text
change_dollars = September_dollars - August_dollars
```

If a percentage is meaningful, it can also calculate:

```text
change_percent = change_dollars / August_dollars * 100
```

The percentage must be labeled undefined or omitted when the August baseline is zero. The system should not turn a zero baseline into a misleading percentage.

The insights agent then supplies the chart tool with explicit labels and dollar series. The chart tool validates the shape and precision of the input, renders a PNG, and returns the actual artifact path. It does not query the database or infer a takeaway.

Finally, the coordinator assembles the answer. It can show a Markdown table, cite the generated chart, summarize the largest changes, and separate observed facts from possible explanations or recommendations.

## Where the numbers come from

Financial Pandit has three different kinds of output, and keeping them separate is one of the most important design decisions.

### Database evidence

Transaction amounts, dates, categories, descriptions, and transaction types come from the database through the read-only query tools. The model cannot claim that a query ran unless the tool returned evidence.

### Deterministic calculations

Category totals, differences, rankings, and chart series are derived from retrieved values. Money is kept in dollar units and the chart input validates decimal precision, matching series lengths, unique labels, and special chart rules such as balanced Sankey flows.

### Model interpretation

The language model interprets the user’s intent, chooses the needed specialists, explains patterns, and offers recommendations when asked. Its explanations are useful, but they are not a replacement for the database query or the calculation.

That division is the core answer to a common agent-system concern: the model can explain a number without being allowed to invent the number.

## Tools and outputs

The project uses three important tool capabilities.

### Querying transactions

`inspect_transaction_schema` exposes the actual columns and types. `query_transactions` runs a bounded, parameterized, read-only query and returns JSON containing status, columns, rows, row count, the executed query and parameters, dollar units, and limitations.

### Calculating spending

The Financial Insights Analyst performs calculations over the retrieved evidence. For recurring reports, the repository also contains a monthly spending helper that aggregates expense records by month and category. The current production crew path is centered on the PostgreSQL query tools; the older monthly helper and its SQLite tests are useful regression coverage but should not be confused with the live crew database adapter.

### Producing charts

`create_spending_chart` renders bar, line, horizontal bar, stacked bar, waterfall, and Sankey charts. It accepts structured chart instructions rather than executable code. The output is a local PNG artifact with a returned reference that can be included in the response.

## Crew versus Flow: the confusion and the current choice

CrewAI supports both crews and flows, and it is easy to blur the two concepts.

A **Crew** is the team execution layer: agents, tasks, delegation, and the selected process. A **Flow** is an application-level workflow layer that can hold state, route conditionally, persist state, and coordinate multiple crews or direct agent calls.

The current Financial Pandit implementation uses a Crew. `Process.sequential` is present, but only one task is scheduled: the coordinator task. The specialist work happens through coordinator delegation, not through a four-task sequential chain.

That choice was appropriate for the first working example because it made agent roles and tool use easy to see. The next stage is a Flow around the crew when the application needs durable session state, retries, explicit routing, human review, or multiple analysis paths.

In other words:

```text
Current prototype: request → one coordinator task → delegated specialists → answer
Possible production evolution: request → Flow state/routing → Crew(s) → validated answer
```

The distinction also explains why LibreChat conversation history is currently passed into the API request as `session_context`. It is request-scoped context, not persistent CrewAI memory.

## Validation: what worked and what remains unreliable

The repository includes offline tests for the major boundaries:

- Crew wiring verifies four agents, one coordinator task, delegation permissions, and tool ownership.
- Flow-entry tests verify that the question and session context reach `run_assignment`.
- API tests verify `/health`, model discovery, `/ask`, OpenAI-compatible chat completions, streaming events, validation failures, and generic error responses.
- Query-tool tests cover parameter binding, result limits, rejected write statements, missing databases, invalid queries, and timeouts.
- Chart-tool tests cover all supported chart types, PNG creation, dollar precision, alignment validation, and balanced Sankey/waterfall rules.
- Monthly spending tests cover empty data, invalid months, decimal amounts, and aggregate totals.

These checks establish that the boundaries are intentionally designed and testable without making live model calls. They do not prove that every real model run will choose the ideal delegation plan, nor do they replace end-to-end testing against the configured production database.

The remaining reliability risks are therefore clear:

- a model may still misunderstand an ambiguous question;
- live database schema and data quality may differ from fixtures;
- a database outage or incomplete month can limit the answer;
- chart generation can produce an artifact without proving that the narrative takeaway is sensible;
- advice remains interpretation and should not be presented as regulated financial advice;
- the legacy `SQLite*` naming and separate SQLite helper can cause maintenance confusion.

The strongest next validation step is a small set of end-to-end cases using a controlled PostgreSQL dataset containing known August and September transactions, zero-baseline categories, income, transfers, missing categories, and an incomplete current month.

## The next requirement: FastAPI, LibreChat, and Azure

The natural product boundary is an HTTP API. Financial Pandit already contains the beginning of that boundary:

- `/health` provides a liveness check;
- `/ask` provides a simple question-and-answer endpoint;
- `/v1/models` exposes the `financial-pandit` model identity;
- `/v1/chat/completions` adapts the crew to an OpenAI-compatible chat contract;
- prior LibreChat messages are passed as request-scoped context;
- streaming keeps the connection alive while the synchronous crew completes.

LibreChat can therefore treat Financial Pandit as a custom OpenAI-compatible model endpoint. The API becomes the stable interface, while the CrewAI implementation and database remain behind it.

That leads naturally into Azure. A production deployment can separate the concerns into an API service, a managed PostgreSQL database, object storage for chart artifacts, centralized secrets, logging and tracing, and a LibreChat deployment or integration point. The exact Azure service choices should follow workload requirements, but the architectural direction is already visible: keep the model orchestration behind a versioned HTTP boundary and keep financial evidence behind a private, read-only data boundary.

## Closing perspective

Financial Pandit began as a month-comparison question, but the deeper lesson is about system boundaries. CrewAI is valuable here not because it makes arithmetic magical, but because it makes responsibilities explicit.

The database supplies evidence. Tools enforce controlled operations. Agents specialize the work. The coordinator connects the pieces. The final language model response explains what the evidence supports—and should be honest about what it does not.

That is the foundation needed before exposing the project to LibreChat and deploying it as a reliable Azure-backed service.

See the standalone [architecture diagram](./architecture.md) for the component and data-flow view.
