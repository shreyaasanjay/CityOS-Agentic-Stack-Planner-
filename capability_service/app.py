"""FastAPI app for capability discovery and trusted publication."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response, status

from tellme_harness.schemas import CityOSCapabilitySnapshot, CityOSDiscoveryRequestContext

from .audit import new_event_id, now_iso
from .auth import authenticate_principal, require_endpoint_access
from .config import CapabilityServiceConfig, get_capability_service_config
from .filtering import build_filtered_snapshot_with_audit
from .schemas import (
    CapabilityAuditEvent,
    CapabilityRegistrationResult,
    CapabilityStatusUpdateRequest,
    CapabilityStatusUpdateResult,
)
from .repository import ApprovedCapabilitiesRepository, CapabilityRepository


def _resolve_repository(
    config: CapabilityServiceConfig, repository: Optional[CapabilityRepository]
) -> CapabilityRepository:
    if repository is not None:
        return repository

    configured_path = Path(config.approved_capabilities_path)
    if configured_path.exists():
        return ApprovedCapabilitiesRepository(configured_path)

    if config.mode == "production":
        raise FileNotFoundError(
            "Approved capability metadata file does not exist: "
            f"{config.approved_capabilities_path}"
        )

    fallback_path = Path(__file__).resolve().parent / "approved_capabilities.json"
    return ApprovedCapabilitiesRepository(fallback_path)


def create_app(
    *,
    config: Optional[CapabilityServiceConfig] = None,
    repository: Optional[CapabilityRepository] = None,
) -> FastAPI:
    resolved_config = config or get_capability_service_config()
    resolved_repository = _resolve_repository(resolved_config, repository)
    app = FastAPI(title="Capability Registry Service", version="0.2.0")
    app.state.config = resolved_config
    app.state.repository = resolved_repository

    def _write_audit_event(
        *,
        event_type: str,
        endpoint: str,
        request_id: str | None = None,
        principal_id: str | None = None,
        space_id: str | None = None,
        snapshot_id: str | None = None,
        schema_version: str | None = None,
        capability_count: int = 0,
        grant_ids_used: list[str] | None = None,
        decision: str | None = None,
        error_category: str | None = None,
    ) -> None:
        state_hashes = app.state.repository.get_state_hashes()
        app.state.repository.append_audit_event(
            CapabilityAuditEvent(
                event_id=new_event_id(),
                event_type=event_type,
                timestamp=now_iso(),
                request_id=request_id,
                principal_id=principal_id,
                endpoint=endpoint,
                space_id=space_id,
                snapshot_id=snapshot_id,
                schema_version=schema_version,
                capability_count=capability_count,
                grant_ids_used=list(grant_ids_used or []),
                policy_version=state_hashes.policy_version,
                capability_state_hash=state_hashes.capability_state_hash,
                grant_state_hash=state_hashes.grant_state_hash,
                privacy_policy_state_hash=state_hashes.privacy_policy_state_hash,
                decision=decision,
                error_category=error_category,
            )
        )

    def _auth_error_category(exc: HTTPException) -> str:
        detail = str(exc.detail).lower()
        if "missing bearer token" in detail:
            return "missing_bearer_token"
        if "invalid bearer token" in detail:
            return "invalid_bearer_token"
        if "disabled" in detail:
            return "principal_disabled"
        return "authorization_denied"

    def _decision_for_http_error(exc: HTTPException) -> str:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return "unauthenticated"
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            return "forbidden"
        return "rejected"

    def _set_discovery_headers(
        response: Response,
        *,
        request_id: str,
        snapshot_id: str,
        schema_version: str,
        capability_count: int,
        discovery_source: str,
        validation_outcome: str,
    ) -> None:
        state_hashes = app.state.repository.get_state_hashes()
        response.headers["X-Capability-Request-Id"] = request_id
        response.headers["X-Capability-Snapshot-Id"] = snapshot_id
        response.headers["X-Capability-Schema-Version"] = schema_version
        response.headers["X-Capability-Count"] = str(capability_count)
        response.headers["X-Capability-Discovery-Source"] = discovery_source
        response.headers["X-Capability-Validation-Outcome"] = validation_outcome
        response.headers["X-Capability-Policy-Version"] = state_hashes.policy_version
        response.headers["X-Capability-State-Hash"] = state_hashes.capability_state_hash
        response.headers["X-Capability-Grant-State-Hash"] = state_hashes.grant_state_hash
        response.headers["X-Capability-Privacy-Policy-State-Hash"] = state_hashes.privacy_policy_state_hash

    @app.post("/v1/discovery/query", response_model=CityOSCapabilitySnapshot)
    def discovery_query(
        request_context: CityOSDiscoveryRequestContext,
        response: Response,
        authorization: Optional[str] = Header(default=None),
    ) -> CityOSCapabilitySnapshot:
        endpoint = "discovery_query"
        existing_record = app.state.repository.get_space_record(request_context.space_id)
        _write_audit_event(
            event_type="discovery_query_received",
            endpoint=endpoint,
            request_id=request_context.query_id,
            space_id=request_context.space_id,
            schema_version=existing_record.get("schema_version") if isinstance(existing_record, dict) else None,
        )
        try:
            principal = authenticate_principal(
                authorization,
                config=app.state.config,
                principals=app.state.repository.get_principals(),
            )
            require_endpoint_access(principal, "discovery")
        except HTTPException as exc:
            _write_audit_event(
                event_type="discovery_auth_failure",
                endpoint=endpoint,
                request_id=request_context.query_id,
                space_id=request_context.space_id,
                decision=_decision_for_http_error(exc),
                error_category=_auth_error_category(exc),
            )
            raise

        snapshot, audit_info = build_filtered_snapshot_with_audit(
            existing_record,
            request_context,
            principal=principal,
            grants=app.state.repository.get_grants(),
        )
        _set_discovery_headers(
            response,
            request_id=request_context.query_id,
            snapshot_id=audit_info.snapshot_id,
            schema_version=audit_info.schema_version,
            capability_count=audit_info.capability_count,
            discovery_source=snapshot.source,
            validation_outcome="accepted",
        )
        _write_audit_event(
            event_type="discovery_query_completed",
            endpoint=endpoint,
            request_id=request_context.query_id,
            principal_id=principal.principal_id,
            space_id=request_context.space_id,
            snapshot_id=audit_info.snapshot_id,
            schema_version=audit_info.schema_version,
            capability_count=audit_info.capability_count,
            grant_ids_used=audit_info.grant_ids_used,
            decision=audit_info.decision,
        )
        return snapshot

    @app.post("/v1/capabilities/register", response_model=CapabilityRegistrationResult)
    def register_capabilities(
        snapshot: CityOSCapabilitySnapshot,
        authorization: Optional[str] = Header(default=None),
    ) -> CapabilityRegistrationResult:
        endpoint = "capabilities_register"
        try:
            principal = authenticate_principal(
                authorization,
                config=app.state.config,
                principals=app.state.repository.get_principals(),
            )
            require_endpoint_access(principal, "publisher")
        except HTTPException as exc:
            _write_audit_event(
                event_type="registration_rejected",
                endpoint=endpoint,
                principal_id=None,
                space_id=snapshot.space_id,
                snapshot_id=snapshot.snapshot_id,
                schema_version=snapshot.schema_version,
                decision=_decision_for_http_error(exc),
                error_category=_auth_error_category(exc),
            )
            raise
        try:
            stored = app.state.repository.register_snapshot(snapshot)
        except ValueError as exc:
            _write_audit_event(
                event_type="registration_rejected",
                endpoint=endpoint,
                principal_id=principal.principal_id,
                space_id=snapshot.space_id,
                snapshot_id=snapshot.snapshot_id,
                schema_version=snapshot.schema_version,
                capability_count=0,
                grant_ids_used=[],
                decision="rejected",
                error_category="validation_error",
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        capability_count = len(stored.get("sensors", [])) + len(stored.get("context_apis", []))
        registered_capability_ids = [
            f"sensor:{sensor.get('sensor_id')}"
            for sensor in stored.get("sensors", [])
            if isinstance(sensor, dict) and sensor.get("sensor_id")
        ] + [
            f"api:{api.get('api_name')}"
            for api in stored.get("context_apis", [])
            if isinstance(api, dict) and api.get("api_name")
        ]
        _write_audit_event(
            event_type="capability_registered",
            endpoint=endpoint,
            principal_id=principal.principal_id,
            space_id=snapshot.space_id,
            snapshot_id=stored.get("snapshot_id", snapshot.snapshot_id),
            schema_version=stored.get("schema_version", snapshot.schema_version),
            capability_count=capability_count,
            grant_ids_used=[],
            decision="allowed",
        )
        return CapabilityRegistrationResult(
            space_id=snapshot.space_id,
            snapshot_id=stored.get("snapshot_id", snapshot.snapshot_id),
            registered_capability_ids=registered_capability_ids,
            capability_count=capability_count,
        )

    @app.post(
        "/v1/capabilities/{capability_id}/status",
        response_model=CapabilityStatusUpdateResult,
    )
    def update_capability_status(
        capability_id: str,
        status_update: CapabilityStatusUpdateRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> CapabilityStatusUpdateResult:
        endpoint = "capability_status_update"
        try:
            principal = authenticate_principal(
                authorization,
                config=app.state.config,
                principals=app.state.repository.get_principals(),
            )
            require_endpoint_access(principal, "publisher")
        except HTTPException as exc:
            _write_audit_event(
                event_type="status_update_rejected",
                endpoint=endpoint,
                principal_id=None,
                decision=_decision_for_http_error(exc),
                error_category=_auth_error_category(exc),
            )
            raise
        try:
            result = app.state.repository.update_capability_status(capability_id, status_update)
        except KeyError as exc:
            _write_audit_event(
                event_type="status_update_rejected",
                endpoint=endpoint,
                principal_id=principal.principal_id,
                space_id=None,
                capability_count=0,
                grant_ids_used=[],
                decision="rejected",
                error_category="capability_not_found",
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Capability not found.",
            ) from exc
        _write_audit_event(
            event_type="capability_status_updated",
            endpoint=endpoint,
            principal_id=principal.principal_id,
            space_id=result.space_id,
            capability_count=1,
            grant_ids_used=[],
            decision="allowed",
        )
        return result

    return app


app = create_app()
