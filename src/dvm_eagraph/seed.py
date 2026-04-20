"""
Seed script for the Application Capability Map graph database.

Graph model:
  (Application)-[:SUPPORTS]->(BusinessCapability)
  (Application)-[:EXPOSES]->(Interface)-[:CONSUMED_BY]->(Application)
  (Interface)-[:CARRIES]->(DataObject)

Run:
  py seed.py
"""

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

BUSINESS_CAPABILITIES = [
    {"id": "bc-customer-mgmt",    "name": "Customer Management",       "description": "Manage customer lifecycle, profiles and segmentation"},
    {"id": "bc-order-mgmt",       "name": "Order Management",          "description": "Create, fulfil and track customer orders"},
    {"id": "bc-finance",          "name": "Financial Management",      "description": "Accounts payable/receivable, general ledger, reporting"},
    {"id": "bc-inventory",        "name": "Inventory Management",      "description": "Track stock levels, warehousing and replenishment"},
    {"id": "bc-hr",               "name": "Human Resources",           "description": "Employee records, payroll and workforce planning"},
    {"id": "bc-analytics",        "name": "Analytics & Reporting",     "description": "Business intelligence and operational dashboards"},
]

APPLICATIONS = [
    {"id": "app-crm",       "name": "CRM System",           "description": "Customer relationship management",       "technology": "Salesforce"},
    {"id": "app-erp",       "name": "ERP System",           "description": "Enterprise resource planning",            "technology": "SAP"},
    {"id": "app-oms",       "name": "Order Management System", "description": "Order capture and fulfilment",         "technology": "Custom Java"},
    {"id": "app-wms",       "name": "Warehouse Management System", "description": "Inventory and warehouse ops",      "technology": "Manhattan WMS"},
    {"id": "app-hrms",      "name": "HR Management System", "description": "HR, payroll and workforce",               "technology": "Workday"},
    {"id": "app-bi",        "name": "BI Platform",          "description": "Analytics and dashboards",                "technology": "Power BI"},
    {"id": "app-ecomm",     "name": "E-Commerce Platform",  "description": "Online storefront and cart",              "technology": "Shopify"},
    {"id": "app-mdm",       "name": "Master Data Hub",      "description": "Master data management and distribution", "technology": "Informatica MDM"},
]

# (source_app_id, target_app_id, interface_name, protocol, data_object_name, data_object_description)
INTERFACES = [
    ("app-crm",   "app-mdm",  "Customer Master Sync",    "REST",      "Customer",      "Customer profile including contact details and segmentation"),
    ("app-mdm",   "app-erp",  "Customer Distribution",   "REST",      "Customer",      "Customer profile including contact details and segmentation"),
    ("app-mdm",   "app-oms",  "Customer Distribution",   "REST",      "Customer",      "Customer profile including contact details and segmentation"),
    ("app-ecomm", "app-oms",  "Order Submission",        "REST",      "Order",         "Order header, lines, quantities and delivery details"),
    ("app-oms",   "app-erp",  "Order Financials",        "REST",      "Order",         "Order header, lines, quantities and delivery details"),
    ("app-oms",   "app-wms",  "Fulfilment Request",      "Message",   "FulfilmentOrder","Pick/pack instructions derived from a sales order"),
    ("app-wms",   "app-oms",  "Shipment Confirmation",   "Message",   "Shipment",       "Despatch advice with tracking details"),
    ("app-wms",   "app-erp",  "Stock Valuation",         "Batch",     "StockPosition",  "Warehouse stock on-hand quantities and values"),
    ("app-erp",   "app-bi",   "Financial Extract",       "Batch",     "FinancialData",  "GL transactions, cost centres and P&L entries"),
    ("app-hrms",  "app-erp",  "Payroll Journal",         "Batch",     "PayrollEntry",   "Payroll cost allocations for financial posting"),
    ("app-hrms",  "app-bi",   "Workforce Metrics",       "Batch",     "EmployeeData",   "Headcount, attrition and workforce KPIs"),
]

