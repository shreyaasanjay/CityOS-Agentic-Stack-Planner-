---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS Occupancy_agent, Attendance_records_agent, Badge_check_in_agent, Calendar_updates_agent, Decision_reconciliation

(* --algorithm Protocol {
variables
  occupancy_agent_to_decision_reconciliation = <<>>; \* occupancy_agent -> decision_reconciliation
  attendance_records_agent_to_decision_reconciliation = <<>>; \* attendance_records_agent -> decision_reconciliation
  badge_check_in_agent_to_decision_reconciliation = <<>>; \* badge_check_in_agent -> decision_reconciliation
  calendar_updates_agent_to_decision_reconciliation = <<>>; \* calendar_updates_agent -> decision_reconciliation

macro send(ch, msg) {
  ch := Append(ch, msg);
}

macro receive(ch, var) {
  await Len(ch) > 0;
  var := Head(ch);
  ch := Tail(ch);
}

fair process (occupancy_agent_proc \in {Occupancy_agent})
variables msg = "";
{
  occupancy_agent_start:
    skip; \* independently evaluate observed room occupancy
  occupancy_agent_send:
    send(occupancy_agent_to_decision_reconciliation, "occupancy_evidence");
  occupancy_agent_done:
    skip;
}

fair process (attendance_records_agent_proc \in {Attendance_records_agent})
variables msg = "";
{
  attendance_records_agent_start:
    skip; \* independently evaluate expected attendance records
  attendance_records_agent_send:
    send(attendance_records_agent_to_decision_reconciliation, "attendance_records_evidence");
  attendance_records_agent_done:
    skip;
}

fair process (badge_check_in_agent_proc \in {Badge_check_in_agent})
variables msg = "";
{
  badge_check_in_agent_start:
    skip; \* independently evaluate badge check-in status
  badge_check_in_agent_send:
    send(badge_check_in_agent_to_decision_reconciliation, "badge_check_in_evidence");
  badge_check_in_agent_done:
    skip;
}

fair process (calendar_updates_agent_proc \in {Calendar_updates_agent})
variables msg = "";
{
  calendar_updates_agent_start:
    skip; \* independently evaluate calendar participation updates
  calendar_updates_agent_send:
    send(calendar_updates_agent_to_decision_reconciliation, "calendar_updates_evidence");
  calendar_updates_agent_done:
    skip;
}

fair process (decision_reconciliation_proc \in {Decision_reconciliation})
variables msg = "";
{
  decision_reconciliation_start:
    receive(occupancy_agent_to_decision_reconciliation, msg);
  decision_reconciliation_receive_2:
    receive(attendance_records_agent_to_decision_reconciliation, msg);
  decision_reconciliation_receive_3:
    receive(badge_check_in_agent_to_decision_reconciliation, msg);
  decision_reconciliation_receive_4:
    receive(calendar_updates_agent_to_decision_reconciliation, msg);
  decision_reconciliation_reconcile:
    skip; \* application-level reconciliation and final decision
  decision_reconciliation_done:
    skip;
}

} *)

AllDone == \A p \in {Occupancy_agent, Attendance_records_agent, Badge_check_in_agent, Calendar_updates_agent, Decision_reconciliation}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {Occupancy_agent, Attendance_records_agent, Badge_check_in_agent, Calendar_updates_agent, Decision_reconciliation}: pc[p] \in STRING
  /\ occupancy_agent_to_decision_reconciliation \in Seq(STRING)
  /\ attendance_records_agent_to_decision_reconciliation \in Seq(STRING)
  /\ badge_check_in_agent_to_decision_reconciliation \in Seq(STRING)
  /\ calendar_updates_agent_to_decision_reconciliation \in Seq(STRING)

ChannelsDrained ==
  AllDone => (Len(occupancy_agent_to_decision_reconciliation) = 0 /\ Len(attendance_records_agent_to_decision_reconciliation) = 0 /\ Len(badge_check_in_agent_to_decision_reconciliation) = 0 /\ Len(calendar_updates_agent_to_decision_reconciliation) = 0)

ChannelBound ==
  Len(occupancy_agent_to_decision_reconciliation) <= 3 /\ Len(attendance_records_agent_to_decision_reconciliation) <= 3 /\ Len(badge_check_in_agent_to_decision_reconciliation) <= 3 /\ Len(calendar_updates_agent_to_decision_reconciliation) <= 3

====
