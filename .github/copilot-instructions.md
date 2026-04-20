# Copilot Instructions — dvm-eagraph

## What this project does

`dvm-eagraph` is a Python CLI tool that downloads FactSheets and relationships from a SAP LeanIX workspace (via the internal `dvm-leanix` proxy) and loads them into a local Neo4j graph database.

Entry points (defined in `pyproject.toml`):
- `dvm-eagraph` → `dvm_eagraph.load_leanix:main` — main pipeline
- `dvm-eagraph-seed` → `dvm_eagraph.seed:main` — loads a small self-contained demo graph

## Linting

Ruff is the linter and formatter (`line-length = 120`, rules: E/F/W/I/UP). Configuration lives in `pyproject.toml`.

```bash
uv run ruff check src/        # lint
uv run ruff check src/ --fix  # lint and auto-fix
uv run ruff format src/       # format
```

`seed.py` is exempt from E501 (long-line rule) — its data tables use intentional alignment.

## Build & release

The project uses `hatchling` + `hatch-vcs`. **Version is derived entirely from git tags** — never edit version strings manually.

```bash
# Build wheel and sdist
pip install build
python -m build

# Smoke-test the built wheel
pip install dist/*.whl
dvm-eagraph --help
```

**Release**: push a `vMAJOR.MINOR.PATCH` tag; GitHub Actions builds and publishes to PyPI automatically via OIDC trusted publishing.

```bash
git tag v0.2.0
git push origin v0.2.0
```

## Running locally (development)

```bash
# Recommended: run without installing
uvx dvm-eagraph -- --help

# Or install editable
pip install -e .
dvm-eagraph --help
```

Prerequisites before a full run:
1. Neo4j Desktop running with a DBMS started
2. `.env` file with `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` (see `.env.example`)
3. `dvm-leanix serve` running in a separate terminal (LeanIX GraphQL proxy on `http://localhost:8765/graphql`)

Common invocations:
```bash
dvm-eagraph                          # full run: download + load into Neo4j
dvm-eagraph --skip-neo4j             # download only, save JSON to data/leanix/
dvm-eagraph --skip-download          # load from previously saved JSON
dvm-eagraph --generate-mapping       # scan LeanIX and write metamodel-mapping.yaml, then exit
dvm-eagraph --mapping my.yaml        # use a custom mapping file
dvm-eagraph-seed                     # clear Neo4j and load demo graph
```

## Architecture

All logic lives in two source files under `src/dvm_eagraph/`:

| File | Role |
|---|---|
| `load_leanix.py` | Main pipeline: download from LeanIX, coerce data, write to Neo4j |
| `seed.py` | Seeds a small demo graph (no LeanIX access needed) |
| `metamodel-mapping.yaml` | Bundled default mapping (used when none found in CWD) |

### Pipeline flow (`load_leanix.py`)

```
main()
 ├── load_mapping()              reads CWD or bundled metamodel-mapping.yaml
 ├── download_factsheets()       → lean_ix.download.fetch_all() → {Type}.json
 ├── download_relations()        → lean_ix.download.fetch_all_relations() → {Type}_relations.json
 ├── load_saved_json()           reads previously saved JSON (--skip-download path)
 └── load_to_neo4j()
       ├── create_constraints()  UNIQUE on id per label
       ├── load_nodes()          MERGE + SET per factsheet type
       └── load_relationships()  MERGE per relationship type, batched by type
```

`load_leanix.py` imports `lean_ix.download` from the `dvm-leanix` package (external dependency, not in this repo).

### Metamodel mapping YAML

`metamodel-mapping.yaml` has two top-level keys:

```yaml
factsheet_types:
  Application:
    node_label: Application
    subtypes:           # optional
      Microservice:
        node_label: Application

relationships:
  relApplicationToBusinessCapability: SUPPORTS
```

The mapping controls which LeanIX types are fetched and what Neo4j labels/relationship types they get. If no mapping file is found in the CWD, the bundled one (`src/dvm_eagraph/metamodel-mapping.yaml`) is used automatically.

### Neo4j data model

All writes use `MERGE` — the tool is fully idempotent (safe to re-run).

- **Nodes**: `MERGE (n:Label {id: row.id}) SET n += row` — LeanIX UUID is the stable key
- **Relationships**: `MERGE (src)-[:TYPE]->(tgt)` — no duplicates ever created
- **Constraints**: `CREATE CONSTRAINT IF NOT EXISTS FOR (...) REQUIRE .id IS UNIQUE`

### Property coercion

`clean_props()` / `_coerce_neo4j_value()` handle the LeanIX → Neo4j type mapping:
- Primitives and lists of primitives → stored as-is
- Dicts and lists of objects → serialised to JSON strings (preserves fields like `externalId`)
- `None` values → dropped

### Relationship name conversion

`rel_name_to_neo4j(leanix_rel)` resolves relationship types:
1. Checks `DEFAULT_RELATIONS` dict (hardcoded known mappings)
2. Falls back to stripping `rel` prefix and converting camelCase → `UPPER_SNAKE_CASE`

The mapping YAML overrides both of these at load time.

## MCP — Neo4j integration for Copilot

The project is pre-configured (`.vscode/settings.json`) with the `mcp-neo4j-cypher` MCP server, giving Copilot Chat three tools: `read_neo4j_cypher`, `write_neo4j_cypher`, `get_neo4j_schema`. Neo4j must be running before starting a Copilot Chat session that uses these tools. `get_neo4j_schema` requires the APOC plugin.

If the Neo4j password differs from `password`, update `.vscode/settings.json` — do **not** commit credentials to source control.
