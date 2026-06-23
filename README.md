# TraceFix: Repairing Agent Coordination Protocols with TLA+ Counterexamples

A research platform for verifying LLM-based Multi-Agent Systems (MAS) using TLA+ formal methods. LLMs design coordination protocols as an Intermediate Representation (IR), which compiles to TLA+ specs verified by the TLC model checker. A repair loop fixes violations automatically.

> Reference implementation accompanying the ACM CAIS 2026 paper **[*TraceFix: Repairing Agent Coordination Protocols with TLA+ Counterexamples*](https://arxiv.org/abs/2605.07935)** — 🏆 **ACM CAIS 2026 Best Paper Award**.

## Demo



https://github.com/user-attachments/assets/08c126f2-baf1-42cf-84a6-c969d150d3bc




## Core Idea

LangGraph-style centralized orchestration avoids concurrency — but also limits scalability. This project targets **independent concurrent agents** with shared resources and message channels, where coordination bugs (deadlocks, race conditions, liveness failures) are real risks.

**The pipeline:**
1. LLM generates an Intermediate Representation (IR) describing agents, resources, and channels
2. IR compiles to a TLA+ specification via PlusCal
3. TLC model checker exhaustively explores all interleavings
4. If verification fails, the counterexample trace guides LLM repair
5. Post-verification, state machines are extracted, per-agent prompts are generated, and a verified intermediary expression is emitted

TLC doesn't check business logic — it checks coordination: *"Can two agents hold the same lock? Can the system deadlock? Does every agent eventually terminate?"*

## 🚧 Ongoing Updates

Actively in progress — porting TraceFix onto a more robust harness and generalizing it beyond the bundled benchmarks:

- **A stronger agent harness**
- **More general tasks**
- **Simpler MAS building**

## Project Structure

```
.
├── tracefix/                       # Main package
│   ├── pipeline/                   # Agentic verification pipeline (IR → PlusCal → TLA+)
│   ├── cli/                        # CLI tool: tla-verify-pluscal
│   └── runtime/
│       ├── enforcement/            # Architecture A: runtime enforcement engine
│       ├── monitoring/             # Architecture B: runtime monitoring engine
│       └── baselines/
│           ├── shared_chat/        # Baseline: shared-chat (no protocol)
│           └── null_monitor/       # Baseline: null-monitor (no protocol)
├── benchmark/                      # 48 coordination tasks (16 scenarios × 3 difficulties)
├── lib/                            # tla2tools.jar (download separately, see Requirements)
├── .claude/skills/                 # Claude Code interactive skills
├── pyproject.toml
└── LICENSE
```

Run the pipeline to generate verified workspaces (`ir.json`, `Protocol.tla`, `states.json`, per-agent prompts) and a verified intermediary expression (`spec/cityos_module_plan.json`) locally — see Quick Start.

For the TraceFix to CityOS boundary, see [docs/CITYOS_MODULE_PLAN.md](docs/CITYOS_MODULE_PLAN.md). TraceFix emits the verified blueprint; CityOS Synthesizer later builds one CityOS app/container per agent and one separate monitor app/container; CityOS Runtime OS executes and enforces lifecycle, permissions, sensors, privacy, ConcordFS communication, and monitoring.

## Verification Pipeline

**`tracefix/pipeline/`** — Agentic pipeline (IR → PlusCal → TLA+)
- `pipeline/pluscal_generator.py` → `pluscal_compiler.py` → `pluscal_parser.py` (tree-sitter)
- TLC state space optimizations: ChannelBound CONSTRAINT, agent-specific Next formula, string messages, multi-core TLC (`-workers auto`), safety-only verification
- One channel per directed (from, to) pair — `labels` field distinguishes message types

**`tracefix/cli/`** — CLI tool (installed via `pip install -e .`)
- Commands: `validate`, `scaffold`, `verify`, `extract-states`

## Benchmarks

**`benchmark/`** — 16 scenarios × 3 difficulties = 48 coordination tasks

| # | Scenario | # | Scenario |
|---|----------|---|----------|
| 1 | Shared Codebase Development | 9 | Dining Philosophers |
| 2 | Smart Building | 10 | Parallel Build |
| 3 | Research Writing | 11 | Flexible Manufacturing |
| 4 | Code Collaboration | 12 | Collaborative Kitchen |
| 5 | Medical Consultation | 13 | Pharmaceutical Lab |
| 6 | Codebase Development | 14 | Drug Discovery Pipeline |
| 7 | Document Co-authoring | 15 | Semiconductor Fabrication |
| 8 | API System Development | 16 | CI/CD Pipeline |

Each task has `description.md`, `tools.json` (per-agent tool schemas), and `metadata.json`. Scenarios 12–16 include simulation environments with failure injection (`--difficulty 0-3`).

## Local Runtime Architectures

These are legacy/local-development runners and benchmark harnesses. They consume the same TLC-verified spec and provide fine-grained locking (agents run in parallel, blocking only at contention) — unlike LangGraph's global serialization. They are useful for debugging and experiments, but they are not the CityOS production execution path.

**`tracefix/runtime/enforcement/`** — **Enforcement**: Runtime mediator structurally prevents coordination violations. Agents are unaware of locks/channels.

**`tracefix/runtime/monitoring/`** — **Monitoring + correction**: Agents autonomously call coordination tools (`acquire_lock`, `send_message`, etc.); the monitor validates every operation against the verified state machine (`states.json`). On an out-of-order call it **corrects** the agent — the op is blocked before any effect and the legal next actions are returned as guidance (e.g. "do `acquire_lock("DOC")` instead") — and after a bounded number of unrecovered corrections the agent fails honestly (never loops forever, never fakes success).

**`tracefix/runtime/baselines/shared_chat/`** and **`tracefix/runtime/baselines/null_monitor/`** — Baselines without protocol monitoring, for comparison experiments.

## Orchestration Workflow

```
Task Description
    ↓
Phase 1: Structured Analysis → ir.json
    ↓
    tla-verify-pluscal scaffold ir.json → Protocol.tla + Protocol.cfg
    ↓
Phase 2: Write PlusCal Process Bodies
    ↓
Phase 2.5: Semantic Fidelity Check
    ↓
Phase 3: tla-verify-pluscal verify . → TLC (repair loop on failure)
    ↓
Phase 4: tla-verify-pluscal extract-states . → states.json
    ↓
Phase 5: Generate per-agent prompts → prompts/runtime_a/ + prompts/runtime_b/
    ↓
Phase 6: Emit verified intermediary expression → spec/cityos_module_plan.json
```

### Using Claude Code (Recommended)

```
> /tla-verify-pluscal
"Design a protocol for task 3E (Two-Author Research Report)"
```

### Using CLI Directly

```bash
pip install -e .
tla-verify-pluscal validate ir.json
tla-verify-pluscal scaffold ir.json -o workspace/my_task/
# (edit Protocol.tla to fill in process bodies)
tla-verify-pluscal verify workspace/my_task/
tla-verify-pluscal extract-states workspace/my_task/
```

### Output Artifacts

```
workspace/my_task/
├── ir.json              # IR specification (agents, resources, channels)
├── Protocol.tla         # PlusCal source + translated TLA+
├── Protocol.cfg         # TLC configuration
├── states.json          # Extracted state machine for runtime
├── summary.json         # Repair tracking
└── prompts/
    ├── runtime_a/       # Per-agent prompts for enforcement runtime
    └── runtime_b/       # Per-agent prompts for monitoring runtime
```

## Quick Start

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"        # package + test deps (pytest, pytest-asyncio)
pip install openai anthropic    # LLM providers (pipeline + runtimes)

# Run tests
pytest tracefix/pipeline/tests/ -v             # Pipeline tests
pytest tracefix/runtime/enforcement/tests/ -v                 # Runtime A tests
pytest tracefix/runtime/monitoring/tests/ -v                 # Runtime B tests
pytest benchmark/tests/ -v                 # Benchmark tests

# Run the agentic verification pipeline (produces a verified workspace/ )
python -m tracefix.pipeline --benchmark 3E --verbose

# Export a verified intermediary expression from a generated workspace
tracefix export-cityos-plan --workspace workspace/3E

# Legacy local debug only: run agents with monitoring against the generated workspace
python -m tracefix.runtime.monitoring run --task 3E --workspace workspace/3E --verbose

# ...add --live to watch the coordination in real time in your browser during the run
# (D3 + live SSE at http://localhost:8765; a static run_trace.html is also saved + opened)
python -m tracefix.runtime.monitoring run --task 3E --workspace workspace/3E --verbose --live

# Baseline runtimes (no protocol monitoring)
python -m tracefix.runtime.baselines.shared_chat run --task 3E --verbose
python -m tracefix.runtime.baselines.null_monitor run --task 3E --verbose
```

**Requirements:**
- Python 3.11+ (3.13 tested)
- Java 17 (for TLC): any `java` 17 on your `PATH` works, or override with `TLA_VERIFY_JAVA` / `--java-path` (on macOS it also auto-detects Homebrew's `/opt/homebrew/opt/openjdk@17/bin/java`)
- `lib/tla2tools.jar` v1.8.0 (not in git, download from [TLA+ releases](https://github.com/tlaplus/tlaplus/releases))
- API keys in `.env`: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`

## Verified Properties

TLC exhaustively checks these properties on every generated specification:

| Property | What it verifies |
|----------|-----------------|
| Deadlock freedom | No reachable state where all agents are stuck |
| Mutual exclusion | No lock held by two agents simultaneously |
| Termination | All agents eventually reach their terminal state |
| No orphan locks | All locks freed when protocol completes |
| Channel drainage | All messages consumed when protocol completes |
| Type invariant | All variables maintain valid types throughout execution |

## IR Schema

The IR has 3 top-level sections: `agents`, `resources`, `channels`.

- **Resources**: `Lock` (mutual exclusion) or `Counter` (non-negative integer). Counter = shared resource pool (API rate limits, GPU slots), NOT loop bounds.
- **Channels**: Unbounded FIFO queues between agents. One channel per directed (from, to) pair; `labels` field distinguishes message types.

Agent behavior is expressed as PlusCal process bodies. State machines are extracted post-verification into `states.json`, which is the ground truth for runtime monitoring and prompt generation.

## Citation

If you use TraceFix in your research, please cite:

```bibtex
@inproceedings{xia2026tracefix,
  title     = {TraceFix: Repairing Agent Coordination Protocols with TLA+ Counterexamples},
  author    = {Xia, Shuren and Li, Qiwei and Ehsan, Taqiya and Ortiz, Jorge},
  booktitle = {ACM Conference on AI and Agentic Systems (CAIS '26)},
  year      = {2026},
  doi       = {10.1145/3786335.3813159},
  url       = {https://arxiv.org/abs/2605.07935}
}
```

## License

MIT — see [LICENSE](LICENSE).
