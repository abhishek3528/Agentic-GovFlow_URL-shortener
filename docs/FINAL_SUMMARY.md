# Final Summary

## Plan and rationale

The delivery deliberately concentrates on one claim: a governed, non-linear,
stateful SDLC engine demonstrated against real engineering work. The work was
organized into a frozen contract, an orchestration core, an independently useful
URL-shortener product, named controls, adversarial tests, and three sequential
scenarios. Additional planning phases were collapsed into implementation so the
final architecture could be documented from executable code rather than intent.

Python, FastAPI, SQLite, and pytest keep the reviewer path local and readable.
The `TaskExecutor` seam separates execution from governance, while deterministic
handlers and injected time/identity make credential-free replay possible. The
separation is exercised by
`tests/test_engine.py::test_fork_join_executes_end_to_end_with_ordered_events`;
deterministic replay is checked by
`tests/test_engine.py::test_deterministic_execution_replays_byte_stable_structure`.

## Delivered artifacts

- `orchestrator/`: frozen contracts, DAG validation, governed execution,
  append-only events, policies, approvals, bounded recovery, metrics, and
  selective re-planning.
- `app/`: FastAPI create, redirect, stats, health/readiness, and OpenAPI endpoints
  over restart-safe SQLite. Product behavior is checked by
  `tests/test_shortener_service.py`, `tests/test_shortener_api.py`, and
  `tests/test_shortener_live.py`.
- `scenarios/`: the sequential S-01, S-02, and S-03 implementations, CLI,
  dependency-aware runner, and evidence exporter. Dependency ordering is checked
  by `tests/test_scenario_runner.py::test_runner_executes_dependencies_in_order`.
- `evidence/<run_id>/`: generated, never hand-authored bundles containing an
  index, result, contexts, plans, graph, artifacts, decisions, controls, metrics,
  and JSONL events. Bundle contents and hashes are checked by
  `tests/test_scenario_runner.py::test_exporter_writes_indexed_reproducible_bundle`.
- `web/`: a React + TypeScript browser client for the shortener API — shorten a
  link, copy it, open it, and see how many times each link has been opened. It
  consumes the endpoints above and adds no product capability of its own. Its
  only effect on the service is a `CORSMiddleware` registration in
  `app/main.py`, scoped by explicit allowlist to the local development origin
  rather than a wildcard, and disabled entirely by setting
  `URL_SHORTENER_CORS_ORIGINS` to an empty string.
- `README.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`, and this final summary.

## Scenario outcomes

| Scenario | Outcome | Executable evidence |
|---|---|---|
| S-01 greenfield | `succeeded`; implementation forks and joins, an unsafe URL is denied while safe work continues, one transient failure recovers, and release waits for human-attributed approval. | `tests/test_greenfield_scenario.py::test_s01_executes_full_sdlc_fork_join_and_human_release_gate`; `tests/test_greenfield_scenario.py::test_s01_records_denial_recovery_lineage_and_event_derived_metrics` |
| S-02 brownfield | `safe_stopped`; repository impact and red reproduction precede planning, the gated fix passes, then persistent release verification exhausts its budget. Compensation executes before safe-stop. Metrics: `retry_count=1`, `rollback_count=1`, `mttr=5.0s`. | `tests/test_brownfield_scenario.py::test_s02_impact_and_red_fixture_gate_change_planning`; `tests/test_brownfield_scenario.py::test_s02_red_to_green_fork_join_and_gated_fix`; `tests/test_brownfield_scenario.py::test_s02_exhausts_retry_compensates_and_safe_stops_with_metrics` |
| S-03 ambiguous | `succeeded`; human clarification produces requirement v2 while v1 remains, two plan revisions remain reviewable, and selective re-planning ends with 5 artifacts invalidated and 14 retained fresh. | `tests/test_ambiguous_scenario.py::test_s03_surfaces_ambiguities_and_pauses_before_v1_normalization`; `tests/test_ambiguous_scenario.py::test_s03_replan_preserves_v1_and_selectively_invalidates_analytics`; `tests/test_ambiguous_scenario.py::test_s03_reapplies_policies_validation_and_final_human_quality_gate` |

