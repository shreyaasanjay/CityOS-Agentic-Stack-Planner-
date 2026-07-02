# TraceFix Design Prompt

Brief ID: brief_task_f4c50e6feba5
Query ID: tellme_f4c50e6feba5
Space ID: smart_room_1
Route: multi_agent
Task category: event_detection
Executable: True

## User Query
Determine whether attendance can be verified for an active meeting. Independently evaluate room occupancy observations, expected attendance records, and badge check-in status. Reconcile discrepancies between the evidence sources and generate a final attendance decision with confidence and evidence references.

## Application Goal
- goal_type: event_detection
- user_intent: occupancy_lookup
- success_condition: Return a privacy-bounded answer with confidence and evidence references.
- failure_condition: Return insufficient_evidence when the bounded evidence is not enough.
- non_goals: identify person, face recognition, medical diagnosis, infer cause or intent

## CityOS Capabilities
- snapshot: cap_smart_room_1_cam_audio_v1
- camera_door_01 (video): primary
- camera_room_01 (video): primary
- microphone_array_01 (audio): supporting
- radar_corner_01 (radar): unavailable
- wifi_ap_01 (wifi): unavailable
- available_context_apis: cityos_context_lookup, get_acoustic_event_context, get_anonymous_track_context, get_audio_context, get_audio_level_context, get_audio_source_zone_context, get_camera_coverage_metadata, get_camera_occupancy_context, get_context_by_time_window, get_distress_keyword_context, get_entry_event_context, get_event_context, get_exit_event_context, get_impact_sound_context, get_microphone_coverage_metadata, get_motion_event_context, get_occupancy_context, get_posture_candidate_context, get_raw_audio_reference, get_raw_video_reference, get_room_state, get_speech_activity_context
- coverage_gaps:
  - No available sensor supports 'room_state' context for this space.

## Required Modalities
- context, video

## Candidate Harnesses
- video_context_harness
- answer_synthesis_harness

## Time Windows
- none specified

## Evidence Plan
- primary: video_context_harness_packet
- supporting: none
- minimum_sufficient: video_context_harness_packet
- conflicting_checks: none

## Answer Contract
- answer_type: direct_answer
- fallback_answer_type: insufficient_evidence
- allowed_claims: timestamped_event_summary
- forbidden_claims: raw_sensor_access, face_identity, personal_identity, medical_diagnosis, unsupported_behavioral_inference

## Evidence Card
- card_type: descriptive
- claim_target: observed_room_state

## Privacy Policy
- privacy_scope: cityos_structured_context_only
- raw_sensor_access_allowed: False
- identity_inference_allowed: False
- forbidden_inferences: ['raw_sensor_access', 'face_identity', 'personal_identity', 'speaker_identification', 'unrestricted_transcription', 'medical_diagnosis', 'confirmed_injury', 'emotional_state_inference', 'unsupported_behavioral_inference']
- policy_id: smart_room_1_camera_audio_default

## Escalation Conditions
- insufficient_evidence
- conflicting_structured_context
- privacy_policy_denied

## Caveats
- This is a stub only; TraceFix-main is not invoked in V0.
- Real TraceFix multi-agent execution is not integrated yet.
- LLM reasoning is audit-only; validators and deterministic guardrails remain authoritative.
- No available sensor supports 'room_state' context for this space.
- Only the selected derived-context APIs may be used.
- Use CityOS structured context only.
- Do not request raw sensor access.
- Do not identify a person, diagnose injury, or infer unsupported behavior.

## Execution Note
This is a design prompt for future TraceFix coordination. TraceFix-main is not invoked in V0.
