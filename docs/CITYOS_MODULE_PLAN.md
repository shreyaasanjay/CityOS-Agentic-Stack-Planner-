# TraceFix Verified Intermediary Expression

TraceFix is the planner and verifier in the TraceFix to CityOS pipeline. It turns structured application intent into a verified intermediary expression: application goals, agent specs, topology, protocol artifacts, TLA+/PlusCal outputs, verification results, state machines, prompt files, and monitor requirements.

TraceFix does not run production agents. It does not import CityOS as a Python library, and it does not create Docker containers. Those responsibilities belong to later CityOS stages.

## Pipeline Boundary

The intended production flow is:

1. TeLLMe decomposes user intent into structured application requirements.
2. TraceFix consumes those requirements and generates the verified workspace.
3. TraceFix emits `spec/cityos_module_plan.json`.
4. CityOS Synthesizer consumes the verified intermediary expression.
5. CityOS Synthesizer builds one deployable CityOS app/container per generated agent.
6. CityOS Synthesizer builds the runtime monitor as its own CityOS app/container.
7. CityOS Runtime OS runs the modules and controls lifecycle, permissions, sensors, privacy, ConcordFS communication, and monitoring.

TraceFix outputs a verified blueprint. CityOS Synthesizer builds deployable modules from that blueprint. CityOS Runtime OS runs and enforces everything.

## Generated Intermediary Expression

After a successful `tracefix design`, the workspace includes:

```text
spec/cityos_module_plan.json
```

You can also regenerate the intermediary expression from an existing verified workspace:

```bash
tracefix export-cityos-plan --workspace workspace/my_task
```

To write the intermediary expression somewhere else:

```bash
tracefix export-cityos-plan --workspace workspace/my_task --out /path/to/cityos_module_plan.json
```

## Intermediary Expression Contents

The intermediary expression includes:

- application name, description, and inferred goals
- TraceFix workspace path, generated timestamp, and verification status
- verified topology from `spec/ir.json`
- allowed communication edges
- allowed transitions if `spec/states.json` exposes them
- paths to generated TLA+/PlusCal and verification artifacts
- generated agents, roles, prompt paths, inputs, outputs, tools, required context, and ConcordFS channel declarations
- runtime monitor requirements and source artifacts
- required external/sensor context placeholders
- explicit CityOS Synthesizer handoff instructions
- explicit list of production responsibilities TraceFix must not perform

## Local Runner Status

`tracefix run` and the runner UI are legacy local-development tools. They are useful for debugging generated workspaces and experiments, but they are not the CityOS production execution boundary.

Production execution should go through:

```bash
tracefix design "application requirement"
tracefix export-cityos-plan --workspace workspace/generated_task
```

The CityOS Synthesizer should then ingest `spec/cityos_module_plan.json` and decide how to package, permission, schedule, and launch the resulting modules.

## ConcordFS Declarations

TraceFix only declares intended communication paths. It does not require a real ConcordFS library during planning or verification.

The intermediary expression declares future channels using a filesystem-oriented shape that CityOS and ConcordFS can own later:

```text
messages/<agent_name>/inbox/*.json
messages/<agent_name>/outbox/*.json
state/*.json
logs/*.jsonl
```

CityOS Runtime OS is responsible for attaching the real ConcordFS implementation, enforcing permissions, mediating communication, and monitoring runtime behavior.
