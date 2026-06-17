# TraceFix Local UI Bundle

This folder contains convenience scripts for running the TraceFix UIs locally.
Keep this folder at the root of the TraceFix repo.

## What You Get

- TraceFix Studio viewer: `http://127.0.0.1:8787`
- TraceFix Runner: `http://127.0.0.1:8788`

The viewer reads existing benchmark and artifact files.
The runner can launch the classic TraceFix LLM pipeline, run the new headless `tracefix design` flow, or execute a verified workspace with `tracefix run`.

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

For the new OpenCode runtime harness, install the external `opencode` CLI separately and make sure it is on `PATH`.

## Start The UIs

Start both:

```powershell
.\local-ui\start-both.ps1
```

Start only the viewer:

```powershell
.\local-ui\start-viewer.ps1
```

Start only the LLM runner:

```powershell
.\local-ui\start-runner.ps1
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
- Runner source: `tracefix/runner_ui/`
- Pipeline fixes needed by the runner: `tracefix/pipeline/cli.py`, `tracefix/pipeline/tool_client.py`, `tracefix/pipeline/pipeline/llm_client.py`

API keys entered in the runner are passed only to the spawned process environment for that run. The UI does not write them to disk.
