# TraceFix Pipeline Timing

- Total: 18074.90 ms
- Slowest stage: tellme_decomposition
- Suspected bottleneck: external_model_or_orchestration
- Confidence: medium
- Repair attempts: 0
- Repair stop reason: none
- IR sanitization attempted: no
- IR sanitizer recovered pipeline: no
- Single-agent fast path considered: no
- Single-agent fast path used: no
- Fast-path reason: unavailable
- Fast-path fallback to OpenCode: yes
- Fast-path IR duration: 0.00 ms
- Coord template considered: no
- Coord template used: no
- Coord pattern: none
- Coord confidence: 0.00
- Coord fallback reason: n/a
- Fan-in decision used: no
- Evidence source count: 0
- Evidence sources: none
- Decision agent: n/a
- Template priority: n/a
- Application agents: 0
- Runtime monitors: 1
- Pattern scores: {}
- Pattern repository enabled: None
- Candidate harvest attempted: no
- Candidate saved: no
- Candidate deduplicated: no
- Candidate id: n/a
- Topology hash: n/a
- Candidate usage count: 0
- Harvest skip reason: n/a
- IR fields removed: none
- IR fields normalized: none

| Stage | Duration (ms) | Success |
| --- | ---: | :---: |
| tellme_decomposition | 18071.59 | yes |

Recommended next fix: Compare API wait and deterministic stage durations in this report.
