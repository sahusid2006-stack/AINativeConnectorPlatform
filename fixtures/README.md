# Independent fixtures

Platform-owned fixed fixtures are not generator instructions or editable artifacts.
`github_acceptance.json` freezes selected public behavior; `adversarial.json` retains
attack cases for boundary checks and later source/runtime integration.

The `fixtures` Compose process serves synthetic GitHub only, on the private network:

- Resource: `demo/connector-fixture`; public test token: `simulator-github-token`.
- List/get/create use `/repos/{owner}/{repo}/issues` with GitHub-native payloads.
- List uses `Link` pagination and contains a pull-request record to exclude publicly.
- `X-Fixture-Scenario`: permission, rate_limit, upstream_error, ambiguous_write.
- `/_fixture/reset` sets `docs_revision` (v1/v2/malicious) and `provider_revision`
  (v1/v2) independently; `/_fixture/docs` serves retained synthetic documentation;
  `/_fixture/state` shows synthetic write effects. These controls are private test
  infrastructure, not owner/product APIs.
- Provider v2 uses native `headline`; the public schema still uses `title`.
- Ambiguous create takes effect before a 2-second response delay. A client timeout
  must not be treated as proof that a write failed.

`make verify-fixtures` checks the independent provider and the future publication
checker. It does **not** execute a generated connector. `make verify-security`
checks baked read-only fixtures, no credentials/host mounts/network, process limits,
timeout cleanup, and dependencies. Source-fetch/redirect SSRF and model instruction
injection remain pending integration gates; the malicious docs fixture is data only.
No Slack, SOAP, MCP, live GitHub, or model behavior exists in this slice.
