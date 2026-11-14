"""Fixed public schemas are application-owned, not generated test authority."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
if not CONTRACTS.exists():
    CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "platform.schema.json").read_text())


def validate_contract(name, value):
    schema = {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def validate_package(package, source_ids):
    validate_contract("MappingPackage", package)
    if set(package["source_snapshot_ids"]) != {str(value) for value in source_ids}:
        raise ValueError("Package source evidence must match retained snapshots")
    operations = package["manifest"]["operations"]
    names = [operation["name"] for operation in operations]
    if len(set(names)) != len(names):
        raise ValueError("Operation names must be unique")
    if {mapping["operation"] for mapping in package["mappings"]} != set(names) or len(package["mappings"]) != len(names):
        raise ValueError("Each selected operation requires exactly one mapping")
    for operation in operations:
        Draft202012Validator.check_schema(operation["input_schema"])
        Draft202012Validator.check_schema(operation["output_schema"])
        if not {span["source_id"] for span in operation["source_evidence"]}.issubset(set(package["source_snapshot_ids"])):
            raise ValueError("Operation evidence references an unretained source")
    # Protocol-specific request grammar and execution are implemented in WO-6/native drivers.
