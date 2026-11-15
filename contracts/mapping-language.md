# Declarative mapping language v1 (M1)

Platform-owned grammar. Generated artifacts supply data, never Python/JavaScript,
imports, shell commands, environment access, filesystem access, or network calls.
Frozen public contracts and independent acceptance fixtures remain authoritative.

Each mapping has `operation`, `driver: "rest"`, `request`, and `output_map`.
`request` allows only literal `base_url`, literal `method` (`GET`/`POST`), literal
`path_template`, optional expression-valued `query`, and optional expression-valued
`json`. Path parameters `{owner}`, `{repo}`, `{issue_number}` select scalar public
inputs and are URL-encoded, not evaluated as code. GET bodies are forbidden.
The current trusted capability profile permits only `http://fixtures:8000` and
its GitHub issue endpoints. Fixture administration endpoints are never permitted.

Expressions recursively construct ordinary objects, lists and JSON scalars.
An object containing a `$` key must be exactly one recognized operator:

| Expression | Meaning |
|---|---|
| `{"$input":"/owner"}` | Bounded JSON Pointer into defaulted public input |
| `{"$native":"/title"}` | Bounded JSON Pointer into native response/current item |
| `{"$literal":value}` | JSON value, without further interpretation |
| `{"$default":[expression,fallback]}` | Fallback only for missing value, not explicit null |
| `{"$each":{"items":expression,"map":expression,"exclude_if_has":"pull_request"}}` | Map an array, optionally exclude objects containing the named field |
| `{"$next_page":true}` | Trusted driver's validated pagination metadata |

JSON Pointer uses `~1`/`~0`, at most 16 segments and 1,024 characters. Arrays in
`$each` have at most 1,000 entries. Payloads/results have at most 2 MiB, 10,000
nodes, and 32 levels; interpreter expansion has an independent 10,000-node budget.
Missing values without explicit defaults fail. Unknown `$` operators fail closed.
These limits are supplemented by actual isolated-container resource enforcement.

Example issue transform: `{"title":{"$native":"/title"},
"number":{"$native":"/number"},"body":{"$native":"/body"},
"state":{"$native":"/state"},"html_url":{"$native":"/html_url"}}`.
Documentation drift to native `headline` changes only the native selector, not
public `title`. Acceptance must verify that such mappings work, not merely parse.

## Trusted driver boundary

Evaluator input is `{mode,mapping,input,native?,next_page?}`; output mode returns
public-shaped data. Request mode returns exactly `{method,url,json}`. No credential
is passed into evaluation. The trusted driver derives resources from the actual
URL, checks the operation/method/body/query capability and public-argument binding,
checks connection policy and pinned network destination, then injects credentials.
Native response JSON and validated pagination metadata return through isolated
output evaluation and frozen public schema/format validation. Raw provider errors
are normalized, not retained as credential-bearing diagnostics. Safe read retry
bounds and write approvals/intents belong to the runtime, not generated mappings.

Version/digest selection is exact and retained. Private candidate-test invocation
is simulator-only; normal owner routes cannot request it. Active registry selection
and controlled publication are later WO-8 integration, not part of this grammar.