# (app_id, capability_id)
APP_CAPABILITIES = [
    ("app-crm",   "bc-customer-mgmt"),
    ("app-erp",   "bc-customer-mgmt"),
    ("app-erp",   "bc-order-mgmt"),
    ("app-erp",   "bc-finance"),
    ("app-erp",   "bc-inventory"),
    ("app-oms",   "bc-order-mgmt"),
    ("app-wms",   "bc-inventory"),
    ("app-hrms",  "bc-hr"),
    ("app-bi",    "bc-analytics"),
    ("app-ecomm", "bc-order-mgmt"),
    ("app-ecomm", "bc-customer-mgmt"),
    ("app-mdm",   "bc-customer-mgmt"),
]

# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def clear_database(tx):
    tx.run("MATCH (n) DETACH DELETE n")


def create_constraints(session):
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Application)        REQUIRE a.id IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (b:BusinessCapability)  REQUIRE b.id IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (d:DataObject)          REQUIRE d.name IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Interface)           REQUIRE i.id IS UNIQUE",
    ]
    for c in constraints:
        session.run(c)


def create_capabilities(tx, capabilities):
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (b:BusinessCapability {id: row.id})
        SET b.name = row.name, b.description = row.description
        """,
        rows=capabilities,
    )


def create_applications(tx, applications):
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (a:Application {id: row.id})
        SET a.name = row.name, a.description = row.description, a.technology = row.technology
        """,
        rows=applications,
    )


def create_app_capability_links(tx, links):
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (a:Application {id: row.app_id})
        MATCH (b:BusinessCapability {id: row.cap_id})
        MERGE (a)-[:SUPPORTS]->(b)
        """,
        rows=[{"app_id": a, "cap_id": c} for a, c in links],
    )


def create_interfaces(tx, interfaces):
    for idx, (src, tgt, iface_name, protocol, do_name, do_desc) in enumerate(interfaces):
        iface_id = f"iface-{idx:03d}"
        tx.run(
            """
            MATCH (src:Application {id: $src})
            MATCH (tgt:Application {id: $tgt})
            MERGE (d:DataObject {name: $do_name})
              SET d.description = $do_desc
            MERGE (i:Interface {id: $iface_id})
              SET i.name = $iface_name, i.protocol = $protocol
            MERGE (src)-[:EXPOSES]->(i)
            MERGE (i)-[:CONSUMED_BY]->(tgt)
            MERGE (i)-[:CARRIES]->(d)
            """,
            src=src, tgt=tgt,
            iface_name=iface_name, protocol=protocol,
            do_name=do_name, do_desc=do_desc,
            iface_id=iface_id,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed():
    uri      = os.environ["NEO4J_URI"]
    username = os.environ["NEO4J_USERNAME"]
    password = os.environ["NEO4J_PASSWORD"]
    with GraphDatabase.driver(uri, auth=(username, password)) as driver:
        driver.verify_connectivity()
        print("Connected to Neo4j.")

        with driver.session() as session:
            print("Clearing existing data...")
            session.execute_write(clear_database)

            print("Creating constraints...")
            create_constraints(session)

            print("Creating BusinessCapability nodes...")
            session.execute_write(create_capabilities, BUSINESS_CAPABILITIES)

            print("Creating Application nodes...")
            session.execute_write(create_applications, APPLICATIONS)

            print("Creating SUPPORTS relationships...")
            session.execute_write(create_app_capability_links, APP_CAPABILITIES)

            print("Creating Interface nodes and relationships...")
            session.execute_write(create_interfaces, INTERFACES)

        print("\nSeed complete. Summary:")
        with driver.session() as session:
            for label in ["Application", "BusinessCapability", "Interface", "DataObject"]:
                count = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
                print(f"  {label}: {count}")

            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            print(f"  Relationships: {rel_count}")


def main():
    seed()


if __name__ == "__main__":
    main()
