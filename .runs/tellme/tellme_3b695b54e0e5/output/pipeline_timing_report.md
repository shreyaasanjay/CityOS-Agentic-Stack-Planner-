# TraceFix Pipeline Timing

- Total: 18665.19 ms
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
| tellme_decomposition | 18662.80 | yes |

Recommended next fix: Compare API wait and deterministic stage durations in this report.
