# dvm-eagraph — LeanIX → Neo4j Loader

Downloads Application, BusinessCapability, Interface and Organization factsheets
(plus all relationships between them) from SAP LeanIX and loads them into Neo4j.

---

## Installation

```bash
pip install dvm-eagraph
```

Or run without installing using `uvx`:

```bash
uvx dvm-eagraph -- --help
```

> **Note:** `dvm-eagraph` depends on `dvm-leanix`.
> Ensure it is installed and available in your Python environment before use.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Neo4j running | Start the database in Neo4j Desktop before running the loader |
| `.env` file | Must contain `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` |
| `dvm-leanix` proxy | Must be running in a **separate terminal** before the loader is launched |
| `uv` (optional) | Required for `uvx` — install from [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |

---

## GitHub Copilot MCP Integration (Neo4j)

This project is pre-configured so that **GitHub Copilot in VS Code** can query the Neo4j database directly using natural language, via the `mcp-neo4j-cypher` MCP server.

### How it works

The MCP server is configured in `.vscode/settings.json`. When Neo4j is running, Copilot can execute Cypher queries through three tools:

- `read_neo4j_cypher` — run read queries (MATCH, RETURN)
- `write_neo4j_cypher` — run write queries (MERGE, CREATE, DELETE)
- `get_neo4j_schema` — introspect node labels and relationship types (requires APOC)

### Setup

1. **Install `uv`** — the MCP server is launched via `uvx` (no separate install needed after this):

   ```powershell
   # Windows (PowerShell)
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   ```bash
   # macOS / Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Start Neo4j** in Neo4j Desktop before using Copilot chat.

3. **Open the project in VS Code** — the `.vscode/settings.json` is already configured:

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

   > **Update the `--password` value** if your Neo4j password differs from `password`.

4. **Verify** — in Copilot Chat, the Neo4j tools will appear in the MCP tools list. Ask a question like:
   > *"How many Application nodes are in the graph?"*

### Changing credentials

If your Neo4j password is different, update `.vscode/settings.json` directly — or better, ask Copilot to query using your credentials. Do **not** commit passwords to source control; consider using a VS Code user settings override for the password.

### Start the dvm-leanix proxy

```powershell
dvm-leanix serve          # uses default workspace URL + legacy SSL
```

The proxy authenticates via your browser and listens on `http://localhost:8765/graphql`.

---

## Quick Start

```bash
# Full run: download from LeanIX, load into Neo4j
dvm-eagraph

# Or with uvx (no install needed):
uvx dvm-eagraph
```

---

## Usage

```
dvm-eagraph [OPTIONS]

Options:
  --proxy URL             dvm-leanix GraphQL proxy URL
                          (default: http://localhost:8765/graphql)
  --data-dir PATH         Directory to save raw JSON files
                          (default: data/leanix)
  --mapping PATH          Path to metamodel mapping YAML
                          (default: metamodel-mapping.yaml in CWD, then bundled default)
  --generate-mapping      Scan LeanIX and write a mapping YAML, then exit
  --all-factsheets        Download every FactSheet type (ignores mapping filter)
  --limit N               Cap downloads to N records per FactSheet type and N rows per
                          relationship type (useful for testing)
  --no-verify-ssl         Disable SSL certificate verification (insecure)
  --skip-download         Load from previously saved JSON; skip LeanIX download
  --skip-neo4j            Download only; do not write to Neo4j
```

### Common workflows

```bash
# Download only (inspect JSON before loading)
dvm-eagraph --skip-neo4j

# Quick end-to-end test with 10 records per type
dvm-eagraph --limit 10

# Load previously downloaded JSON into Neo4j (no LeanIX connection needed)
dvm-eagraph --skip-download

# Generate a mapping YAML from your live LeanIX workspace
dvm-eagraph --generate-mapping

# Use a custom mapping file
dvm-eagraph --mapping path/to/my-mapping.yaml

# Re-run against a different proxy
dvm-eagraph --proxy http://localhost:9000/graphql

# With uvx, passing arguments after --
uvx dvm-eagraph -- --skip-download --mapping my-mapping.yaml
```

---

## Mapping File

The `metamodel-mapping.yaml` controls which LeanIX FactSheet types are downloaded and how they map to Neo4j labels and relationship types.

- **Default**: if no `metamodel-mapping.yaml` exists in the working directory, the bundled default is used automatically.
- **Custom**: place your own `metamodel-mapping.yaml` in the working directory, or pass `--mapping <path>`.
- **Generate**: run `dvm-eagraph --generate-mapping` to scan the live workspace and produce a new file.

---

## What Gets Downloaded

### Factsheet types

| LeanIX Type | Neo4j Label | Key properties loaded |
|---|---|---|
| `Application` | `:Application` | `id`, `displayName`, `description`, `technicalSuitability`, `businessValue`, `lifecycle`, … |
| `BusinessCapability` | `:BusinessCapability` | `id`, `displayName`, `description`, `level`, … |
| `Interface` | `:Interface` | `id`, `displayName`, `description`, `dataFlowDirection`, … |
| `Organization` | `:Organization` | `id`, `displayName`, `description`, `orgUnit`, … |

All scalar fields returned by the LeanIX GraphQL schema are downloaded and stored as
node properties. The LeanIX `id` (UUID) is used as the unique identifier.

### Raw JSON output

Downloaded data is saved to `data/leanix/` for inspection and offline re-use:

```
data/leanix/
  Application.json
  BusinessCapability.json
  Interface.json
  Organization.json
  Application_relations.json
  BusinessCapability_relations.json
  Interface_relations.json
  Organization_relations.json
```

---

## Graph Model

The script extends the base graph model with LeanIX data:

```
(Application)-[:SUPPORTS]->(BusinessCapability)
(Application)-[:EXPOSES]->(Interface)
(Interface)-[:CONSUMED_BY]->(Application)
(Interface)-[:CARRIES]->(DataObject)
(Application)-[:OWNED_BY]->(Organization)
(BusinessCapability)-[:CHILD_OF]->(BusinessCapability)   # hierarchy
```

### Relationship type mapping

LeanIX relation field names are mapped to Neo4j relationship types:

| LeanIX relation field | Neo4j relationship type |
|---|---|
| `relApplicationToBusinessCapability` | `:SUPPORTS` |
| `relApplicationToInterface` | `:EXPOSES` |
| `relInterfaceToApplication` | `:CONSUMED_BY` |
| `relInterfaceToDataObject` | `:CARRIES` |
| `relApplicationToDataObject` | `:USES_DATA` |
| `relToParent` | `:CHILD_OF` |
| `relApplicationToOrganization` | `:OWNED_BY` |
| `relOrganizationToApplication` | `:OWNS` |
| `relApplicationToITComponent` | `:USES_COMPONENT` |
| `relITComponentToApplication` | `:USED_BY` |
| *(any other)* | Auto-converted camelCase → UPPER_SNAKE_CASE |

---

## Idempotency

The script is safe to re-run — it will update existing data, not duplicate it.

- **Nodes**: loaded with `MERGE (n:Label {id: row.id}) SET n += row`  
  The LeanIX UUID is the stable key. Re-running updates all scalar properties.
- **Relationships**: loaded with `MERGE (src)-[:TYPE]->(tgt)`  
  Duplicate edges are never created.
- **Constraints**: `CREATE CONSTRAINT IF NOT EXISTS` — harmless to run repeatedly.

---

## Architecture

```
dvm_eagraph.load_leanix
│
├── download_factsheets()   ← lean_ix.download.fetch_all()
│     Introspects GraphQL schema, fetches all pages, writes {Type}.json
│
├── download_relations()    ← lean_ix.download.fetch_all_relations()
│     Fetches all relation edges, deduplicates, writes {Type}_relations.json
│
├── load_saved_json()       ← reads previously saved JSON (--skip-download)
│
└── load_to_neo4j()
      ├── create_constraints()     UNIQUE on id per label
      ├── load_nodes()             MERGE + SET per factsheet type
      └── load_relationships()     MERGE per relationship type (batched by type)
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Connection refused` on proxy URL | Proxy not running | Run `dvm-leanix serve` in another terminal |
| `AuthError` from Neo4j | Wrong credentials | Check `.env` values |
| `SSL` / certificate errors | Corporate proxy | Run `dvm-leanix diagnose` or use `--no-verify-ssl` |
| Empty download for a type | Type doesn't exist in workspace | Check available types with `dvm-leanix download --list-types` |
| Unknown relation in Neo4j | Unmapped LeanIX relation name | Add entry to `metamodel-mapping.yaml` |

### List available types and relations

```powershell
# List all factsheet types in the workspace
dvm-leanix download --list-types

# List all relation fields for a type
dvm-leanix download --type Application --list-relations
```
