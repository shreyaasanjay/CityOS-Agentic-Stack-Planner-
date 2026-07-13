"""Service-local schemas for capability publication and authorization workflows."""

from __future__ import annotations

from typing import Any
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ServicePrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    principal_type: Literal["application"] = "application"
    status: Literal["enabled", "disabled"] = "enabled"
    auth_token_env: str
    allowed_endpoints: list[Literal["discovery", "publisher"]] = Field(default_factory=list)


class CapabilityGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str
    principal_id: str
    space_id: str
    capability_ids: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)
    allowed_outputs: list[str] = Field(default_factory=list)
    purpose: str = "answer_smart_room_query"
    expires_at: Optional[str] = None
    enabled: bool = True


class CapabilityRegistrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str
    snapshot_id: str
    registered_capability_ids: list[str] = Field(default_factory=list)
    capability_count: int = 0


class CapabilityStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    last_status_at: str


class CapabilityStatusUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    space_id: str
    available: bool
    last_status_at: str


class CapabilityStateHashes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: str
    capability_state_hash: str
    grant_state_hash: str
    privacy_policy_state_hash: str


class CapabilityAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    timestamp: str
    request_id: Optional[str] = None
    principal_id: Optional[str] = None
    endpoint: str
    space_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    schema_version: Optional[str] = None
    capability_count: int = 0
    grant_ids_used: list[str] = Field(default_factory=list)
    policy_version: str
    capability_state_hash: str
    grant_state_hash: str
    privacy_policy_state_hash: str
    decision: Optional[Literal["allowed", "empty", "rejected", "unauthenticated", "forbidden"]] = None
    error_category: Optional[str] = None


class FilteredSnapshotAuditInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    schema_version: str
    capability_count: int
    grant_ids_used: list[str] = Field(default_factory=list)
    decision: Literal["allowed", "empty"]


class DiscoveryProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    schema_version: Optional[str] = None
    capability_count: int = 0
    validation_outcome: Optional[str] = None
    discovery_source: Optional[str] = None
    policy_version: Optional[str] = None
    capability_state_hash: Optional[str] = None
    grant_state_hash: Optional[str] = None
    privacy_policy_state_hash: Optional[str] = None
    production_discovery_used: bool = False
    privacy_summary: list[str] = Field(default_factory=list)
