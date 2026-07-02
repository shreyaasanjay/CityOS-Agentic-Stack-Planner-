---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS DEVELOPER_A, DEVELOPER_B, DEVELOPER_C

(* --algorithm Protocol {
variables
  A_to_B = <<>>; \* DEVELOPER_A -> DEVELOPER_B, labels: ['status_report']
  A_to_C = <<>>; \* DEVELOPER_A -> DEVELOPER_C, labels: ['status_report']
  B_to_A = <<>>; \* DEVELOPER_B -> DEVELOPER_A, labels: ['status_report']
  B_to_C = <<>>; \* DEVELOPER_B -> DEVELOPER_C, labels: ['status_report']
  C_to_A = <<>>; \* DEVELOPER_C -> DEVELOPER_A, labels: ['status_report']
  C_to_B = <<>>; \* DEVELOPER_C -> DEVELOPER_B, labels: ['status_report']
  AUTH_MODULE = "FREE"; \* Lock
  DATABASE_MODULE = "FREE"; \* Lock
  API_MODULE = "FREE"; \* Lock

macro send(ch, msg) {
  ch := Append(ch, msg);
}

macro receive(ch, var) {
  await Len(ch) > 0;
  var := Head(ch);
  ch := Tail(ch);
}

macro acquire_lock(lock) {
  await lock = "FREE";
  lock := self;
}

macro release_lock(lock) {
  lock := "FREE";
}

fair process (DEVELOPER_A_proc \in {DEVELOPER_A})
variables msg = "";
{
  DEVELOPER_A_start:
    skip; \* TODO: replace with DEVELOPER_A's protocol logic
  DEVELOPER_A_done:
    skip;
}

fair process (DEVELOPER_B_proc \in {DEVELOPER_B})
variables msg = "";
{
  DEVELOPER_B_start:
    skip; \* TODO: replace with DEVELOPER_B's protocol logic
  DEVELOPER_B_done:
    skip;
}

fair process (DEVELOPER_C_proc \in {DEVELOPER_C})
variables msg = "";
{
  DEVELOPER_C_start:
    skip; \* TODO: replace with DEVELOPER_C's protocol logic
  DEVELOPER_C_done:
    skip;
}

} *)

AllDone == \A p \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C}: pc[p] \in STRING
  /\ AUTH_MODULE \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C, "FREE"}
  /\ DATABASE_MODULE \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C, "FREE"}
  /\ API_MODULE \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C, "FREE"}
  /\ A_to_B \in Seq(STRING)
  /\ A_to_C \in Seq(STRING)
  /\ B_to_A \in Seq(STRING)
  /\ B_to_C \in Seq(STRING)
  /\ C_to_A \in Seq(STRING)
  /\ C_to_B \in Seq(STRING)

NoOrphanLocks ==
  AllDone => (AUTH_MODULE = "FREE" /\ DATABASE_MODULE = "FREE" /\ API_MODULE = "FREE")

ChannelsDrained ==
  AllDone => (Len(A_to_B) = 0 /\ Len(A_to_C) = 0 /\ Len(B_to_A) = 0 /\ Len(B_to_C) = 0 /\ Len(C_to_A) = 0 /\ Len(C_to_B) = 0)

ChannelBound ==
  Len(A_to_B) <= 3 /\ Len(A_to_C) <= 3 /\ Len(B_to_A) <= 3 /\ Len(B_to_C) <= 3 /\ Len(C_to_A) <= 3 /\ Len(C_to_B) <= 3

====