---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS DEVELOPER_A, DEVELOPER_B, DEVELOPER_C

(* --algorithm Protocol {
variables
  A_to_B = <<>>; \* DEVELOPER_A -> DEVELOPER_B, labels: ['pass', 'fail']
  A_to_C = <<>>; \* DEVELOPER_A -> DEVELOPER_C, labels: ['pass', 'fail']
  B_to_A = <<>>; \* DEVELOPER_B -> DEVELOPER_A, labels: ['pass', 'fail']
  B_to_C = <<>>; \* DEVELOPER_B -> DEVELOPER_C, labels: ['pass', 'fail']
  C_to_A = <<>>; \* DEVELOPER_C -> DEVELOPER_A, labels: ['pass', 'fail']
  C_to_B = <<>>; \* DEVELOPER_C -> DEVELOPER_B, labels: ['pass', 'fail']
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

\* -- DEVELOPER_A: authentication feature (AUTH_MODULE, DATABASE_MODULE)
fair process (DEVELOPER_A_proc \in {DEVELOPER_A})
variables msg = "", result = "";
{
A_design:
  skip; \* domain: design_implement_authentication()
A_acquire_auth:
  acquire_lock(AUTH_MODULE);
A_commit_auth:
  skip; \* domain: commit_to_module("AUTH_MODULE")
A_release_auth:
  release_lock(AUTH_MODULE);
A_acquire_db:
  acquire_lock(DATABASE_MODULE);
A_commit_db:
  skip; \* domain: commit_to_module("DATABASE_MODULE")
A_release_db:
  release_lock(DATABASE_MODULE);
A_test:
  skip; \* domain: run_local_tests()
A_decide:
  either {
    result := "pass";
  } or {
    result := "fail";
  };
A_notify:
  if (result = "pass") {
    send(A_to_B, "pass");
    send(A_to_C, "pass");
  } else {
    send(A_to_B, "fail");
    send(A_to_C, "fail");
  };
A_rcv_B:
  receive(B_to_A, msg);
A_rcv_C:
  receive(C_to_A, msg);
A_done:
  skip;
}

\* -- DEVELOPER_B: REST API endpoints (DATABASE_MODULE, API_MODULE)
fair process (DEVELOPER_B_proc \in {DEVELOPER_B})
variables msg = "", result = "";
{
B_design:
  skip; \* domain: design_implement_api_endpoints()
B_acquire_db:
  acquire_lock(DATABASE_MODULE);
B_commit_db:
  skip; \* domain: commit_to_module("DATABASE_MODULE")
B_release_db:
  release_lock(DATABASE_MODULE);
B_acquire_api:
  acquire_lock(API_MODULE);
B_commit_api:
  skip; \* domain: commit_to_module("API_MODULE")
B_release_api:
  release_lock(API_MODULE);
B_test:
  skip; \* domain: run_local_tests()
B_decide:
  either {
    result := "pass";
  } or {
    result := "fail";
  };
B_notify:
  if (result = "pass") {
    send(B_to_A, "pass");
    send(B_to_C, "pass");
  } else {
    send(B_to_A, "fail");
    send(B_to_C, "fail");
  };
B_rcv_A:
  receive(A_to_B, msg);
B_rcv_C:
  receive(C_to_B, msg);
B_done:
  skip;
}

\* -- DEVELOPER_C: API auth middleware (API_MODULE, AUTH_MODULE)
fair process (DEVELOPER_C_proc \in {DEVELOPER_C})
variables msg = "", result = "";
{
C_design:
  skip; \* domain: design_implement_auth_middleware()
C_acquire_auth:
  acquire_lock(AUTH_MODULE);
C_commit_auth:
  skip; \* domain: commit_to_module("AUTH_MODULE")
C_release_auth:
  release_lock(AUTH_MODULE);
C_acquire_api:
  acquire_lock(API_MODULE);
C_commit_api:
  skip; \* domain: commit_to_module("API_MODULE")
C_release_api:
  release_lock(API_MODULE);
C_test:
  skip; \* domain: run_local_tests()
C_decide:
  either {
    result := "pass";
  } or {
    result := "fail";
  };
C_notify:
  if (result = "pass") {
    send(C_to_A, "pass");
    send(C_to_B, "pass");
  } else {
    send(C_to_A, "fail");
    send(C_to_B, "fail");
  };
C_rcv_A:
  receive(A_to_C, msg);
C_rcv_B:
  receive(B_to_C, msg);
C_done:
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