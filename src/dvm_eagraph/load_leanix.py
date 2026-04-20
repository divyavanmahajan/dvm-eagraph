"""
load_leanix — Download LeanIX factsheets and relationships, then load into Neo4j.

Uses a YAML mapping file to control which FactSheet types to load and how
Neo4j node labels and relationship type names are derived.

Usage
-----
    # Step 1 (one-time): scan your LeanIX workspace and generate the mapping YAML
    dvm-eagraph --generate-mapping

    # Step 2: full run (download from LeanIX + load into Neo4j)
    dvm-eagraph

    # Use a custom mapping file
    dvm-eagraph --mapping my-mapping.yaml

    # Download only (saves JSON, skips Neo4j)
    dvm-eagraph --skip-neo4j

    # Import every FactSheet type (ignore mapping filter)
    dvm-eagraph --all-factsheets

    # Load from previously saved JSON (skips LeanIX download)
    dvm-eagraph --skip-download

    # Custom proxy URL
    dvm-eagraph --proxy http://localhost:8765/graphql

Prerequisites
-------------
1. Start the dvm-leanix proxy in a separate terminal:
       dvm-leanix serve
2. Ensure Neo4j is running and .env contains NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD.
"""

import argparse
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv
from lean_ix.download import (
    _BASE_SUBSELECT,
    _SAFE_BASE_FIELDS,
    build_query,
    build_relations_query,
    fetch_all,
    fetch_all_relations,
    introspect_type,
    list_relation_fields,
    write_json,
)
from neo4j import GraphDatabase

load_dotenv()

_PACKAGE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PROXY = "http://localhost:8765/graphql"
DEFAULT_DATA_DIR = Path("data/leanix")
DEFAULT_MAPPING_FILE = Path("metamodel-mapping.yaml")
METAMODEL_MD = Path("leanix-metamodel.md")

# Fallback list used only when no mapping file is present and --generate-mapping
# has not been run.
_BUILTIN_FACTSHEET_TYPES = ["Application", "BusinessCapability", "Interface", "Organization"]

# ---------------------------------------------------------------------------
# Reasonable relationship defaults derived from the LeanIX metamodel diagram.
# Keys are LeanIX relation field names; values are the Neo4j relationship types
# that will be used in generated YAML files and as the auto-convert fallback.
# ---------------------------------------------------------------------------

