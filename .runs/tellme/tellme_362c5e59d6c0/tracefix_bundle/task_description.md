# TraceFix Dry-Run Task

## User Query
How many people are in the room in total?

## Scope
- space_id: smart_room_1
- privacy boundary: CityOS structured context only
- raw sensor policy: no raw sensor data may be used; inputs are CityOS structured context, not raw sensor data

## Time Windows
- none specified

## Required Modalities
- video

## Candidate Harnesses
- answer_synthesis_harness
- occupancy_context_harness

## Application Goal
- failure_condition: Return insufficient_evidence when the bounded evidence is not enough.
- goal_type: occupancy_count
- non_goals: ['identify person', 'face recognition', 'medical diagnosis', 'infer cause or intent']
- success_condition: Return a privacy-bounded answer with confidence and evidence references.
- user_intent: occupancy_lookup

## Evidence Plan
- conflicting_evidence_checks: []
- minimum_sufficient_evidence: ['occupancy_context_harness_packet']
- primary_evidence: ['occupancy_context_harness_packet']
- supporting_evidence: []

## Answer Packet Requirements
- allowed_claims: ['bounded_count']
- answer_type: direct_answer
- fallback_answer_type: insufficient_evidence
- forbidden_claims: ['raw_sensor_access', 'face_identity', 'personal_identity', 'medical_diagnosis', 'unsupported_behavioral_inference']
- must_include_confidence: True
- must_include_evidence_refs: True
- must_include_limitations: True
- required_fields: ['answer', 'confidence', 'evidence_refs', 'caveats', 'privacy_scope', 'limitations']

## Allowed Claims
- bounded_count

## Forbidden Claims
- raw_sensor_access, face_identity, personal_identity, medical_diagnosis, unsupported_behavioral_inference

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
