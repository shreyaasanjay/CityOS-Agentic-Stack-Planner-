"""Top-level TeLLMe Harness V0 lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .agent_loop import SingleAgentLoop
from .cityos_mock import MockCityOSClient
from .context_builder import build_context_window
from .config import get_llm_config
from .llm_agent import DeterministicAgentBackend, LLMAgentBackend
from .llm_client import (
    FakeLLMClient,
    LLMClient,
    OpenAICompatibleLLMClient,
    ResilientLLMClient,
)
from .logging_store import LocalLoggingStore
from .orchestrator import AgentOrchestrator
from .tracefix_adapter import validate_tracefix_task_spec
from .tracefix_bundle import build_tracefix_bundle
from .tracefix_coordination import compile_coordination_plan
from .tracefix_design_prompt import render_tracefix_design_prompt
from .tracefix_toolchain import detect_tracefix_toolchain
from .tracefix_workspace_adapter import TraceFixWorkspaceAdapter
from .evidence_card import build_dryrun_card_preview
from .schemas import (
    AnswerPacket,
    EvidenceCardRequirements,
    SmartspaceExecutionBrief,
    TellMeQuery,
    TraceFixTaskSpec,
)
from .single_agent_runner import SingleAgentRunner
from .tool_executor import ToolExecutor


class TellMeHarness:
    def __init__(
        self,
        fixture_path: str | Path | None = None,
        runs_root: str | Path | None = None,
        agent_backend_mode: str = "deterministic",
        llm_client: Optional[LLMClient] = None,
        llm_api_key: Optional[str] = None,
        llm_model: str = "gpt-4.1-mini",
        execution_mode: str = "planning_only",
    ) -> None:
        # execution_mode:
        #   "planning_only"        — brief + dry-run only (default; unchanged behavior)
        #   "tracefix_design"      — also compile coordination plan + build a real
        #                            TraceFix workspace (IR validated against TraceFix's
        #                            own schema). No TLC.
        #   "tracefix_verify"      — build the workspace, then run TraceFix's real
        #                            generate→translate→TLC pipeline and apply the hard
        #                            verification gate. Skips verification (toolchain_
        #                            unavailable) when Java 17 / tla2tools.jar are absent.
        #   "verified_mock_runtime"— reserved for the future Runtime B slice (builds
        #                            workspace today; runtime not yet integrated).
        self.execution_mode = execution_mode
        self.cityos_client = MockCityOSClient(fixture_path=fixture_path)
        self.logging_store = LocalLoggingStore(runs_root=runs_root)
        self.single_agent_runner = SingleAgentRunner(self.cityos_client)
        self.tool_executor = ToolExecutor(self.cityos_client)
        self.agent_backend_mode = agent_backend_mode
        # NOTE: llm_api_key is held only on the instance and is never written to
        # logging artifacts, prompts, events, or AnswerPacket.raw_outputs.
        self._llm_api_key = llm_api_key
        self._llm_model = llm_model
        self.llm_client = llm_client
        self._decomposition_resilient: Optional[ResilientLLMClient] = None
        self.agent_backend = self._build_agent_backend(agent_backend_mode, llm_client)
        self.single_agent_loop = SingleAgentLoop(self.tool_executor, agent_backend=self.agent_backend)
        self.orchestrator = AgentOrchestrator(
            llm_client=self._build_decomposition_client(llm_client),
            llm_backend_mode=agent_backend_mode,
        )

    def handle_query(
        self,
        user_query: str,
        space_id: str | None = None,
        timestamp: str | None = None,
    ) -> AnswerPacket:
        query = TellMeQuery(
            query_id=self._generate_query_id(),
            user_query=user_query,
            space_id=space_id or "smart_room_1",
            timestamp=timestamp,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        query_analysis, route_decision, execution_plan = self.orchestrator.analyze_and_route(query)

        self.logging_store.write_json(query.query_id, "query.json", query)
        run_info = self.logging_store.get_query_run_info(query.query_id)
        if self.logging_store.fallback_active:
            self.logging_store.append_event(
                query.query_id,
                {
                    "event": "logging_fallback_used",
                    "query_id": query.query_id,
                    "original_runs_root": run_info.get("original_runs_root"),
                    "fallback_runs_root": run_info.get("fallback_runs_root"),
                    "run_dir": run_info["run_dir"],
                },
            )
        self.logging_store.write_json(query.query_id, "query_analysis.json", query_analysis)
        self.logging_store.write_json(query.query_id, "route_decision.json", route_decision)
        self.logging_store.write_json(query.query_id, "execution_plan.json", execution_plan)
        self.logging_store.append_event(
            query.query_id,
            {"event": "query_analyzed", "query_id": query.query_id, "intent": query_analysis.intent},
        )
        self.logging_store.append_event(
            query.query_id,
            {"event": "route_decided", "query_id": query.query_id, "route": route_decision.route},
        )
        self.logging_store.append_event(
            query.query_id,
            {"event": "execution_plan_created", "query_id": query.query_id, "plan_type": execution_plan.plan_type},
        )
        self.logging_store.append_event(
            query.query_id,
            {"event": "agent_backend_selected", "query_id": query.query_id, "agent_backend_mode": self.agent_backend_mode},
        )

        if route_decision.route in {"single_agent", "multi_agent"}:
            # Persist the capability-grounding artifacts (discovery → context → brief).
            if execution_plan.cityos_capability_snapshot is not None:
                self.logging_store.write_json(
                    query.query_id,
                    "cityos_capability_snapshot.json",
                    execution_plan.cityos_capability_snapshot,
                )
                self.logging_store.append_event(
                    query.query_id,
                    {
                        "event": "cityos_capabilities_discovered",
                        "query_id": query.query_id,
                        "snapshot_id": execution_plan.cityos_capability_snapshot.get("snapshot_id"),
                    },
                )
            if execution_plan.room_capability_context is not None:
                self.logging_store.write_json(
                    query.query_id,
                    "room_capability_context.json",
                    execution_plan.room_capability_context,
                )
            if execution_plan.smartspace_execution_brief is not None:
                self.logging_store.write_json(
                    query.query_id,
                    "smartspace_execution_brief.json",
                    execution_plan.smartspace_execution_brief,
                )
                brief = SmartspaceExecutionBrief(**execution_plan.smartspace_execution_brief)
                self.logging_store.write_text(
                    query.query_id,
                    "tracefix_design_prompt.md",
                    render_tracefix_design_prompt(brief),
                )
                self.logging_store.append_event(
                    query.query_id,
                    {
                        "event": "execution_brief_compiled",
                        "query_id": query.query_id,
                        "executable": brief.executable,
                        "clarification_required": brief.ambiguity.clarification_required,
                    },
                )
                if self.execution_mode in {"tracefix_design", "tracefix_verify", "verified_mock_runtime"}:
                    self._build_and_persist_tracefix_workspace(
                        query.query_id,
                        brief,
                        verify=(self.execution_mode == "tracefix_verify"),
                    )
            if execution_plan.llm_decomposition_proposal is not None:
                self.logging_store.write_json(
                    query.query_id,
                    "llm_decomposition_proposal.json",
                    execution_plan.llm_decomposition_proposal,
                )
            if execution_plan.proposal_validation is not None:
                self.logging_store.write_json(
                    query.query_id,
                    "proposal_validation.json",
                    execution_plan.proposal_validation,
                )
                validation_payload = execution_plan.proposal_validation
                self.logging_store.append_event(
                    query.query_id,
                    {
                        "event": "proposal_validated",
                        "query_id": query.query_id,
                        "validation_status": validation_payload.get("validation_status"),
                        "repaired": validation_payload.get("repaired", False),
                    },
                )
            if execution_plan.intent_decomposition is not None:
                self.logging_store.write_json(
                    query.query_id,
                    "intent_decomposition.json",
                    execution_plan.intent_decomposition,
                )
                self.logging_store.append_event(
                    query.query_id,
                    {"event": "intent_decomposed", "query_id": query.query_id},
                )
            tracefix_task_spec = self._build_tracefix_stub(query, route_decision, execution_plan)
            validation_errors = validate_tracefix_task_spec(tracefix_task_spec)
            if validation_errors:
                answer_packet = AnswerPacket(
                    query_id=query.query_id,
                    status="error",
                    answer="The complex query produced an invalid TraceFixTaskSpec dry-run stub.",
                    confidence=0.0,
                    caveats=[
                        "Real TraceFix is not integrated yet in V0.",
                        "The dry-run spec failed validation and was not bundled.",
                    ],
                    route_decision=route_decision.model_dump(),
                    raw_outputs=self._merge_raw_outputs(
                        query.query_id,
                        {
                            "agent_backend_mode": self.agent_backend_mode,
                            "tracefix_task_spec": tracefix_task_spec.model_dump(),
                            "tracefix_validation_errors": validation_errors,
                        },
                    ),
                    tracefix_task_spec=tracefix_task_spec,
                    error_message="; ".join(validation_errors),
                )
                self.logging_store.write_json(query.query_id, "tracefix_task_spec.json", tracefix_task_spec)
                self.logging_store.write_json(query.query_id, "answer_packet.json", answer_packet)
                self.logging_store.append_event(
                    query.query_id,
                    {"event": "tracefix_spec_validation_failed", "query_id": query.query_id},
                )
                return answer_packet

            tracefix_bundle = build_tracefix_bundle(tracefix_task_spec)
            self._write_tracefix_bundle_artifacts(query.query_id, tracefix_task_spec, tracefix_bundle)
            self.logging_store.append_event(
                query.query_id,
                {
                    "event": "tracefix_bundle_written",
                    "query_id": query.query_id,
                    "task_id": tracefix_bundle["task_id"],
                    "agent_count": len(tracefix_bundle["agents"]),
                    "channel_count": len(tracefix_bundle["channels"]),
                    "bundle_dir": "tracefix_bundle",
                },
            )
            bundle_summary = {
                "task_id": tracefix_bundle["task_id"],
                "agent_names": [agent["name"] for agent in tracefix_bundle["agents"]],
                "channel_count": len(tracefix_bundle["channels"]),
                "resource_count": len(tracefix_bundle["resources"]),
            }
            execution_plan = execution_plan.model_copy(
                update={"tracefix_bundle_summary": bundle_summary}
            )
            self.logging_store.write_json(query.query_id, "execution_plan.json", execution_plan)

            # Build a dry-run preview of the front-facing evidence card. No values
            # are fabricated: every metric is a labelled placeholder. The real
            # EvidenceCardPacket is populated only after harness execution.
            card_preview_summary: dict[str, Any] = {}
            if tracefix_task_spec.evidence_card_contract:
                card_contract = EvidenceCardRequirements(**tracefix_task_spec.evidence_card_contract)
                card_preview = build_dryrun_card_preview(
                    card_contract, task_id=tracefix_task_spec.task_id
                )
                self.logging_store.write_json(query.query_id, "evidence_card_packet.json", card_preview)
                card_preview_summary = {
                    "card_type": card_preview.card_type,
                    "metric_count": len(card_preview.metrics),
                    "validation_status": card_preview.validation_status,
                }
                self.logging_store.append_event(
                    query.query_id,
                    {
                        "event": "evidence_card_preview_created",
                        "query_id": query.query_id,
                        "card_type": card_preview.card_type,
                    },
                )

            answer_packet = AnswerPacket(
                query_id=query.query_id,
                status="needs_tracefix",
                answer="This query requires a future TraceFix multi-agent workflow and cannot be fully answered in V0.",
                confidence=0.0,
                caveats=[
                    "Real TraceFix is not integrated yet in V0; TraceFix-main is not invoked.",
                    "The query has been converted into a TraceFixTaskSpec stub for future integration.",
                    "Even simple queries now stop at intent decomposition plus TraceFix dry-run packaging.",
                ],
                route_decision=route_decision.model_dump(),
                raw_outputs=self._merge_raw_outputs(
                    query.query_id,
                    {
                        "agent_backend_mode": self.agent_backend_mode,
                        "tracefix_task_spec": tracefix_task_spec.model_dump(),
                        "tracefix_bundle_summary": bundle_summary,
                        "evidence_card_preview": card_preview_summary,
                    },
                ),
                tracefix_task_spec=tracefix_task_spec,
            )
            self.logging_store.write_json(query.query_id, "tracefix_task_spec.json", tracefix_task_spec)
            self.logging_store.write_json(query.query_id, "answer_packet.json", answer_packet)
            self.logging_store.append_event(
                query.query_id,
                {"event": "tracefix_stub_created", "query_id": query.query_id},
            )
            self.logging_store.append_event(
                query.query_id,
                {"event": "answer_returned", "query_id": query.query_id, "status": answer_packet.status},
            )
            return answer_packet

        if route_decision.route == "needs_clarification":
            answer_packet = AnswerPacket(
                query_id=query.query_id,
                status="needs_clarification",
                answer="Please provide a specific room-state, occupancy, motion, or audio question.",
                confidence=0.0,
                caveats=route_decision.caveats,
                route_decision=route_decision.model_dump(),
                raw_outputs=self._merge_raw_outputs(query.query_id),
            )
            self.logging_store.write_json(query.query_id, "answer_packet.json", answer_packet)
            self.logging_store.append_event(
                query.query_id,
                {"event": "clarification_requested", "query_id": query.query_id},
            )
            return answer_packet

        answer_packet = AnswerPacket(
            query_id=query.query_id,
            status="not_answerable",
            answer="This request is outside the current V0 policy or evidence boundary.",
            confidence=0.0,
            caveats=route_decision.caveats,
            route_decision=route_decision.model_dump(),
            raw_outputs=self._merge_raw_outputs(query.query_id),
        )
        self.logging_store.write_json(query.query_id, "answer_packet.json", answer_packet)
        self.logging_store.append_event(
            query.query_id,
            {"event": "query_not_answerable", "query_id": query.query_id, "route": route_decision.route},
        )
        return answer_packet

    def _build_and_persist_tracefix_workspace(self, query_id: str, brief, *, verify: bool = False) -> None:
        """Compile the coordination plan and build a real TraceFix workspace.

        Deterministic and toolchain-independent: the IR is validated against
        TraceFix's own schema. When ``verify`` is set, TraceFix's real
        generate→translate→TLC pipeline is run and the hard verification gate is
        applied (skipping cleanly when the toolchain is unavailable).
        """
        coordination_plan = compile_coordination_plan(brief)
        self.logging_store.write_json(query_id, "tracefix_coordination_plan.json", coordination_plan)
        self.logging_store.append_event(
            query_id,
            {
                "event": "tracefix_coordination_plan_compiled",
                "query_id": query_id,
                "template": coordination_plan.template,
                "agent_count": len(coordination_plan.agents),
                "executable": coordination_plan.executable,
            },
        )

        run_dir = Path(self.logging_store.ensure_query_dir(query_id))
        workspace_root = run_dir / "tracefix_workspace"
        build_result = TraceFixWorkspaceAdapter().build_workspace(
            brief=brief,
            coordination_plan=coordination_plan,
            workspace_root=workspace_root,
        )
        self.logging_store.write_json(query_id, "tracefix_workspace_metadata.json", build_result)
        self.logging_store.append_event(
            query_id,
            {
                "event": "tracefix_workspace_built",
                "query_id": query_id,
                "ir_valid": build_result.ir_valid,
                "ir_validation_source": build_result.ir_validation_source,
                "spec_file_count": len(build_result.spec_files),
            },
        )

        if verify:
            self._verify_tracefix_workspace(query_id, workspace_root)

    def _verify_tracefix_workspace(self, query_id: str, workspace_root: Path) -> None:
        """Run TraceFix's real TLC pipeline and apply the hard verification gate."""
        from .tracefix_execution_adapter import TraceFixExecutionAdapter

        toolchain = detect_tracefix_toolchain()
        self.logging_store.write_json(query_id, "tracefix_toolchain_status.json", toolchain)

        result = TraceFixExecutionAdapter().verify_workspace(workspace=workspace_root)
        self.logging_store.write_json(query_id, "tracefix_verification_result.json", result)

        # Mirror TLC logs to the run dir (already bounded by the adapter).
        for src_name, dest_name in (
            ("tlc_stdout.log", "tracefix_tlc_stdout.log"),
            ("tlc_stderr.log", "tracefix_tlc_stderr.log"),
        ):
            src = workspace_root / "output" / src_name
            if src.is_file():
                self.logging_store.write_text(query_id, dest_name, src.read_text(encoding="utf-8", errors="ignore"))

        self.logging_store.append_event(
            query_id,
            {
                "event": "tracefix_verification_completed",
                "query_id": query_id,
                "status": result.status,
                "verified": result.verified,
                "executable": result.executable,
                "verification_scope": result.verification_scope,
                "invariants_checked": result.invariants_checked,
                "toolchain_available": toolchain.verification_available,
            },
        )

    def _build_tracefix_stub(
        self,
        query: TellMeQuery,
        route_decision: RouteDecision,
        execution_plan,
    ) -> TraceFixTaskSpec:
        if execution_plan.tracefix_task_spec:
            return TraceFixTaskSpec(**execution_plan.tracefix_task_spec)
        return TraceFixTaskSpec(
            task_id=execution_plan.task_id or f"task_{uuid4().hex[:12]}",
            query_id=query.query_id,
            user_query=query.user_query,
            space_id=query.space_id,
            route=route_decision.route,
            time_windows=list(execution_plan.time_windows),
            required_modalities=list(execution_plan.required_modalities),
            candidate_harnesses=list(execution_plan.required_harnesses),
            application_goal={},
            evidence_plan={},
            answer_packet_requirements={},
            output_contract=dict(execution_plan.output_contract),
            privacy_policy={"privacy_scope": "cityos_structured_context_only"},
            validation_policy={"llm_proposal_untrusted": True},
            escalation_conditions=[],
            forbidden_claims=[],
            allowed_claims=[],
            reasoning_summary="Fallback TraceFix stub built from execution plan only.",
            reason=route_decision.rationale,
            caveats=[
                "This is a stub only; TraceFix-main is not invoked in V0.",
                "Real TraceFix multi-agent execution is not integrated yet.",
                "Execution, checkpointing, and monitored recovery remain future work.",
            ],
        )

    def _generate_query_id(self) -> str:
        return f"tellme_{uuid4().hex[:12]}"

    def _merge_raw_outputs(self, query_id: str, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        raw_outputs = self.logging_store.get_query_run_info(query_id)
        raw_outputs["llm_operational"] = self._llm_operational_metadata()
        if extra:
            raw_outputs.update(extra)
        return raw_outputs

    def _llm_operational_metadata(self) -> dict[str, Any]:
        config = get_llm_config()
        metadata: dict[str, Any] = {
            "agent_backend_mode": self.agent_backend_mode,
            "real_llm_requested": self.agent_backend_mode == "llm",
            "openai_key_configured": bool(self._llm_api_key) or config.has_key,
            "model": self._resolved_llm_model() if self.agent_backend_mode == "llm" else None,
        }
        if self._decomposition_resilient is not None:
            metadata["decomposition"] = self._decomposition_resilient.operational_metadata()
        return metadata

    def _build_agent_backend(self, agent_backend_mode: str, llm_client: Optional[LLMClient]):
        if agent_backend_mode == "deterministic":
            return DeterministicAgentBackend()
        if agent_backend_mode == "fake_llm":
            return LLMAgentBackend(llm_client=llm_client or FakeLLMClient(), mode="fake_llm")
        if agent_backend_mode == "llm":
            if llm_client is not None:
                client = llm_client
            elif self._llm_api_key:
                client = OpenAICompatibleLLMClient.for_openai(
                    api_key=self._llm_api_key,
                    model=self._llm_model,
                )
            else:
                client = OpenAICompatibleLLMClient.from_config()
            return LLMAgentBackend(llm_client=client, mode="llm")
        raise ValueError(
            "Unknown agent_backend_mode: {mode}. Expected one of deterministic, fake_llm, llm.".format(
                mode=agent_backend_mode
            )
        )

    def _build_decomposition_client(self, llm_client: Optional[LLMClient]) -> LLMClient:
        if llm_client is not None:
            return llm_client
        # Real LLM only when explicitly in 'llm' mode AND a key is configured.
        if self.agent_backend_mode == "llm":
            primary = self._build_real_llm_client()
            if primary is not None:
                resilient = ResilientLLMClient(
                    primary=primary,
                    fallback=FakeLLMClient(),
                    model=self._resolved_llm_model(),
                )
                self._decomposition_resilient = resilient
                return resilient
        return FakeLLMClient()

    def _build_real_llm_client(self) -> Optional[OpenAICompatibleLLMClient]:
        if self._llm_api_key:
            return OpenAICompatibleLLMClient.for_openai(
                api_key=self._llm_api_key,
                model=self._llm_model,
            )
        try:
            return OpenAICompatibleLLMClient.from_config()
        except RuntimeError:
            return None

    def _resolved_llm_model(self) -> str:
        if self._llm_api_key:
            return self._llm_model
        return get_llm_config().model

    def _write_tracefix_bundle_artifacts(self, query_id: str, spec: TraceFixTaskSpec, bundle: dict) -> None:
        bundle_dir = "tracefix_bundle"
        self.logging_store.write_text_in_subdir(
            query_id,
            bundle_dir,
            "task_description.md",
            bundle["task_description"],
        )
        self.logging_store.write_json_in_subdir(query_id, bundle_dir, "agents.json", bundle["agents"])
        self.logging_store.write_json_in_subdir(query_id, bundle_dir, "channels.json", bundle["channels"])
        self.logging_store.write_json_in_subdir(query_id, bundle_dir, "tool_manifest.json", bundle["tool_manifest"])
        self.logging_store.write_json_in_subdir(query_id, bundle_dir, "output_contract.json", bundle["output_contract"])
        self.logging_store.write_json_in_subdir(query_id, bundle_dir, "bundle.json", bundle)


def json_dumps_compact(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)
