# Financial Pandit Architecture

The diagram shows the current CrewAI implementation and the next integration boundary for LibreChat and Azure. The specialist agents are dynamically delegated by the coordinator; they are not four fixed sequential tasks.

```mermaid
flowchart LR
    U[User question]
    LC[LibreChat]
    API[FastAPI adapter\n/ask\n/v1/chat/completions]
    CTX[Request-scoped\nconversation context]

    subgraph APP[Financial Pandit application]
        ENTRY[run_assignment]
        CREW[ContentCrew\nProcess.sequential\n1 scheduled coordinator task]
        COORD[Financial Analysis Coordinator\ninterprets, delegates, assembles]
        TX[Transaction Analyst\nread-only evidence retrieval]
        INS[Financial Insights Analyst\ncalculations and chart instructions]
        ADV[Financial Advisor\nplain-language recommendations]
        SCHEMA[inspect_transaction_schema]
        QUERY[query_transactions\nparameterized SELECT/WITH\nbounded results]
        CHART[create_spending_chart\nvalidated chart input]
        ANSWER[Markdown answer\nfacts + calculations + limitations]
    end

    subgraph DATA[Evidence and artifacts]
        DB[(PostgreSQL\ntransactions)]
        PNG[(PNG chart artifacts)]
    end

    subgraph AZURE[Production direction on Azure]
        SECRETS[Secret management]
        OBS[Logs, metrics, traces]
        STORE[Durable artifact storage]
    end

    U --> LC
    LC --> API
    API --> CTX
    API --> ENTRY
    CTX --> ENTRY
    ENTRY --> CREW
    CREW --> COORD

    COORD -. delegate .-> TX
    COORD -. delegate .-> INS
    COORD -. delegate when advice requested .-> ADV

    TX --> SCHEMA
    SCHEMA --> DB
    TX --> QUERY
    QUERY --> DB
    QUERY --> TX
    TX --> COORD

    COORD --> INS
    INS --> CHART
    CHART --> PNG
    PNG --> INS
    INS --> COORD

    COORD --> ADV
    ADV --> COORD
    COORD --> ANSWER
    ANSWER --> API
    API --> LC
    LC --> U

    SECRETS -. DATABASE_URL / model config .-> API
    SECRETS -.-> APP
    OBS -. telemetry .-> API
    OBS -. telemetry .-> APP
    PNG -. future durable copy .-> STORE

    classDef model fill:#dbeafe,stroke:#2563eb,color:#0f172a;
    classDef tool fill:#dcfce7,stroke:#16a34a,color:#0f172a;
    classDef data fill:#fef3c7,stroke:#d97706,color:#0f172a;
    classDef boundary fill:#f3e8ff,stroke:#9333ea,color:#0f172a;

    class COORD,TX,INS,ADV model;
    class SCHEMA,QUERY,CHART tool;
    class DB,PNG data;
    class API,LC,SECRETS,OBS,STORE boundary;
```

## Reading the diagram

- LibreChat sends the current user message and prior turns to the FastAPI adapter.
- `run_assignment` passes the question and request-scoped context to `ContentCrew`.
- The coordinator is the only scheduled task and is the only agent allowed to delegate.
- The Transaction Analyst is the only database-facing specialist.
- The Financial Insights Analyst works from supplied evidence and creates charts from validated values.
- The Financial Advisor explains implications and recommendations but does not query data.
- The database is an evidence source; the chart PNG is an output artifact.
- Azure services are shown as the production direction for secrets, observability, and durable artifact storage. They are deployment concerns around the current application, not additional agents.

## Current versus next state

| Area | Current implementation | Production direction |
|---|---|---|
| Client | Direct entry point and API tests | LibreChat using the OpenAI-compatible endpoint |
| Orchestration | One coordinator task with dynamic delegation | Flow wrapper for state, routing, retries, and review |
| Database | PostgreSQL via `DATABASE_URL` and read-only tools | Managed/private Azure PostgreSQL with operational controls |
| Charts | Local PNG artifacts | Durable artifact storage and response links |
| Configuration | `.env` values loaded locally | Managed secrets and deployment configuration |
| Operations | Logs and local validation | Azure monitoring, tracing, alerts, and health checks |
