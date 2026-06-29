# TraceFix Local UI Bundle

This folder contains convenience scripts for running the TraceFix UIs locally.
Keep this folder at the root of the TraceFix repo.

## What You Get

- TraceFix Studio viewer: `http://127.0.0.1:8787`
- TraceFix Intermediary Planner: `http://127.0.0.1:8788`
- TraceFix CityOS Synthesizer: `http://127.0.0.1:8790`

The viewer reads existing benchmark and artifact files.
The intermediary planner can launch the classic TraceFix LLM pipeline, run the headless `tracefix design` flow, export `spec/cityos_module_plan.json`, and inspect the verified intermediary expression.
The CityOS Synthesizer consumes a verified workspace/intermediary plan and writes Docker-buildable CityOS app folders.

TraceFix design does not execute production agents or provide the CityOS runtime. Local execution remains available under `Legacy Debug` for development experiments. The CityOS Synthesizer packages the intermediary expression into one CityOS app/container per agent plus one monitor app/container. CityOS Runtime OS executes those synthesized modules.

## First-Time Setup

From the repo root:

```powershell
python -m pip install -r local-ui\requirements-ui.txt
```

For Ollama runs, also make sure Ollama is running and the model is pulled:

```powershell
ollama pull llama3.2:3b
ollama serve
```

For the design step, install the external `opencode` CLI separately and make sure it is on `PATH`. Exporting or viewing an existing intermediary plan does not require OpenCode.

## Start The UIs

Start both:

```powershell
.\local-ui\start-both.ps1
```

Start only the viewer:

```powershell
.\local-ui\start-viewer.ps1
```

Start only the intermediary planner:

```powershell
.\local-ui\start-runner.ps1
```

Start only the CityOS Synthesizer:

```powershell
.\local-ui\start-synth.ps1
```

Add `-Open` to open the links in your browser:

```powershell
.\local-ui\start-both.ps1 -Open
```

## Stop The UIs

```powershell
.\local-ui\stop-ui.ps1
```

## Source Files

- Viewer source: `tracefix/ui/`
- Intermediary planner source: `tracefix/runner_ui/`
- CityOS Synthesizer source: `tracefix/cityos_synth_ui/`
- Pipeline fixes needed by the runner: `tracefix/pipeline/cli.py`, `tracefix/pipeline/tool_client.py`, `tracefix/pipeline/pipeline/llm_client.py`

API keys entered in the planner are passed only to the spawned process environment for that design/pipeline run. The UI does not write them to disk.
