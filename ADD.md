## Project Overview

Python project that models **enterprise applications and their relationships to business capabilities** in a Neo4j graph database. Applications are connected via typed Interfaces that carry named DataObjects.

Neo4j Desktop runs locally at `bolt://localhost:7687`, user `neo4j`. Credentials are in `.env` (never commit it). The database must be **started in Neo4j Desktop** before running any queries.

The Neo4j MCP server is configured in `.vscode/settings.json` — when the database is running, Copilot can query it directly using the `read_neo4j_cypher`, `write_neo4j_cypher`, and `get_neo4j_schema` tools.

## Running the Seed

The database is already seeded. To re-seed (clears and rebuilds all data):

```bash
.venv\Scripts\activate
py seed.py
```

`seed.py` clears the database and recreates everything from the inline data arrays at the top of the file. Edit those arrays to change the dataset.

## Loading from LeanIX (`load_leanix.py`)

Downloads FactSheets and relationships from a live LeanIX workspace via `dvm-leanix` proxy and loads them into Neo4j. Uses a YAML mapping file to control node labels and relationship type names.

### Typical workflow

```bash
# 1. Generate mapping YAML from live LeanIX (one-time per workspace)
py load_leanix.py --generate-mapping
# → writes metamodel-mapping.yaml

# 2. Review / edit metamodel-mapping.yaml as needed

# 3. Full run: download from LeanIX + load into Neo4j
py load_leanix.py

# Use a non-default mapping file
py load_leanix.py --mapping path/to/my-mapping.yaml

# Download only (skip Neo4j write)
py load_leanix.py --skip-neo4j

# Load from previously saved JSON (skip LeanIX download)
py load_leanix.py --skip-download
```

Requires `dvm-leanix serve` running in a separate terminal.

### `metamodel-mapping.yaml` structure

```yaml
factsheet_types:
  Application:
    node_label: Application       # Neo4j node label for this FactSheet type
  BusinessCapability:
    node_label: BusinessCapability
  # ... one entry per FactSheet type to include

relationships:
  relApplicationToBusinessCapability: SUPPORTS     # LeanIX rel field → Neo4j rel type
  relApplicationToInterface: PROVIDES
  relToParent: CHILD_OF
  # ...
```

If `metamodel-mapping.yaml` does not exist and `--mapping` is not supplied, the script falls back to a built-in default covering `Application`, `BusinessCapability`, `Interface`, `Organization`.

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

### Seeded data

**Applications (8):** CRM System (Salesforce), ERP System (SAP), Order Management System (Custom Java), Warehouse Management System (Manhattan WMS), HR Management System (Workday), BI Platform (Power BI), E-Commerce Platform (Shopify), Master Data Hub (Informatica MDM)

**Business Capabilities (6):** Customer Management, Order Management, Financial Management, Inventory Management, Human Resources, Analytics & Reporting

**Interfaces (11):** REST, Message, and Batch interfaces connecting the apps above, each carrying a named DataObject (e.g. `Customer`, `Order`, `FulfilmentOrder`, `Shipment`, `StockPosition`, `FinancialData`, `PayrollEntry`, `EmployeeData`)

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

## Neo4j Driver Usage

Use the official `neo4j` Python package (not `py2neo` or `neomodel`).

- Use `GraphDatabase.driver(...)` as a context manager.
- Prefer `session.execute_read` / `session.execute_write` over raw `session.run`.
- Always use parameterized queries — never concatenate user input into Cypher strings:
  ```python
  session.run("MATCH (n {name: $name}) RETURN n", name=user_input)
  ```

## Cypher Conventions

- Node labels: `PascalCase` (e.g., `Application`, `BusinessCapability`)
- Relationship types: `UPPER_SNAKE_CASE` (e.g., `SUPPORTS`, `CONSUMED_BY`)
- Property names: `camelCase`

## MCP Server (Copilot ↔ Neo4j)

Configured in `.vscode/settings.json` using `uvx mcp-neo4j-cypher` via stdio. No extra install needed — `uvx` runs it on demand.

`get_neo4j_schema` requires the **APOC plugin** to be enabled (Neo4j Desktop → your DB → Plugins tab).

## Environment

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
```

Loaded via `python-dotenv` (`load_dotenv()` at top of each script). The `.venv` is already created — activate with `.venv\Scripts\activate`.

## Testing

- Use a separate Neo4j test database to isolate test state.
- Clean up in teardown: `session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))`
- Run tests: `pytest`
- Run a single test: `pytest path/to/test_file.py::test_function_name`

