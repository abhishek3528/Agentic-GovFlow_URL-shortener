# Documentation

Written last, in one pass, from finished code rather than ahead of it. Every
governance claim in these documents names the test that backs it; a claim
without an executable check behind it was removed rather than left standing.

| Document | Read it for |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Components and boundaries, the governance/work seam, DAG execution and gates, the state machines, and the append-only event stream every reviewer view projects from |
| [`TESTING.md`](TESTING.md) | The test approach: coverage by file, and the paired-test convention that proves each control is load-bearing rather than vacuous |
| [`FINAL_SUMMARY.md`](FINAL_SUMMARY.md) | Plan and rationale, delivered artifacts, scenario outcomes, validation, risks and trade-offs, assumptions, and limitations |

Start with [`../README.md`](../README.md) for setup and the one command that runs
everything.
