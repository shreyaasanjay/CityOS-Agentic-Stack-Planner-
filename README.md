# Smart Room Agentic Planner

The Smart Room Agentic Planner turns a natural-language multi-agent question into a verified CityOS module plan and deployable apps.

The end-to-end flow is:

1. TeLLMe decomposes the user intent into structured application requirements.
2. TraceFix generates an agent/resource/channel protocol.
3. TraceFix verifies the protocol with TLA+/PlusCal and TLC.
4. TraceFix exports `spec/cityos_module_plan.json`.
5. CityOS Synthesizer packages the verified plan into one CityOS app per agent plus one monitor app.
6. CityOS Runtime OS runs the generated apps.

TraceFix is the planner and verifier. It does not run production CityOS agents itself.

## Requirements

Install or configure the following before running the project:

- Python 3.11+
- Java 17 for TLC
- `lib/tla2tools.jar`
- OpenCode CLI for LLM design runs
- LLM API keys as needed:
  - TeLLMe: OpenAI key, commonly using `gpt-4.1`
  - TraceFix: OpenRouter key, commonly using GLM 5.2
  - Optional: Anthropic, Ollama

## First-Time Setup

From the repo root, create and activate a Python virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the project dependencies.

```powershell
python -m pip install -e ".[test,agentic,opencode]"
python -m pip install -r local-ui\requirements-ui.txt
```

Copy the environment template if you want CLI runs to auto-load keys.

```powershell
Copy-Item .env.example .env
```

Then fill in any keys you need.

```env
OPENAI_API_KEY=
OPENROUTER_API_KEY=
ANTHROPIC_API_KEY=
```

The local UI can also accept API keys directly in the browser. Keys entered there are passed only to the spawned run process and are not written to disk.

## Start The Local UI

Start the main integrated UI.

```powershell
.\local-ui\start-synth.ps1 -Port 8790 -Open
```

Open the UI in your browser.

```text
http://127.0.0.1:8790/
```

For another computer to access the UI on the same network, start the server in LAN mode on the machine that cloned the repo.

```powershell
.\local-ui\start-synth.ps1 -Port 8790 -Lan
```

Then open the printed LAN URL from the other computer, for example:

```text
http://192.168.1.25:8790/
```

If the LAN URL does not connect, allow inbound TCP traffic for port `8790` in Windows Firewall.

You can also start the runner-only UI on port `8788`.

```powershell
.\local-ui\start-runner.ps1 -Port 8788 -Open
```

To start every local UI, run:

```powershell
.\local-ui\start-both.ps1 -Open
```

This starts the following local services:

```text
Viewer:      http://127.0.0.1:8787/
Runner:      http://127.0.0.1:8788/
Synthesizer: http://127.0.0.1:8790/
```

Stop the local UIs with:

```powershell
.\local-ui\stop-ui.ps1
```

## Smartroom Mirror API

The web-data agents use the public read-only smartroom mirror by default:

```text
https://smartroom-mirror.vercel.app/api/v1
```

This mirror has the same endpoint shapes as the LAN smartroom API, but it contains synced recordings only. It does not provide live streams. Frame requests redirect to the nearest pre-extracted thumbnail.

You can still paste a LAN API URL into the UI when running on the lab network.

## End-To-End UI Flow

Open the integrated UI.

```text
http://127.0.0.1:8790/
```

Choose the workflow you want:

- TeLLMe: turn a natural-language smart-room request into a structured task.
- TraceFix: generate or export a verified intermediary plan.
- CityOS Synthesizer: package a verified workspace into CityOS app folders and run web-data agents.

Enter the required API keys.

Pick provider and model settings:

- Use OpenAI or TeLLMe-facing settings for TeLLMe decomposition.
- Use OpenRouter/GLM 5.2 or the desired TraceFix model for verification.

Choose either a benchmark task or a custom task.

Click **Run Full Pipeline** from TeLLMe, or run each stage manually:

1. Process the TeLLMe intent.
2. Continue to TraceFix and click **Generate Verified Plan**.
3. Switch to CityOS Synthesizer and click **Generate CityOS Artifacts**.
4. Click **Run Web Data Apps** to query the smartroom API/mirror and generate the answer packet.

During TraceFix verification, wait for:

- IR generation
- PlusCal/TLA+ generation
- TLC verification
- Repair loop, if needed
- State extraction
- Prompt generation
- CityOS module plan export

Check the output tabs:

- Log
- IR
- Intermediary Plan
- Protocol
- States
- TLC Error

Check **LLM Usage** for token and cost reporting. Mixed-model runs show model breakdowns, for example:

```text
TeLLMe:   gpt-4.1
TraceFix: glm-5.2
```

Deterministic or no-LLM runs show zero tokens instead of unavailable usage.

Generated CityOS app folders are written under the selected workspace output directory, commonly:

```text
workspace/<run_id>/output/cityos_synthesis/
```

Web-data run outputs are kept inside the repo under:

```text
.tracefix-ui/web-data-runs/
```

## CLI Flow

Generate a verified workspace from a natural-language task.

```powershell
tracefix design "Design a smart room application with independent agents for occupancy, lighting, badge access, and monitoring" --model <model-name> --verbose
```

Export or regenerate the CityOS module plan.

```powershell
tracefix export-cityos-plan --workspace workspace/<generated_workspace>
```

Run the legacy local debug runner only when needed.

```powershell
tracefix run --local-dev --workspace workspace/<generated_workspace>
```

## Main Outputs

A successful run creates a workspace containing the following structure:

```text
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
```

The most important handoff file is:

```text
spec/cityos_module_plan.json
```

CityOS Synthesizer consumes that file and writes Docker-buildable CityOS app packages.

## Theme Toggle

The runner UI supports dark mode and beige light mode. Use the theme button in the header to switch modes. The preference is saved locally in the browser.

## Troubleshooting

If the UI cannot start, check the logs.

```text
.tracefix-ui\logs\runner.out.log
.tracefix-ui\logs\runner.err.log
.tracefix-ui\logs\unified-ui.out.log
.tracefix-ui\logs\unified-ui.err.log
```

If `http://127.0.0.1:8790/` does not load, make sure the server is running:

```powershell
.\local-ui\start-synth.ps1 -Port 8790
```

If another computer cannot connect, start with `-Lan` and use the printed LAN URL. Also check Windows Firewall for inbound TCP access to port `8790`.

If TLC cannot run, check that Java is installed and that the TLA tools JAR exists.

```text
lib/tla2tools.jar
```

If an LLM request times out, verify the following:

- The API key is correct.
- The selected model exists for that provider.
- Network access is available.
- Provider and model settings match the intended stage.

If CityOS synthesis is not ready, make sure the workspace contains:

```text
spec/ir.json
spec/states.json
spec/cityos_module_plan.json
prompts/runtime_b/
```

## Project Structure

```text
tracefix/
  pipeline/              Agentic IR -> PlusCal -> TLA+ verification
  runtime/               CLI, CityOS plan export, local runtimes
  runner_ui/             Integrated local planner and synthesizer UI
  cityos_synth_ui/       Standalone CityOS synthesis UI

benchmark/               Benchmark coordination tasks
local-ui/                PowerShell scripts for local UI startup
docs/                    CityOS handoff and architecture notes
workspace/               Generated run workspaces
```