# Fixed public contracts

- `platform.schema.json`: Operation, ConnectorManifest, MappingPackage, and
  InvocationResult definitions for retained artifacts and later drivers.
- `github.schema.json`: fixed list/get/create public input/output shapes, including
  strict positive issue/page numbers, nullable body/next-page, and stable `title`.

These files are platform-owned. Generated packages may supply bounded declarative
mappings and supplemental tests, but must never rewrite public schemas or fixed
acceptance fixtures to make themselves pass. Package validation is not executable
adapter acceptance; runtime driver validation and independent tests come later.
Schema changes must preserve or explicitly version the accepted public contract.
