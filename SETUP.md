# Setup Guide — dvm-eagraph

This guide explains how to set up and run the `dvm-eagraph` pipeline on a new machine. The tool downloads FactSheets and relationships from a LeanIX workspace via the `dvm-leanix` proxy and loads them into a local Neo4j graph database.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| Neo4j Desktop | 5.x | [neo4j.com/download](https://neo4j.com/download/) |
| `dvm-leanix` | any | Lean IX proxy must be installed and authenticated |
| `uv` | any | For `uvx dvm-eagraph` — [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |

---

## 1 — Install the Package

### Option A — `uvx` (recommended, no install required)

Install `uv` once:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then run directly without installation:

```bash
uvx dvm-eagraph -- --help
```

### Option B — pip install

```bash
pip install dvm-eagraph
```

After installation, the `dvm-eagraph` command is available in your environment.

> **Note:** `dvm-leanix` Ensure it is installed and accessible in the same Python environment or on your `PATH` before running.

---

## 2 — Create the Neo4j Database

1. Open **Neo4j Desktop**
2. Create a new **Project** (or use an existing one)
3. Add a **Local DBMS** — version 5.x recommended
4. Set a password and start the database
5. Install the **APOC plugin** (optional but recommended for schema introspection):
   - In Neo4j Desktop → your DBMS → **Plugins** tab → install APOC

---

## 3 — Configure Environment Variables

Create a `.env` file in your working directory (never commit this file):

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password_here
```

Replace `your_password_here` with the password you set in Neo4j Desktop.

> `.env` is listed in `.gitignore`. Do not add credentials to source control.

---

## 4 — Configure the Mapping File

The `metamodel-mapping.yaml` file controls which LeanIX FactSheet types are downloaded and how they are named in Neo4j.

### How filtering works

The `factsheet_types` section is an **explicit whitelist**. Only types listed there are downloaded and loaded — any FactSheet type present in your LeanIX workspace but absent from the mapping is silently skipped.

Mapping resolution order (first match wins):

1. `--mapping <path>` — the file you specify (error if it doesn't exist)
2. `metamodel-mapping.yaml` in the current working directory
3. Bundled package default (covers the 14 common Volvo Group types below)

### Choosing your mapping

**Option A — use the bundled default** (no action needed):

When no `metamodel-mapping.yaml` is found in the working directory, the bundled default is used automatically. It covers:
`Application`, `BusinessCapability`, `BusinessContext`, `DataObject`, `DigitalProductHierarchy`, `Initiative`, `Interface`, `ITComponent`, `Objective`, `Organization`, `Platform`, `Provider`, `TechnicalStack`, `targetMap`

Use this if the bundled types cover everything you need. **If your workspace has FactSheet types not in this list, they will not be downloaded** — use `--all-factsheets` or a custom mapping instead.

**Option B — use a custom mapping in your working directory**:

Copy and edit the bundled default, or regenerate from your LeanIX workspace:

```bash
# Start the dvm-leanix proxy first (see step 5), then:
dvm-eagraph --generate-mapping
```

This writes a new `metamodel-mapping.yaml` in the current directory reflecting every type in your live workspace. Edit `node_label` and relationship values as needed before running the loader.

**Option C — point to a specific file**:

```bash
dvm-eagraph --mapping path/to/my-mapping.yaml
```

### When to use `--all-factsheets` instead

Use `--all-factsheets` when you want to **bypass the mapping whitelist entirely** and download every FactSheet type currently present in the workspace, without editing any YAML:

```bash
dvm-eagraph --all-factsheets
```

| Situation | Recommended approach |
|---|---|
| Bundled default covers all needed types | Run without any flags (use bundled default) |
| Workspace has extra types; you want to pick which ones | `--generate-mapping`, edit YAML, then run |
| Workspace has extra types; you want everything right now | `--all-factsheets` |
| Auditing a new workspace before setting up a mapping | `--all-factsheets --skip-neo4j` (download only) |

> **Note:** `--all-factsheets` still applies relationship mappings from the YAML (or built-in defaults). It introspects the live LeanIX GraphQL schema at runtime, so the proxy must be running. It cannot be combined with `--skip-download`.

---

## 5 — Start the dvm-leanix Proxy

`dvm-eagraph` connects to LeanIX via the `dvm-leanix` local proxy. Start it in a **separate terminal** before running the loader:

```bash
dvm-leanix serve
```

The proxy runs on `http://localhost:8765/graphql` by default. It must remain running for the duration of the download.


---

## 6 — Run the Loader

### Path A — Single step (you have LeanIX credentials and Neo4j on the same machine)

With the proxy running and Neo4j started:

```bash
dvm-eagraph
# or
uvx dvm-eagraph
```

This downloads all FactSheets and relationships from LeanIX (saved to `data/leanix/`) and loads them into Neo4j in one go.

**Full run time:** ~5–15 minutes depending on workspace size (~4,500+ applications).

### Path B — Separate steps (two people / two machines)

Use this when the person with LeanIX credentials is different from the person loading Neo4j — for example, a team member downloads the data on their machine and passes the `data/leanix/` folder to someone else who loads it into Neo4j.

**Step B1 — Download only** *(run by the person with LeanIX access, proxy must be running)*

```bash
dvm-eagraph --skip-neo4j
```

This saves all JSON files to `data/leanix/` and exits without touching Neo4j. Transfer the entire `data/leanix/` folder to the machine where Neo4j is running.

**Step B2 — Load into Neo4j** *(run by the person with Neo4j, no LeanIX connection needed)*

```bash
dvm-eagraph --skip-download
```

This reads the previously saved JSON from `data/leanix/` and loads everything into Neo4j. No proxy or LeanIX credentials required.

### Testing with `--limit`

Before running a full load, use `--limit N` to download and load only the first N records per FactSheet type and per relationship type. This lets you verify that the download, mapping, and Neo4j loading are all working correctly in under a minute, without waiting for the full dataset.

```bash
# Quick end-to-end test: download 10 records per type and load into Neo4j
dvm-eagraph --limit 10

# Download only, 10 records per type (inspect JSON before committing to Neo4j)
dvm-eagraph --limit 10 --skip-neo4j

# Re-load the limited JSON files already saved to disk
dvm-eagraph --limit 10 --skip-download
```

> The limit is applied **per FactSheet type** — so with `--limit 10` and 4 types, you get up to 40 nodes and up to 10 relationships per type. The capped JSON files are saved to `data/leanix/` and overwrite any previous full download, so run a full load (without `--limit`) when you are ready to populate the database completely.

---

## Common Usage Patterns

```bash
# Standard full load (download + load into Neo4j)
dvm-eagraph

# Quick test: download and load only 10 records per type
dvm-eagraph --limit 10

# Download only — inspect the JSON without touching Neo4j
dvm-eagraph --skip-neo4j

# Load from a previously saved download (no LeanIX connection needed)
dvm-eagraph --skip-download

# Use a custom mapping file
dvm-eagraph --mapping path/to/my-mapping.yaml

# Import every FactSheet type, ignoring the mapping filter (see below)
dvm-eagraph --all-factsheets

# Point to a non-default proxy URL
dvm-eagraph --proxy http://localhost:8765/graphql

# Regenerate the mapping YAML from the live workspace metamodel
dvm-eagraph --generate-mapping

# With uvx, pass arguments after --
uvx dvm-eagraph -- --skip-download --mapping my-mapping.yaml
```

### When to use `--all-factsheets`

By default, `dvm-eagraph` only downloads FactSheet types listed in the `factsheet_types` section of `metamodel-mapping.yaml`. This means **any type not in the mapping is silently skipped**.

Use `--all-factsheets` when:

- **Your workspace has types not in the mapping** — e.g. new FactSheet types were added to the LeanIX workspace after the mapping was generated. This flag bypasses the filter and discovers all types live from the LeanIX schema.
- **You want a full audit** — download everything in the workspace before deciding what to include in a custom mapping.

> **Note:** `--all-factsheets` still applies relationship mappings from the YAML (or built-in defaults). It requires the proxy to be running, as it introspects the live LeanIX GraphQL schema to discover available types. It cannot be combined with `--skip-download`.

---

## Verifying the Load

After the load completes, open Neo4j Browser at `http://localhost:7474` and run:

```cypher
// Count all nodes by label
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC;

// Spot-check applications
MATCH (a:Application) RETURN a.name LIMIT 10;

// Check relationships exist
MATCH ()-[r]->() RETURN type(r), count(r) ORDER BY count(r) DESC LIMIT 20;
```

A healthy load should show:
- ~4,500+ `Application` nodes
- ~400+ `BusinessCapability` nodes
- Relationships: `SUPPORTS`, `CHILD_OF`, `AFFECTS`, `OWNED_BY`, `RUNS`, etc.

---

## Data Directory Layout

Downloaded data is saved to `data/leanix/` and can be reused with `--skip-download`:

```
data/
└── leanix/
    ├── Application.json
    ├── BusinessCapability.json
    ├── Initiative.json
    ├── relations_Application.json
    └── ...
```

---

## Graph Data Model

```
(Application)-[:SUPPORTS]->(BusinessCapability)
(Application)-[:EXPOSES]->(Interface)-[:CONSUMED_BY]->(Application)
(Interface)-[:CARRIES]->(DataObject)
```

| Node                 | Key properties                                   |
|----------------------|--------------------------------------------------|
| `Application`        | `id`, `name`, `description`, `technology`        |
| `BusinessCapability` | `id`, `name`, `description`                      |
| `Interface`          | `id`, `name`, `protocol` (REST/Message/Batch)    |
| `DataObject`         | `name`, `description`                            |

### Cypher conventions

- Node labels: `PascalCase` (e.g., `Application`, `BusinessCapability`)
- Relationship types: `UPPER_SNAKE_CASE` (e.g., `SUPPORTS`, `CONSUMED_BY`)
- Property names: `camelCase`

### Useful Cypher patterns

```cypher
// Apps supporting a capability
MATCH (a:Application)-[:SUPPORTS]->(b:BusinessCapability {name: "Order Management"})
RETURN a.name

// All interfaces out of an app + what data they carry
MATCH (src:Application {name: "CRM System"})-[:EXPOSES]->(i:Interface)-[:CONSUMED_BY]->(tgt:Application)
RETURN i.name, i.protocol, [(i)-[:CARRIES]->(d) | d.name] AS dataObjects

// Data objects flowing into an app
MATCH (i:Interface)-[:CONSUMED_BY]->(a:Application {name: "ERP System"})
MATCH (i)-[:CARRIES]->(d:DataObject)
RETURN i.name, d.name

// Full map: app -> capabilities + who it sends to
MATCH (a:Application)
OPTIONAL MATCH (a)-[:SUPPORTS]->(b:BusinessCapability)
OPTIONAL MATCH (a)-[:EXPOSES]->(i:Interface)-[:CONSUMED_BY]->(tgt:Application)
RETURN a.name, collect(DISTINCT b.name) AS capabilities, collect(DISTINCT tgt.name) AS sends_to
```

---

## Neo4j Driver Usage

Use the official `neo4j` Python package (not `py2neo` or `neomodel`).

- Use `GraphDatabase.driver(...)` as a context manager.
- Prefer `session.execute_read` / `session.execute_write` over raw `session.run`.
- Always use parameterized queries — never concatenate user input into Cypher strings:

  ```python
  session.run("MATCH (n {name: $name}) RETURN n", name=user_input)
  ```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Connection refused` (Neo4j) | Database not started | Start DBMS in Neo4j Desktop |
| `Connection refused` (proxy) | `dvm-leanix serve` not running | Start proxy in a separate terminal |
| `Authentication failed` | Wrong password in `.env` | Check `NEO4J_PASSWORD` matches Neo4j Desktop |
| SSL errors during download | Corporate SSL inspection | Already handled — the loader uses a relaxed SSL context |
| `ModuleNotFoundError: lean_ix` | `dvm-leanix` not installed | Install `dvm-leanix` in your Python environment |
| Empty nodes loaded | Mapping YAML excludes the type | Add the FactSheet type to your `metamodel-mapping.yaml` or use `--all-factsheets` |
| `externalId` missing from nodes | Field requires JSON coercion | Ensure you are using the latest version of `dvm-eagraph` |

---

## Setting Up MCP for GitHub Copilot ↔ Neo4j

The project includes a pre-configured MCP (Model Context Protocol) server that lets **GitHub Copilot in VS Code** query Neo4j directly using natural language — no manual Cypher needed.

### What it enables

When Neo4j is running, Copilot gains three tools:

| Tool | Description |
|---|---|
| `read_neo4j_cypher` | Run MATCH/RETURN queries against the graph |
| `write_neo4j_cypher` | Run MERGE/CREATE/DELETE queries |
| `get_neo4j_schema` | Introspect node labels and relationship types (requires APOC) |

### Step 1 — Install `uv`

The server runs via `uvx` (part of the `uv` toolchain). Install it once per machine:

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify: `uvx --version`

> `uvx` downloads and runs `mcp-neo4j-cypher` on demand — no separate `pip install` is needed.

### Step 2 — Configure VS Code

The configuration lives in `.vscode/settings.json` (already in the repository):

```json
{
  "github.copilot.chat.mcp.servers": {
    "neo4j": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "mcp-neo4j-cypher",
        "--db-url", "bolt://localhost:7687",
        "--username", "neo4j",
        "--password", "password"
      ]
    }
  }
}
```

**If your Neo4j password differs from `password`**, update the `--password` value here.  
Do **not** commit passwords to source control — consider overriding in VS Code **User** settings (`Ctrl+Shift+P` → *Preferences: Open User Settings (JSON)*) so the project file stays generic.

### Step 3 — Start Neo4j Before Using Copilot

The MCP server connects to Neo4j at startup. Always start the DBMS in Neo4j Desktop **before** opening a Copilot Chat session that uses graph tools.

### Step 4 — Verify

In VS Code Copilot Chat, the Neo4j MCP tools will appear in the tool list. Test with:

> *"How many Application nodes are there?"*

Copilot will call `read_neo4j_cypher` and return the count directly from the graph.

### Schema introspection (`get_neo4j_schema`)

This tool requires the **APOC plugin**:

1. Neo4j Desktop → your DBMS → **Plugins** tab
2. Install **APOC**
3. Restart the DBMS

---

## Re-seeding the Demo Database (optional)

If you want a small self-contained demo graph without LeanIX access:

```bash
dvm-eagraph-seed
```

This clears the database and loads the following sample data:

**Applications (8):** CRM System (Salesforce), ERP System (SAP), Order Management System (Custom Java), Warehouse Management System (Manhattan WMS), HR Management System (Workday), BI Platform (Power BI), E-Commerce Platform (Shopify), Master Data Hub (Informatica MDM)

**Business Capabilities (6):** Customer Management, Order Management, Financial Management, Inventory Management, Human Resources, Analytics & Reporting

**Interfaces (11):** REST, Message, and Batch interfaces connecting the apps above, each carrying a named DataObject (e.g. `Customer`, `Order`, `FulfilmentOrder`, `Shipment`, `StockPosition`, `FinancialData`, `PayrollEntry`, `EmployeeData`)

To re-seed from source during development:

```bash
.venv\Scripts\activate   # Windows
py src/dvm_eagraph/seed.py
```

The inline data arrays at the top of `seed.py` define the full dataset — edit them to change the demo graph.


---

## Testing

Use a separate Neo4j test database to isolate test state from real data.

```bash
# Run all tests
pytest

# Run a single test
pytest path/to/test_file.py::test_function_name
```

Clean up graph state in teardown:

```python
session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
```

---

## Publishing a New Release

To publish a new version to PyPI:

1. Commit and push your changes
2. Create and push a git tag matching `vMAJOR.MINOR.PATCH`:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

The version is derived automatically from the git tag via `hatch-vcs` — no need to update any version string in the code. The GitHub Actions workflow (`.github/workflows/publish.yml`) will build, smoke-test, and publish to PyPI automatically via OIDC trusted publishing.

