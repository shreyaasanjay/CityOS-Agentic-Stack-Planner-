# Smart Room Agentic Planner

The agentic planner turns a natural-language multi-agent question into a verified CityOS module plan and deployable apps.

The end-to-end flow is:

1. TeLLMe decomposes the user intent into structured application requirements.
2. From the user intent, TeLLMe will decide if a multi-agent protocol layer (TraceFix) is required
3. TraceFix generates an agent/resource/channel protocol.
4. TraceFix verifies the protocol with TLA+/PlusCal and TLC.
5. TraceFix exports `spec/cityos_module_plan.json`.
6. CityOS Synthesizer packages the verified plan into one CityOS app per agent plus one monitor app.
7. CityOS Runtime OS runs the generated apps.

TraceFix is the planner and verifier. It does not run production CityOS agents itself.

## Requirements

- Python 3.11+
- Java 17 for TLC
- `lib/tla2tools.jar`
- OpenCode CLI for LLM design runs
- LLM API keys as needed:
  - TeLLMe: OpenAI key, commonly `gpt-4.1`
  - TraceFix: OpenRouter key, commonly GLM 5.2
  - Optional: Anthropic, Ollama

## First-Time Setup

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,agentic,opencode]"
python -m pip install -r local-ui\requirements-ui.txt

## Copy the environment template if you want CLI runs to auto-load keys:

Copy-Item .env.example .env

## Then fill in any keys you need:
OPENAI_API_KEY=
OPENROUTER_API_KEY=
ANTHROPIC_API_KEY=

## The local UI can also accept API keys directly in the browser. Keys entered there are passed only to the spawned run process and are not written to disk.

## Start The Local UI
## Start the main integrated runner:
.\local-ui\start-runner.ps1 -Port 8788 -Open
Open:
http://127.0.0.1:8788/
To start every local UI:
.\local-ui\start-both.ps1 -Open
This starts:
Viewer:      http://127.0.0.1:8787/
Runner:      http://127.0.0.1:8788/
Synthesizer: http://127.0.0.1:8790/
Stop local UIs with:
.\local-ui\stop-ui.ps1
End-To-End UI Flow
Open the runner at http://127.0.0.1:8788/.
Choose the workflow you want:TraceFix Planner to generate or export a verified intermediary plan.
CityOS Synthesizer to package a verified workspace into CityOS app folders.

Enter the required API keys.
Pick provider/model settings.Use OpenAI or TeLLMe-facing settings for TeLLMe decomposition.
Use OpenRouter/GLM 5.2 or the desired TraceFix model for verification.

Choose either a benchmark task or a custom task.
Click Generate Verified Plan.
Wait for TraceFix to finish:IR generation
PlusCal/TLA+ generation
TLC verification
repair loop if needed
state extraction
prompt generation
CityOS module plan export

Check the output tabs:Log
IR
Intermediary Plan
Protocol
States
TLC Error

Check LLM Usage for token and cost reporting.Mixed model runs show model breakdowns, for example TeLLMe gpt-4.1 and TraceFix glm-5.2.
Deterministic/no-LLM runs show zero tokens instead of unavailable usage.

Switch to CityOS Synthesizer.
Pick the verified workspace.
Confirm readiness checks pass.
Choose the output directory/package prefix.
Click Generate CityOS Artifacts.
Use the generated app folders from:
workspace/<run_id>/output/cityos_synthesis/
CLI Flow
Generate a verified workspace from a natural-language task:
tracefix design "Design a smart room application with independent agents for occupancy, lighting, badge access, and monitoring" --model <model-name> --verbose
Export or regenerate the CityOS module plan:
tracefix export-cityos-plan --workspace workspace/<generated_workspace>
Run the legacy local debug runner only when needed:
tracefix run --local-dev --workspace workspace/<generated_workspace>
Main Outputs
A successful run creates a workspace containing:
workspace/<run_id>/
  spec/
    ir.json
    Protocol.tla
    Protocol.cfg
    states.json
    summary.json
    cityos_module_plan.json
  prompts/
    runtime_a/
    runtime_b/
  llm_usage.json
  output/
    cityos_synthesis/
The most important handoff file is:
spec/cityos_module_plan.json
CityOS Synthesizer consumes that file and writes Docker-buildable CityOS app packages.
Theme Toggle
The runner UI supports dark mode and a beige light mode. Use the theme button in the header to switch modes. The preference is saved locally in the browser.
Troubleshooting
If the UI cannot start, check:
.tracefix-ui\logs\runner.out.log
.tracefix-ui\logs\runner.err.log
If TLC cannot run, check Java and tla2tools.jar.
If an LLM request times out, verify:
the API key is correct
the selected model exists for that provider
network access is available
provider/model settings match the intended stage
If CityOS synthesis is not ready, make sure the workspace contains:
spec/ir.json
spec/states.json
spec/cityos_module_plan.json
prompts/runtime_b/
Project Structure
tracefix/
  pipeline/              Agentic IR -> PlusCal -> TLA+ verification
  runtime/               CLI, CityOS plan export, local runtimes
  runner_ui/             Integrated local planner and synthesizer UI
  cityos_synth_ui/       Standalone CityOS synthesis UI
benchmark/               Benchmark coordination tasks
local-ui/                PowerShell scripts for local UI startup
docs/                    CityOS handoff and architecture notes
workspace/               Generated run workspaces
