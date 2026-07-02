---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS DEVELOPER_A, DEVELOPER_B, DEVELOPER_C

(* --algorithm Protocol {
variables
  ch_A_to_B = <<>>; \* DEVELOPER_A -> DEVELOPER_B, labels: ['passed', 'failed']
  ch_B_to_C = <<>>; \* DEVELOPER_B -> DEVELOPER_C, labels: ['passed', 'failed']
  ch_C_to_A = <<>>; \* DEVELOPER_C -> DEVELOPER_A, labels: ['passed', 'failed']
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
a_start:
  skip; \* domain: design_and_implement_feature()
a_acq_auth:
  acquire_lock(AUTH_MODULE);
a_acq_db:
  acquire_lock(DATABASE_MODULE);
a_commit:
  skip; \* domain: commit_changes(modules={AUTH_MODULE, DATABASE_MODULE})
a_rel_db:
  release_lock(DATABASE_MODULE);
a_rel_auth:
  release_lock(AUTH_MODULE);
a_test:
  either {
    \* tests passed
    send(ch_A_to_B, "passed");
  } or {
    \* tests failed
    send(ch_A_to_B, "failed");
  };
a_drain:
  receive(ch_C_to_A, msg);
a_done:
  skip;
}

fair process (DEVELOPER_B_proc \in {DEVELOPER_B})
variables msg = "";
{
b_start:
  skip; \* domain: design_and_implement_feature()
b_acq_db:
  acquire_lock(DATABASE_MODULE);
b_acq_api:
  acquire_lock(API_MODULE);
b_commit:
  skip; \* domain: commit_changes(modules={DATABASE_MODULE, API_MODULE})
b_rel_api:
  release_lock(API_MODULE);
b_rel_db:
  release_lock(DATABASE_MODULE);
b_test:
  either {
    \* tests passed
    send(ch_B_to_C, "passed");
  } or {
    \* tests failed
    send(ch_B_to_C, "failed");
  };
b_drain:
  receive(ch_A_to_B, msg);
b_done:
  skip;
}

fair process (DEVELOPER_C_proc \in {DEVELOPER_C})
variables msg = "";
{
c_start:
  skip; \* domain: design_and_implement_feature()
c_acq_auth:
  acquire_lock(AUTH_MODULE);
c_acq_api:
  acquire_lock(API_MODULE);
c_commit:
  skip; \* domain: commit_changes(modules={API_MODULE, AUTH_MODULE})
c_rel_api:
  release_lock(API_MODULE);
c_rel_auth:
  release_lock(AUTH_MODULE);
c_test:
  either {
    \* tests passed
    send(ch_C_to_A, "passed");
  } or {
    \* tests failed
    send(ch_C_to_A, "failed");
  };
c_drain:
  receive(ch_B_to_C, msg);
c_done:
  skip;
}

} *)

AllDone == \A p \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C}: pc[p] \in STRING
  /\ AUTH_MODULE \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C, "FREE"}
  /\ DATABASE_MODULE \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C, "FREE"}
  /\ API_MODULE \in {DEVELOPER_A, DEVELOPER_B, DEVELOPER_C, "FREE"}
  /\ ch_A_to_B \in Seq(STRING)
  /\ ch_B_to_C \in Seq(STRING)
  /\ ch_C_to_A \in Seq(STRING)

NoOrphanLocks ==
  AllDone => (AUTH_MODULE = "FREE" /\ DATABASE_MODULE = "FREE" /\ API_MODULE = "FREE")

ChannelsDrained ==
  AllDone => (Len(ch_A_to_B) = 0 /\ Len(ch_B_to_C) = 0 /\ Len(ch_C_to_A) = 0)

ChannelBound ==
  Len(ch_A_to_B) <= 3 /\ Len(ch_B_to_C) <= 3 /\ Len(ch_C_to_A) <= 3

====