`safe_stopped` is S-02's intended governed acceptance outcome, not a hidden
success label. Terminal safe-stop cannot later transition to success
(`tests/test_governance_negative.py::test_terminal_run_states_have_no_exit`).

## Validation

The final suite contains **165 passing tests**, including **78 adversarial tests**
in `tests/test_governance_negative.py`. The full command is documented in
`docs/TESTING.md`.

Evidence is regenerable from `python -m scenarios.cli run all`. In the verified
repeatability comparison, **24 of 30 files were byte-identical**; the remaining
files differed only in run-id/timestamp fields. Canonical file generation,
content hashes, and byte-identical regeneration for a fixed deterministic run
are enforced by
`tests/test_scenario_runner.py::test_exporter_writes_indexed_reproducible_bundle`.
Per-run event ordering is enforced by
`tests/test_events.py::test_store_assigns_monotonic_sequence_per_run`.

S-01 scratch databases now use deterministically closed SQLite connections
inside the existing temporary-directory context. A repeated S-01 acceptance run
leaves the `tmp/s01-smoke-*` directory count unchanged
(`tests/test_greenfield_scenario.py::test_s01_executes_full_sdlc_fork_join_and_human_release_gate`).

## Risks and trade-offs

- The engine is a local prototype, not a distributed scheduler. SQLite provides
  a credible restart-safe product slice, not multi-node scale or a production
  SLO. Restart persistence is checked by
  `tests/test_shortener_service.py::test_links_survive_repository_restart`.
- Human decisions in scenarios are deterministic, human-attributed fixtures;
  they are not backed by an external identity provider. The invariant that an
  agent cannot grant approval is nevertheless enforced by
  `tests/test_governance_negative.py::test_engine_refuses_an_agent_granted_approval`.
- Compensation demonstrates named workflow state restoration, not general
  source-control, deployment, or database rollback. Ordering before safe-stop is
  checked by `tests/test_governance_negative.py::test_compensation_runs_before_the_safe_stop`.
- URL safety is intentionally bounded to validation and scheme policy, not a
  comprehensive abuse-detection system. Denied schemes and fail-closed unknown
  policies are checked by
  `tests/test_governance_negative.py::test_url_safety_policy_denies_a_prohibited_destination`
  and `tests/test_governance_negative.py::test_an_unknown_policy_id_fails_closed`.
- Analytics exposes counts, recent timestamps, and UTC-day buckets without raw
  client identity. It does not provide geolocation, fingerprinting, local-time
  bucketing, or a broader analytics platform. Schema privacy and day aggregation
  are checked by
  `tests/test_shortener_service.py::test_click_storage_contains_no_raw_client_identifier_columns`
  and `tests/test_shortener_service.py::test_stats_include_coarse_daily_aggregates_without_client_identity`.

## Assumptions

- Review runs use the repository virtual environment at
  `D:\URL-Project\.venv\Scripts\python.exe` and can bind a local loopback port.
- Deterministic clock/id fixtures are appropriate for scenario evidence; live
  service operation uses the injected system clock. Repeatability is checked by
  `tests/test_contracts.py::test_deterministic_pair_reproduces_across_runs`.
- S-01 -> S-02 -> S-03 is one evolving codebase, so later scenarios require
  earlier terminal results. This ordering is checked by
  `tests/test_scenario_runner.py::test_runner_executes_dependencies_in_order`.

## Limitations

Lane E, the optional model-backed executor, was **not built**. The deterministic
path is the only executor. There is no Docker packaging, rate limiting, custom
aliases, QR codes, or authentication platform. The product and orchestrator
remain bounded demonstrations; they make no claim of production deployment,
formal compliance certification, or enterprise identity assurance.

There is no graphical interface to the orchestration engine. It is driven by its
CLI and reviewed through its evidence bundles, and the browser client under
`web/` is a client for the URL-shortener API rather than for the engine — no
governed workflow depends on it.