DEFAULT_RELATIONS: dict[str, str] = {
    # Application
    "relApplicationToBusinessCapability": "SUPPORTS",
    "relApplicationToInterface":          "PROVIDES",
    "relApplicationToDataObject":         "CRUD",
    "relApplicationToITComponent":        "RUNS_ON",
    "relApplicationToOrganization":       "OWNED_BY",
    "relApplicationToProvider":           "PROVIDED_BY",
    "relApplicationToInitiative":         "AFFECTED_BY",
    "relApplicationToPlatform":           "PART_OF",
    # Interface
    "relInterfaceToApplication":          "CONSUMED_BY",
    "relInterfaceToDataObject":           "TRANSFERS",
    "relInterfaceToITComponent":          "RUNS_ON",
    # BusinessCapability
    "relBusinessCapabilityToApplication":    "SUPPORTED_BY",
    "relBusinessCapabilityToBusinessContext":"ASSOCIATED_WITH",
    "relBusinessCapabilityToOrganization":   "OWNED_BY",
    # Organization
    "relOrganizationToApplication":          "OWNS",
    "relOrganizationToDataObject":           "OWNS",
    "relOrganizationToObjective":            "OWNS",
    "relOrganizationToPlatform":             "OWNS",
    "relOrganizationToBusinessCapability":   "SUPPORTS",
    # ITComponent
    "relITComponentToApplication":           "HOSTS",
    "relITComponentToProvider":              "OFFERED_BY",
    "relITComponentToTechCategory":          "BELONGS_TO",
    "relITComponentToInterface":             "HOSTS",
    # Provider
    "relProviderToITComponent":              "OFFERS",
    # Initiative
    "relInitiativeToApplication":            "AFFECTS",
    "relInitiativeToBusinessCapability":     "AFFECTS",
    "relInitiativeToInterface":              "AFFECTS",
    "relInitiativeToITComponent":            "AFFECTS",
    "relInitiativeToPlatform":               "AFFECTS",
    "relInitiativeToObjective":              "IMPROVES",
    "relInitiativeToProvider":               "AFFECTS",
    # Platform
    "relPlatformToObjective":                "SUPPORTS",
    "relPlatformToBusinessCapability":       "SUPPORTS",
    # DataObject
    "relDataObjectToApplication":            "USED_BY",
    "relDataObjectToInterface":              "CARRIED_BY",
    # Generic / structural
    "relToParent":                           "CHILD_OF",
    "relToSuccessor":                        "SUCCEEDED_BY",
    "relToPredecessor":                      "PRECEDES",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ssl_context() -> ssl.SSLContext:
    """Legacy SSL context that tolerates corporate SSL-inspection proxies."""
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def rel_name_to_neo4j(leanix_rel: str) -> str:
    """Convert a LeanIX relation field name to a Neo4j relationship type string.

    Checks DEFAULT_RELATIONS first; falls back to stripping 'rel' and converting
    camelCase → UPPER_SNAKE_CASE.
    """
    if leanix_rel in DEFAULT_RELATIONS:
        return DEFAULT_RELATIONS[leanix_rel]
    name = leanix_rel[3:] if leanix_rel.startswith("rel") else leanix_rel
    name = re.sub(r"([A-Z])", r"_\1", name).upper().strip("_")
    return name


def _is_neo4j_primitive(v) -> bool:
    if isinstance(v, (str, int, float, bool)):
        return True
    if isinstance(v, list):
        return all(isinstance(i, (str, int, float, bool)) for i in v)
    return False


def _coerce_neo4j_value(v):
    """Coerce a value to a Neo4j-compatible type.

    Primitives and lists of primitives are returned as-is.
    Objects (dicts) and lists of objects are serialised to a JSON string so that
    structured LeanIX fields like ``externalId`` are preserved rather than dropped.
    None is returned for values that cannot be meaningfully stored.
    """
    if v is None:
        return None
    if _is_neo4j_primitive(v):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return None


def clean_props(record: dict) -> dict:
    """Convert a LeanIX record to a dict of Neo4j-compatible property values.

    Primitive values and lists of primitives are kept as-is.
    Object-structured fields (dicts, lists of objects) are serialised to JSON
    strings so that fields like ``externalId`` are preserved.
    Keys whose value cannot be represented (e.g. None after coercion) are dropped.
    """
    result = {}
    for k, v in record.items():
        coerced = _coerce_neo4j_value(v)
        if coerced is not None:
            result[k] = coerced
    return result


# ---------------------------------------------------------------------------
# Mapping: generate and load
# ---------------------------------------------------------------------------


def _gql_request(proxy: str, query: str, ssl_verify) -> dict:
    """Send a raw GraphQL POST to *proxy* and return the parsed JSON body."""
    payload = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        proxy,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    ctx = ssl_verify if isinstance(ssl_verify, ssl.SSLContext) else None
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.load(resp)


def discover_factsheet_types(proxy: str, ssl_verify) -> tuple[list[str], dict[str, list[str]]]:
    """Return (main_types, subtype_map) discovered from the LeanIX GraphQL schema.

    main_types:   OBJECT types implementing BaseFactSheet / FactSheet.
    subtype_map:  {TypeName: [SubtypeName, ...]} for types whose subtypes are
                  exposed as separate OBJECT types implementing the parent type.
                  Empty dict when the workspace has no schema-level subtypes.
    """
    result = _gql_request(proxy, "{ __schema { types { name kind interfaces { name } } } }", ssl_verify)
    types = result.get("data", {}).get("__schema", {}).get("types", [])

    main_types = sorted(
        t["name"] for t in types
        if t["kind"] == "OBJECT"
        and any(i["name"] in ("BaseFactSheet", "FactSheet") for i in (t["interfaces"] or []))
    )
    main_set = set(main_types)

    # Subtypes: OBJECT types whose interfaces include one of the main FactSheet types
    subtype_map: dict[str, list[str]] = {}
    for t in types:
        if t["kind"] != "OBJECT":
            continue
        for iface in (t["interfaces"] or []):
            if iface["name"] in main_set:
                subtype_map.setdefault(iface["name"], []).append(t["name"])
    for key in subtype_map:
        subtype_map[key].sort()

    return main_types, subtype_map


def discover_subtypes_from_schema(proxy: str, type_name: str, ssl_verify) -> list[str]:
    """Try to find subType enum values for a FactSheet type from the live schema.

    Returns a list of subtype names, or an empty list if not exposed.
    """
    try:
        result = _gql_request(proxy, f"""
        {{
          __type(name: "{type_name}") {{
            fields {{
              name
              type {{ name kind enumValues {{ name }} ofType {{ name kind enumValues {{ name }} }} }}
            }}
          }}
        }}
        """, ssl_verify)
        fields = (result.get("data", {}).get("__type") or {}).get("fields") or []
        for f in fields:
            if f["name"] != "subType":
                continue
            t = f["type"]
            vals = (t.get("enumValues") or []) + ((t.get("ofType") or {}).get("enumValues") or [])
            return [v["name"] for v in vals]
    except Exception:
        pass
    return []


def parse_metamodel_md(md_path: Path) -> dict[str, str]:
    """Parse the Relationships table from a leanix-metamodel.md file.

    Each row (Source, Relationship, Target) is converted to:
      key  → relSourceTypeToTargetType   (spaces stripped from type names)
      value → RELATIONSHIP_LABEL         (label uppercased, spaces/slashes → underscores)

    Returns an empty dict if the file cannot be parsed.
    """
    relations: dict[str, str] = {}
    in_relationships = False

    with open(md_path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()

            if stripped == "## Relationships":
                in_relationships = True
                continue

            if not in_relationships:
                continue

            if stripped.startswith("## "):
                break  # next section — stop

            if not stripped.startswith("|"):
                continue

            parts = [p.strip() for p in stripped.split("|")]
            # Expected: ['', '#', 'Source', 'Relationship', 'Target', 'Kind', '']
            if len(parts) < 6:
                continue

            # Skip header row (| # | Source | ...) and separator row (|---|...)
            if parts[1] in ("#", "---", "") or parts[1].startswith("---"):
                continue

            source = parts[2].replace(" ", "")
            rel_label = parts[3]
            target = parts[4].replace(" ", "")

            if not source or not rel_label or not target:
                continue

            lx_field = f"rel{source}To{target}"
            neo4j_type = re.sub(r"[^A-Z0-9]+", "_", rel_label.upper()).strip("_")

            if lx_field not in relations:  # first label for this pair wins
                relations[lx_field] = neo4j_type

    return relations


def parse_metamodel_md_subtypes(md_path: Path) -> dict[str, list[str]]:
    """Parse the Fact Sheet Types table from leanix-metamodel.md.

    Returns {TypeName: [SubtypeName, ...]} for types that declare subtypes.
    Type and subtype names are normalised to PascalCase (spaces removed,
    original capitalisation preserved — e.g. "AI Agent" → "AIAgent").
    Decoration characters (* † .) are stripped from subtype names.
    """
    subtypes: dict[str, list[str]] = {}
    in_table = False

    with open(md_path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()

            if stripped == "## Fact Sheet Types":
                in_table = True
                continue

            if not in_table:
                continue

            if stripped.startswith("## "):
                break

            if not stripped.startswith("|"):
                continue

            parts = [p.strip() for p in stripped.split("|")]
            # | Layer | Fact Sheet Type | Kind | Subtypes |
            # ['', 'Layer', 'Fact Sheet Type', 'Kind', 'Subtypes', '']
            if len(parts) < 5:
                continue

            # Skip header and separator rows
            if parts[1] in ("Layer", "---", "") or parts[1].startswith("---"):
                continue

            type_name_raw = parts[2]
            subtypes_raw = parts[4] if len(parts) > 4 else ""

            # Skip rows with no subtypes
            if not type_name_raw or subtypes_raw.strip() in ("—", "-", "", "Subtypes"):
                continue

            # Normalise type name: remove spaces
            type_name = type_name_raw.replace(" ", "")

            # Parse subtype list: split on commas and semicolons, strip decoration chars
            subtype_list = []
            for st in re.split(r"[,;]", subtypes_raw):
                cleaned = re.sub(r"[*†.\u2020\u2021]", "", st).strip()
                if cleaned and cleaned not in ("—", "-"):
                    subtype_list.append(cleaned.replace(" ", ""))

            if subtype_list:
                subtypes[type_name] = subtype_list

    return subtypes


def generate_mapping_file(proxy: str, ssl_verify, output_path: Path) -> None:
    """Scan the live LeanIX workspace and write a mapping YAML to *output_path*.

    Relationship defaults come from leanix-metamodel.md (if present), falling back
    to the hardcoded DEFAULT_RELATIONS dict. Any relation field not matched by either
    source is auto-converted from camelCase to UPPER_SNAKE_CASE.
    """
    # Load relationship defaults
    if METAMODEL_MD.exists():
        md_defaults = parse_metamodel_md(METAMODEL_MD)
        print(f"[mapping] Loaded {len(md_defaults)} relationship defaults from {METAMODEL_MD}")
    else:
        md_defaults = {}
        print(f"[mapping] {METAMODEL_MD} not found — using built-in relationship defaults")

    # Merge: MD-file entries take priority; hardcoded DEFAULT_RELATIONS fills any gaps
    defaults = {**DEFAULT_RELATIONS, **md_defaults}
    print(f"[mapping] Discovering FactSheet types from {proxy} ...")
    try:
        types, schema_subtypes = discover_factsheet_types(proxy, ssl_verify)
    except Exception as exc:
        print(f"  ERROR scanning LeanIX: {exc}")
        print("  Is the proxy running?  →  dvm-leanix serve")
        sys.exit(1)

    if not types:
        print("  No FactSheet types found. Is the proxy running?  →  dvm-leanix serve")
        sys.exit(1)

    print(f"  Found {len(types)} FactSheet types: {', '.join(types)}")

    # Load subtype defaults from metamodel MD; merge with any schema-discovered subtypes
    md_subtypes = parse_metamodel_md_subtypes(METAMODEL_MD) if METAMODEL_MD.exists() else {}

    # Build factsheet_types section
    fs_section: dict[str, dict] = {}
    for t in types:
        entry: dict = {"node_label": t}

        # Subtypes: schema-discovered takes priority; fall back to MD defaults
        raw_subtypes = schema_subtypes.get(t) or md_subtypes.get(t, [])
        if raw_subtypes:
            entry["subtypes"] = {st: {"node_label": t} for st in raw_subtypes}

        fs_section[t] = entry

    # Build relationships section — introspect main types then subtypes
    rel_section: dict[str, str] = {}

    all_types_to_introspect = list(types)
    for t in types:
        all_types_to_introspect.extend(schema_subtypes.get(t) or md_subtypes.get(t, []))

    for type_name in all_types_to_introspect:
        is_subtype = type_name not in set(types)
        label = f"  Inspecting {'subtype ' if is_subtype else ''}relations for {type_name} ..."
        print(label)
        try:
            type_fields = introspect_type(proxy, type_name, ssl_verify)
            for rf in list_relation_fields(type_fields):
                field_name = rf["name"] if isinstance(rf, dict) else rf
                if field_name not in rel_section:
                    rel_section[field_name] = defaults.get(field_name) or rel_name_to_neo4j(field_name)
        except Exception as exc:
            if is_subtype:
                pass  # subtypes often aren't separate GraphQL types
            else:
                print(f"    WARNING: could not introspect {type_name}: {exc}")

    # Add any defaults not captured via introspection
    for field_name, neo4j_rel in defaults.items():
        if field_name not in rel_section:
            rel_section[field_name] = neo4j_rel

    mapping: dict = {
        "factsheet_types": fs_section,
        "relationships": rel_section,
    }

    header = (
        f"# LeanIX → Neo4j metamodel mapping\n"
        f"# Generated: {date.today()}\n"
        f"#\n"
        f"# factsheet_types: controls which FactSheet types are downloaded/loaded\n"
        f"#   and what Neo4j node label is used for each.\n"
        f"# relationships: maps LeanIX relation field names to Neo4j relationship types.\n"
        f"#\n"
        f"# Edit node_label and relationship values freely; re-run load_leanix.py to apply.\n\n"
    )

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.dump(mapping, fh, default_flow_style=False, sort_keys=True, allow_unicode=True)

    print(f"\n[mapping] Written to {output_path}")
    print(f"  Review and edit the file, then run:  dvm-eagraph --mapping {output_path}")


def load_mapping(mapping_path: Path) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    """Load factsheet types, relationship label map, and subtype map from a YAML mapping file.

    Returns (factsheet_type_names, relation_type_map, subtype_map).
    Exits with a clear message if the file is missing.
    """
    if not mapping_path.exists():
        print(f"ERROR: Mapping file not found: {mapping_path}")
        print(
            "Run the following to generate it from your live LeanIX workspace:\n"
            "  dvm-eagraph --generate-mapping"
        )
        sys.exit(1)

    with open(mapping_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    fs_cfg = data.get("factsheet_types") or {}
    fs_types = list(fs_cfg.keys())
    rel_map = {k: str(v) for k, v in (data.get("relationships") or {}).items()}

    # Extract subtype lists: {TypeName: [subtype, ...]}
    subtype_map: dict[str, list[str]] = {}
    for type_name, cfg in fs_cfg.items():
        if isinstance(cfg, dict) and cfg.get("subtypes"):
            subtype_map[type_name] = list(cfg["subtypes"].keys())

    if not fs_types:
        print(f"ERROR: No factsheet_types found in {mapping_path}")
        sys.exit(1)

    return fs_types, rel_map, subtype_map


def _bundled_mapping() -> Path | None:
    """Return the path to the bundled default metamodel-mapping.yaml, or None."""
    p = _PACKAGE_DIR / "metamodel-mapping.yaml"
    return p if p.exists() else None


def resolve_mapping(
    mapping_arg: str | None,
) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    """Return (factsheet_types, relation_map, subtype_map) from a mapping file or built-in defaults.

    Resolution order:
      1. --mapping <path>  → must exist (hard error if not)
      2. DEFAULT_MAPPING_FILE exists in CWD  → load it
      3. Bundled package default mapping  → load it
      4. Neither exist  → warn and use built-in defaults
    """
    if mapping_arg:
        return load_mapping(Path(mapping_arg))

    if DEFAULT_MAPPING_FILE.exists():
        print(f"[mapping] Using {DEFAULT_MAPPING_FILE}")
        return load_mapping(DEFAULT_MAPPING_FILE)

    bundled = _bundled_mapping()
    if bundled is not None:
        print(f"[mapping] No local {DEFAULT_MAPPING_FILE} found — using bundled default mapping.")
        return load_mapping(bundled)

    print(
        f"[mapping] {DEFAULT_MAPPING_FILE} not found — using built-in defaults "
        f"({', '.join(_BUILTIN_FACTSHEET_TYPES)}).\n"
        f"  Tip: run  dvm-eagraph --generate-mapping  to create a full mapping."
    )
    return _BUILTIN_FACTSHEET_TYPES, DEFAULT_RELATIONS, {}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_factsheets(
    proxy: str,
    ssl_verify,
    types: list[str],
    data_dir: Path,
    subtype_map: dict[str, list[str]] | None = None,
    limit: int | None = None,
) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}

    for type_name in types:
        subtypes = (subtype_map or {}).get(type_name, [])
        subtype_label = f" (subtypes: {', '.join(subtypes)})" if subtypes else ""
        print(f"\n[download] {type_name} factsheets{subtype_label} ...")
        try:
            type_fields = introspect_type(proxy, type_name, ssl_verify)
            base_fields_raw = introspect_type(proxy, "BaseFactSheet", ssl_verify)
            base_field_names = {f["name"] for f in base_fields_raw}

            base_fields = [
                f for f in _SAFE_BASE_FIELDS
                if f in base_field_names or f in _BASE_SUBSELECT
            ]
            if "completion" in base_field_names:
                base_fields.append("completion")

            query = build_query(type_name, type_fields, base_fields)
            records = fetch_all(
                proxy_url=proxy,
                query=query,
                type_name=type_name,
                subtypes=[],
                ssl_verify=ssl_verify,
                verbose=True,
                type_fields=type_fields,
                base_fields=base_fields,
                limit=limit,
            )

            if limit is not None and len(records) >= limit:
                print(f"  [limit] Stopped at {limit} records (limit reached).")

            out_path = data_dir / f"{type_name}.json"
            with open(out_path, "w", encoding="utf-8") as fh:
                write_json(records, fh)

            print(f"  → {len(records)} records  →  {out_path}")
            results[type_name] = records

        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not download {type_name}: {exc}")
            results[type_name] = []

    return results


def download_relations(
    proxy: str,
    ssl_verify,
    types: list[str],
    data_dir: Path,
    limit: int | None = None,
) -> list[dict]:
    seen: set[tuple] = set()
    all_rows: list[dict] = []

    for type_name in types:
        print(f"\n[download] {type_name} relations ...")
        try:
            type_fields = introspect_type(proxy, type_name, ssl_verify)
            relation_fields = list_relation_fields(type_fields)

            if not relation_fields:
                print(f"  No relation fields for {type_name}")
                continue

            query = build_relations_query(type_name, relation_fields)
            rows = fetch_all_relations(
                proxy_url=proxy,
                query=query,
                type_name=type_name,
                relation_fields=relation_fields,
                ssl_verify=ssl_verify,
                verbose=True,
                limit=limit,
            )

            unique_rows = []
            for row in rows:
                key = (row["source_id"], row["relation"], row["target_id"])
                if key not in seen:
                    seen.add(key)
                    unique_rows.append(row)

            if limit is not None and len(rows) >= limit:
                print(f"  [limit] Stopped at {limit} relation rows (limit reached).")

            out_path = data_dir / f"{type_name}_relations.json"
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(unique_rows, fh, indent=2, ensure_ascii=False)

            all_rows.extend(unique_rows)
            print(f"  → {len(unique_rows)} unique relation rows  →  {out_path}")

        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not download {type_name} relations: {exc}")

    return all_rows


def load_saved_json(
    data_dir: Path,
    types: list[str],
    limit: int | None = None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Read factsheets and relations from previously saved JSON files."""
    factsheets: dict[str, list[dict]] = {}
    all_relations: list[dict] = []

    for type_name in types:
        fs_path = data_dir / f"{type_name}.json"
        if fs_path.exists():
            with open(fs_path, encoding="utf-8") as fh:
                records = json.load(fh)
            if limit is not None and len(records) > limit:
                print(f"  [limit] Capping {type_name} at {limit} of {len(records)} records.")
                records = records[:limit]
            factsheets[type_name] = records
            print(f"  Loaded {len(factsheets[type_name])} {type_name} records from {fs_path}")
        else:
            factsheets[type_name] = []
            print(f"  WARNING: {fs_path} not found — skipping {type_name}")

        rel_path = data_dir / f"{type_name}_relations.json"
        if rel_path.exists():
            with open(rel_path, encoding="utf-8") as fh:
                rows = json.load(fh)
            if limit is not None and len(rows) > limit:
                print(f"  [limit] Capping {type_name} relations at {limit} of {len(rows)} rows.")
                rows = rows[:limit]
            all_relations.extend(rows)
            print(f"  Loaded {len(rows)} {type_name} relation rows from {rel_path}")

    seen: set[tuple] = set()
    deduped = []
    for row in all_relations:
        key = (row["source_id"], row["relation"], row["target_id"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    return factsheets, deduped


# ---------------------------------------------------------------------------
# Neo4j loading
# ---------------------------------------------------------------------------


def create_constraints(session, types: list[str]) -> None:
    for label in types:
        session.run(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )


def _merge_nodes(tx, label: str, rows: list[dict]) -> None:
    tx.run(
        f"""
        UNWIND $rows AS row
        MERGE (n:{label} {{id: row.id}})
        SET n += row
        """,
        rows=rows,
    )


def load_nodes(session, label: str, records: list[dict]) -> None:
    rows = [clean_props(r) for r in records if r.get("id")]
    if not rows:
        return
    session.execute_write(_merge_nodes, label, rows)
    print(f"[neo4j] Merged {len(rows)} {label} nodes.")


def _merge_relationships(tx, neo4j_rel: str, rows: list[dict]) -> None:
    tx.run(
        f"""
        UNWIND $rows AS row
        MATCH (src {{id: row.source_id}})
        MATCH (tgt {{id: row.target_id}})
        MERGE (src)-[:{neo4j_rel}]->(tgt)
        """,
        rows=rows,
    )


def load_relationships(session, rows: list[dict], rel_map: dict[str, str]) -> None:
    """Batch-merge relationships grouped by Neo4j relationship type."""
    by_rel: dict[str, list[dict]] = {}
    for row in rows:
        lx_rel = row["relation"]
        neo4j_rel = rel_map.get(lx_rel) or rel_name_to_neo4j(lx_rel)
        by_rel.setdefault(neo4j_rel, []).append(row)

    total = 0
    for neo4j_rel, rel_rows in by_rel.items():
        session.execute_write(_merge_relationships, neo4j_rel, rel_rows)
        print(f"[neo4j] Merged {len(rel_rows)} :{neo4j_rel} relationships.")
        total += len(rel_rows)

    print(f"[neo4j] {total} relationships merged across {len(by_rel)} types.")


def query_db_stats(session) -> dict:
    """Return {label: count, '_relationships': count} for the whole database."""
    stats: dict[str, int] = {}
    result = session.run(
        "MATCH (n) UNWIND labels(n) AS lbl RETURN lbl, count(n) AS c ORDER BY lbl"
    )
    for row in result:
        stats[row["lbl"]] = row["c"]
    stats["_relationships"] = session.run(
        "MATCH ()-[r]->() RETURN count(r) AS c"
    ).single()["c"]
    return stats


def print_stats_comparison(
    before: dict,
    after: dict,
    leanix: dict | None = None,
) -> None:
    """Print a before/after comparison table of node and relationship counts.

    If *leanix* is provided it adds a 'LeanIX' column showing how many
    factsheets/relationships were downloaded from LeanIX for each label.
    """
    node_keys = sorted(k for k in set(before) | set(after) | set(leanix or {}) if k != "_relationships")
    col = max((len(k) for k in node_keys), default=10) + 2

    if leanix is not None:
        header = f"  {'Label':<{col}} {'LeanIX':>9} {'Before':>9} {'After':>9} {'Delta':>9}"
        sep_width = col + 39
    else:
        header = f"  {'Label':<{col}} {'Before':>9} {'After':>9} {'Delta':>9}"
        sep_width = col + 30

    print("\n" + header)
    print("  " + "-" * sep_width)

    for k in node_keys:
        b, a = before.get(k, 0), after.get(k, 0)
        delta = a - b
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        if leanix is not None:
            lx = leanix.get(k, 0)
            lx_str = f"{lx:,}" if lx else "-"
            print(f"  {k:<{col}} {lx_str:>9} {b:>9,} {a:>9,} {delta_str:>9}")
        else:
            print(f"  {k:<{col}} {b:>9,} {a:>9,} {delta_str:>9}")

    # Relationships row
    b, a = before.get("_relationships", 0), after.get("_relationships", 0)
    delta = a - b
    delta_str = f"+{delta}" if delta > 0 else str(delta)
    print("  " + "-" * sep_width)
    if leanix is not None:
        lx = leanix.get("_relationships", 0)
        lx_str = f"{lx:,}" if lx else "-"
        print(f"  {'[Relationships]':<{col}} {lx_str:>9} {b:>9,} {a:>9,} {delta_str:>9}")
    else:
        print(f"  {'[Relationships]':<{col}} {b:>9,} {a:>9,} {delta_str:>9}")


def load_to_neo4j(
    uri: str,
    username: str,
    password: str,
    factsheets: dict[str, list[dict]],
    relations: list[dict],
    rel_map: dict[str, str],
) -> None:
    types = list(factsheets.keys())

    # Build LeanIX counts from the downloaded data
    leanix_counts: dict[str, int] = {label: len(records) for label, records in factsheets.items()}
    leanix_counts["_relationships"] = len(relations)

    with GraphDatabase.driver(uri, auth=(username, password)) as driver:
        driver.verify_connectivity()
        print("\n[neo4j] Connected.")

        with driver.session() as session:
            print("[neo4j] Querying database state before load ...")
            before_stats = query_db_stats(session)

        with driver.session() as session:
            print("[neo4j] Ensuring constraints ...")
            create_constraints(session, types)

            for label, records in factsheets.items():
                if records:
                    load_nodes(session, label, records)

            if relations:
                load_relationships(session, relations, rel_map)

        with driver.session() as session:
            after_stats = query_db_stats(session)

        print("\n[neo4j] Load complete — before/after comparison:")
        print_stats_comparison(before_stats, after_stats, leanix=leanix_counts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download LeanIX factsheets and relationships, then load into Neo4j.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--generate-mapping",
        action="store_true",
        help=(
            f"Scan the live LeanIX workspace and write a mapping YAML "
            f"(default output: {DEFAULT_MAPPING_FILE}). "
            f"Requires the dvm-leanix proxy to be running."
        ),
    )
    parser.add_argument(
        "--mapping",
        default=None,
        metavar="PATH",
        help=(
            f"Path to the metamodel mapping YAML file "
            f"(default: {DEFAULT_MAPPING_FILE} if it exists, otherwise built-in defaults)."
        ),
    )
    parser.add_argument(
        "--proxy",
        default=DEFAULT_PROXY,
        metavar="URL",
        help=f"dvm-leanix GraphQL proxy URL (default: {DEFAULT_PROXY})",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        metavar="PATH",
        help=f"Directory for raw JSON output (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification (insecure; dev only)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip LeanIX download; load from previously saved JSON files in --data-dir",
    )
    parser.add_argument(
        "--skip-neo4j",
        action="store_true",
        help="Download only; do not write to Neo4j",
    )
    parser.add_argument(
        "--all-factsheets",
        action="store_true",
        help=(
            "Discover and import every FactSheet type from the live LeanIX workspace, "
            "ignoring the factsheet_types filter in the mapping file. "
            "Relationship mappings are still loaded from the mapping file (or defaults). "
            "Requires the dvm-leanix proxy to be running."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Cap the number of records downloaded (or loaded from disk) per FactSheet type "
            "and per relationship type. Useful for quickly verifying that downloads and "
            "Neo4j loading are working correctly without processing the full dataset."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    ssl_verify = False if args.no_verify_ssl else make_ssl_context()

    # ── Generate mapping ────────────────────────────────────────────────────
    if args.generate_mapping:
        out = Path(args.mapping) if args.mapping else DEFAULT_MAPPING_FILE
        generate_mapping_file(args.proxy, ssl_verify, out)
        return

    # ── Resolve mapping ─────────────────────────────────────────────────────
    if args.all_factsheets:
        # Discover every FactSheet type live from LeanIX, ignoring the mapping filter.
        print(f"[mapping] --all-factsheets: discovering FactSheet types from {args.proxy} ...")
        try:
            discovered_types, schema_subtypes = discover_factsheet_types(args.proxy, ssl_verify)
        except Exception as exc:
            print(f"  ERROR: could not discover FactSheet types: {exc}")
            print("  Is the proxy running?  →  dvm-leanix serve")
            sys.exit(1)
        if not discovered_types:
            print("  No FactSheet types found. Is the proxy running?  →  dvm-leanix serve")
            sys.exit(1)
        print(f"  Found {len(discovered_types)} types: {', '.join(discovered_types)}")

        # Still load rel_map from the mapping file if present, otherwise use defaults.
        if args.mapping:
            _, rel_map, _ = load_mapping(Path(args.mapping))
        elif DEFAULT_MAPPING_FILE.exists():
            _, rel_map, _ = load_mapping(DEFAULT_MAPPING_FILE)
        else:
            rel_map = DEFAULT_RELATIONS

        factsheet_types = discovered_types
        subtype_map = schema_subtypes
    else:
        factsheet_types, rel_map, subtype_map = resolve_mapping(args.mapping)

    print(f"[mapping] FactSheet types: {', '.join(factsheet_types)}")
    if subtype_map:
        for t, sts in subtype_map.items():
            print(f"  {t} subtypes: {', '.join(sts)}")

    # ── Download or load from disk ───────────────────────────────────────────
    if args.skip_download:
        print(f"\n[load] Reading saved JSON from {data_dir} ...")
        factsheets, relations = load_saved_json(data_dir, factsheet_types, limit=args.limit)
    else:
        factsheets = download_factsheets(
            args.proxy, ssl_verify, factsheet_types, data_dir, subtype_map, limit=args.limit
        )
        relations = download_relations(args.proxy, ssl_verify, factsheet_types, data_dir, limit=args.limit)

    # ── Load into Neo4j ──────────────────────────────────────────────────────
    if not args.skip_neo4j:
        uri = os.environ["NEO4J_URI"]
        username = os.environ["NEO4J_USERNAME"]
        password = os.environ["NEO4J_PASSWORD"]
        load_to_neo4j(uri, username, password, factsheets, relations, rel_map)
    else:
        print("[neo4j] Skipped (--skip-neo4j).")


if __name__ == "__main__":
    main()

