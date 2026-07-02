# TraceFix Dry-Run Task

## User Query
Determine whether a conference meeting may begin by confirming that the number of attendees physically present in the room matches the official attendance roster and that all required participants have checked in. Produce a validated meeting-readiness decision with supporting evidence.

## Scope
- space_id: smart_room_1
- privacy boundary: CityOS structured context only
- raw sensor policy: no raw sensor data may be used; inputs are CityOS structured context, not raw sensor data

## Time Windows
- none specified

## Required Modalities
- context

## Candidate Harnesses
- answer_synthesis_harness

## Application Goal
- failure_condition: Return insufficient_evidence or escalate when the structured context is inadequate.
- goal_type: unsupported
- non_goals: ['identify person', 'face recognition', 'medical diagnosis', 'infer cause or intent']
- success_condition: Return a privacy-bounded answer packet with confidence and evidence refs.
- user_intent: general_lookup

## Evidence Plan
- conflicting_evidence_checks: []
- minimum_sufficient_evidence: ['video_findings_packet']
- primary_evidence: ['video_findings_packet']
- supporting_evidence: []

## Answer Packet Requirements
- allowed_claims: ['insufficient_evidence']
- answer_type: insufficient_evidence
- fallback_answer_type: insufficient_evidence
- forbidden_claims: ['raw_sensor_access', 'face_identity', 'personal_identity', 'medical_diagnosis', 'unsupported_behavioral_inference', 'guessing']
- must_include_confidence: True
- must_include_evidence_refs: True
- must_include_limitations: True
- required_fields: ['answer', 'confidence', 'evidence_refs', 'caveats', 'privacy_scope', 'limitations']

## Allowed Claims
- insufficient_evidence

## Forbidden Claims
- raw_sensor_access, face_identity, personal_identity, medical_diagnosis, unsupported_behavioral_inference, guessing

## Expected Output Contract
- required_fields: answer, confidence, evidence_refs, caveats, privacy_scope, limitations
- field_types:
  - answer: string
  - caveats: array
  - confidence: number
  - evidence_refs: array
  - limitations: array
  - privacy_scope: string

## Privacy Constraints
- Use only CityOS-approved structured context.
- Do not use or request raw sensor data.
- All inputs are CityOS structured context summaries, not raw video, audio, radar, or Wi-Fi artifacts.
