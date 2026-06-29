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
\* BEGIN TRANSLATION (chksum(pcal) = "51ac9b14" /\ chksum(tla) = "501196d6")
\* Process variable msg of process DEVELOPER_A_proc at line 39 col 11 changed to msg_
\* Process variable result of process DEVELOPER_A_proc at line 39 col 21 changed to result_
\* Process variable msg of process DEVELOPER_B_proc at line 81 col 11 changed to msg_D
\* Process variable result of process DEVELOPER_B_proc at line 81 col 21 changed to result_D
VARIABLES pc, A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, AUTH_MODULE, 
          DATABASE_MODULE, API_MODULE, msg_, result_, msg_D, result_D, msg, 
          result

vars == << pc, A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, AUTH_MODULE, 
           DATABASE_MODULE, API_MODULE, msg_, result_, msg_D, result_D, msg, 
           result >>

ProcSet == ({DEVELOPER_A}) \cup ({DEVELOPER_B}) \cup ({DEVELOPER_C})

Init == (* Global variables *)
        /\ A_to_B = <<>>
        /\ A_to_C = <<>>
        /\ B_to_A = <<>>
        /\ B_to_C = <<>>
        /\ C_to_A = <<>>
        /\ C_to_B = <<>>
        /\ AUTH_MODULE = "FREE"
        /\ DATABASE_MODULE = "FREE"
        /\ API_MODULE = "FREE"
        (* Process DEVELOPER_A_proc *)
        /\ msg_ = [self \in {DEVELOPER_A} |-> ""]
        /\ result_ = [self \in {DEVELOPER_A} |-> ""]
        (* Process DEVELOPER_B_proc *)
        /\ msg_D = [self \in {DEVELOPER_B} |-> ""]
        /\ result_D = [self \in {DEVELOPER_B} |-> ""]
        (* Process DEVELOPER_C_proc *)
        /\ msg = [self \in {DEVELOPER_C} |-> ""]
        /\ result = [self \in {DEVELOPER_C} |-> ""]
        /\ pc = [self \in ProcSet |-> CASE self \in {DEVELOPER_A} -> "A_design"
                                        [] self \in {DEVELOPER_B} -> "B_design"
                                        [] self \in {DEVELOPER_C} -> "C_design"]

A_design(self) == /\ pc[self] = "A_design"
                  /\ TRUE
                  /\ pc' = [pc EXCEPT ![self] = "A_acquire_auth"]
                  /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                  C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                  API_MODULE, msg_, result_, msg_D, result_D, 
                                  msg, result >>

A_acquire_auth(self) == /\ pc[self] = "A_acquire_auth"
                        /\ AUTH_MODULE = "FREE"
                        /\ AUTH_MODULE' = self
                        /\ pc' = [pc EXCEPT ![self] = "A_commit_auth"]
                        /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                        C_to_B, DATABASE_MODULE, API_MODULE, 
                                        msg_, result_, msg_D, result_D, msg, 
                                        result >>

A_commit_auth(self) == /\ pc[self] = "A_commit_auth"
                       /\ TRUE
                       /\ pc' = [pc EXCEPT ![self] = "A_release_auth"]
                       /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                       C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                       API_MODULE, msg_, result_, msg_D, 
                                       result_D, msg, result >>

A_release_auth(self) == /\ pc[self] = "A_release_auth"
                        /\ AUTH_MODULE' = "FREE"
                        /\ pc' = [pc EXCEPT ![self] = "A_acquire_db"]
                        /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                        C_to_B, DATABASE_MODULE, API_MODULE, 
                                        msg_, result_, msg_D, result_D, msg, 
                                        result >>

A_acquire_db(self) == /\ pc[self] = "A_acquire_db"
                      /\ DATABASE_MODULE = "FREE"
                      /\ DATABASE_MODULE' = self
                      /\ pc' = [pc EXCEPT ![self] = "A_commit_db"]
                      /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                      C_to_B, AUTH_MODULE, API_MODULE, msg_, 
                                      result_, msg_D, result_D, msg, result >>

A_commit_db(self) == /\ pc[self] = "A_commit_db"
                     /\ TRUE
                     /\ pc' = [pc EXCEPT ![self] = "A_release_db"]
                     /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                     C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                     API_MODULE, msg_, result_, msg_D, 
                                     result_D, msg, result >>

A_release_db(self) == /\ pc[self] = "A_release_db"
                      /\ DATABASE_MODULE' = "FREE"
                      /\ pc' = [pc EXCEPT ![self] = "A_test"]
                      /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                      C_to_B, AUTH_MODULE, API_MODULE, msg_, 
                                      result_, msg_D, result_D, msg, result >>

A_test(self) == /\ pc[self] = "A_test"
                /\ TRUE
                /\ pc' = [pc EXCEPT ![self] = "A_decide"]
                /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, 
                                AUTH_MODULE, DATABASE_MODULE, API_MODULE, msg_, 
                                result_, msg_D, result_D, msg, result >>

A_decide(self) == /\ pc[self] = "A_decide"
                  /\ \/ /\ result_' = [result_ EXCEPT ![self] = "pass"]
                     \/ /\ result_' = [result_ EXCEPT ![self] = "fail"]
                  /\ pc' = [pc EXCEPT ![self] = "A_notify"]
                  /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                  C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                  API_MODULE, msg_, msg_D, result_D, msg, 
                                  result >>

A_notify(self) == /\ pc[self] = "A_notify"
                  /\ IF result_[self] = "pass"
                        THEN /\ A_to_B' = Append(A_to_B, "pass")
                             /\ A_to_C' = Append(A_to_C, "pass")
                        ELSE /\ A_to_B' = Append(A_to_B, "fail")
                             /\ A_to_C' = Append(A_to_C, "fail")
                  /\ pc' = [pc EXCEPT ![self] = "A_rcv_B"]
                  /\ UNCHANGED << B_to_A, B_to_C, C_to_A, C_to_B, AUTH_MODULE, 
                                  DATABASE_MODULE, API_MODULE, msg_, result_, 
                                  msg_D, result_D, msg, result >>

A_rcv_B(self) == /\ pc[self] = "A_rcv_B"
                 /\ Len(B_to_A) > 0
                 /\ msg_' = [msg_ EXCEPT ![self] = Head(B_to_A)]
                 /\ B_to_A' = Tail(B_to_A)
                 /\ pc' = [pc EXCEPT ![self] = "A_rcv_C"]
                 /\ UNCHANGED << A_to_B, A_to_C, B_to_C, C_to_A, C_to_B, 
                                 AUTH_MODULE, DATABASE_MODULE, API_MODULE, 
                                 result_, msg_D, result_D, msg, result >>

A_rcv_C(self) == /\ pc[self] = "A_rcv_C"
                 /\ Len(C_to_A) > 0
                 /\ msg_' = [msg_ EXCEPT ![self] = Head(C_to_A)]
                 /\ C_to_A' = Tail(C_to_A)
                 /\ pc' = [pc EXCEPT ![self] = "A_done"]
                 /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_B, 
                                 AUTH_MODULE, DATABASE_MODULE, API_MODULE, 
                                 result_, msg_D, result_D, msg, result >>

A_done(self) == /\ pc[self] = "A_done"
                /\ TRUE
                /\ pc' = [pc EXCEPT ![self] = "Done"]
                /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, 
                                AUTH_MODULE, DATABASE_MODULE, API_MODULE, msg_, 
                                result_, msg_D, result_D, msg, result >>

DEVELOPER_A_proc(self) == A_design(self) \/ A_acquire_auth(self)
                             \/ A_commit_auth(self) \/ A_release_auth(self)
                             \/ A_acquire_db(self) \/ A_commit_db(self)
                             \/ A_release_db(self) \/ A_test(self)
                             \/ A_decide(self) \/ A_notify(self)
                             \/ A_rcv_B(self) \/ A_rcv_C(self)
                             \/ A_done(self)

B_design(self) == /\ pc[self] = "B_design"
                  /\ TRUE
                  /\ pc' = [pc EXCEPT ![self] = "B_acquire_db"]
                  /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                  C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                  API_MODULE, msg_, result_, msg_D, result_D, 
                                  msg, result >>

B_acquire_db(self) == /\ pc[self] = "B_acquire_db"
                      /\ DATABASE_MODULE = "FREE"
                      /\ DATABASE_MODULE' = self
                      /\ pc' = [pc EXCEPT ![self] = "B_commit_db"]
                      /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                      C_to_B, AUTH_MODULE, API_MODULE, msg_, 
                                      result_, msg_D, result_D, msg, result >>

B_commit_db(self) == /\ pc[self] = "B_commit_db"
                     /\ TRUE
                     /\ pc' = [pc EXCEPT ![self] = "B_release_db"]
                     /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                     C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                     API_MODULE, msg_, result_, msg_D, 
                                     result_D, msg, result >>

B_release_db(self) == /\ pc[self] = "B_release_db"
                      /\ DATABASE_MODULE' = "FREE"
                      /\ pc' = [pc EXCEPT ![self] = "B_acquire_api"]
                      /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                      C_to_B, AUTH_MODULE, API_MODULE, msg_, 
                                      result_, msg_D, result_D, msg, result >>

B_acquire_api(self) == /\ pc[self] = "B_acquire_api"
                       /\ API_MODULE = "FREE"
                       /\ API_MODULE' = self
                       /\ pc' = [pc EXCEPT ![self] = "B_commit_api"]
                       /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                       C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                       msg_, result_, msg_D, result_D, msg, 
                                       result >>

B_commit_api(self) == /\ pc[self] = "B_commit_api"
                      /\ TRUE
                      /\ pc' = [pc EXCEPT ![self] = "B_release_api"]
                      /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                      C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                      API_MODULE, msg_, result_, msg_D, 
                                      result_D, msg, result >>

B_release_api(self) == /\ pc[self] = "B_release_api"
                       /\ API_MODULE' = "FREE"
                       /\ pc' = [pc EXCEPT ![self] = "B_test"]
                       /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                       C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                       msg_, result_, msg_D, result_D, msg, 
                                       result >>

B_test(self) == /\ pc[self] = "B_test"
                /\ TRUE
                /\ pc' = [pc EXCEPT ![self] = "B_decide"]
                /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, 
                                AUTH_MODULE, DATABASE_MODULE, API_MODULE, msg_, 
                                result_, msg_D, result_D, msg, result >>

B_decide(self) == /\ pc[self] = "B_decide"
                  /\ \/ /\ result_D' = [result_D EXCEPT ![self] = "pass"]
                     \/ /\ result_D' = [result_D EXCEPT ![self] = "fail"]
                  /\ pc' = [pc EXCEPT ![self] = "B_notify"]
                  /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                  C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                  API_MODULE, msg_, result_, msg_D, msg, 
                                  result >>

B_notify(self) == /\ pc[self] = "B_notify"
                  /\ IF result_D[self] = "pass"
                        THEN /\ B_to_A' = Append(B_to_A, "pass")
                             /\ B_to_C' = Append(B_to_C, "pass")
                        ELSE /\ B_to_A' = Append(B_to_A, "fail")
                             /\ B_to_C' = Append(B_to_C, "fail")
                  /\ pc' = [pc EXCEPT ![self] = "B_rcv_A"]
                  /\ UNCHANGED << A_to_B, A_to_C, C_to_A, C_to_B, AUTH_MODULE, 
                                  DATABASE_MODULE, API_MODULE, msg_, result_, 
                                  msg_D, result_D, msg, result >>

B_rcv_A(self) == /\ pc[self] = "B_rcv_A"
                 /\ Len(A_to_B) > 0
                 /\ msg_D' = [msg_D EXCEPT ![self] = Head(A_to_B)]
                 /\ A_to_B' = Tail(A_to_B)
                 /\ pc' = [pc EXCEPT ![self] = "B_rcv_C"]
                 /\ UNCHANGED << A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, 
                                 AUTH_MODULE, DATABASE_MODULE, API_MODULE, 
                                 msg_, result_, result_D, msg, result >>

B_rcv_C(self) == /\ pc[self] = "B_rcv_C"
                 /\ Len(C_to_B) > 0
                 /\ msg_D' = [msg_D EXCEPT ![self] = Head(C_to_B)]
                 /\ C_to_B' = Tail(C_to_B)
                 /\ pc' = [pc EXCEPT ![self] = "B_done"]
                 /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                 AUTH_MODULE, DATABASE_MODULE, API_MODULE, 
                                 msg_, result_, result_D, msg, result >>

B_done(self) == /\ pc[self] = "B_done"
                /\ TRUE
                /\ pc' = [pc EXCEPT ![self] = "Done"]
                /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, 
                                AUTH_MODULE, DATABASE_MODULE, API_MODULE, msg_, 
                                result_, msg_D, result_D, msg, result >>

DEVELOPER_B_proc(self) == B_design(self) \/ B_acquire_db(self)
                             \/ B_commit_db(self) \/ B_release_db(self)
                             \/ B_acquire_api(self) \/ B_commit_api(self)
                             \/ B_release_api(self) \/ B_test(self)
                             \/ B_decide(self) \/ B_notify(self)
                             \/ B_rcv_A(self) \/ B_rcv_C(self)
                             \/ B_done(self)

C_design(self) == /\ pc[self] = "C_design"
                  /\ TRUE
                  /\ pc' = [pc EXCEPT ![self] = "C_acquire_auth"]
                  /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                  C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                  API_MODULE, msg_, result_, msg_D, result_D, 
                                  msg, result >>

C_acquire_auth(self) == /\ pc[self] = "C_acquire_auth"
                        /\ AUTH_MODULE = "FREE"
                        /\ AUTH_MODULE' = self
                        /\ pc' = [pc EXCEPT ![self] = "C_commit_auth"]
                        /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                        C_to_B, DATABASE_MODULE, API_MODULE, 
                                        msg_, result_, msg_D, result_D, msg, 
                                        result >>

C_commit_auth(self) == /\ pc[self] = "C_commit_auth"
                       /\ TRUE
                       /\ pc' = [pc EXCEPT ![self] = "C_release_auth"]
                       /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                       C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                       API_MODULE, msg_, result_, msg_D, 
                                       result_D, msg, result >>

C_release_auth(self) == /\ pc[self] = "C_release_auth"
                        /\ AUTH_MODULE' = "FREE"
                        /\ pc' = [pc EXCEPT ![self] = "C_acquire_api"]
                        /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                        C_to_B, DATABASE_MODULE, API_MODULE, 
                                        msg_, result_, msg_D, result_D, msg, 
                                        result >>

C_acquire_api(self) == /\ pc[self] = "C_acquire_api"
                       /\ API_MODULE = "FREE"
                       /\ API_MODULE' = self
                       /\ pc' = [pc EXCEPT ![self] = "C_commit_api"]
                       /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                       C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                       msg_, result_, msg_D, result_D, msg, 
                                       result >>

C_commit_api(self) == /\ pc[self] = "C_commit_api"
                      /\ TRUE
                      /\ pc' = [pc EXCEPT ![self] = "C_release_api"]
                      /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                      C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                      API_MODULE, msg_, result_, msg_D, 
                                      result_D, msg, result >>

C_release_api(self) == /\ pc[self] = "C_release_api"
                       /\ API_MODULE' = "FREE"
                       /\ pc' = [pc EXCEPT ![self] = "C_test"]
                       /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                       C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                       msg_, result_, msg_D, result_D, msg, 
                                       result >>

C_test(self) == /\ pc[self] = "C_test"
                /\ TRUE
                /\ pc' = [pc EXCEPT ![self] = "C_decide"]
                /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, 
                                AUTH_MODULE, DATABASE_MODULE, API_MODULE, msg_, 
                                result_, msg_D, result_D, msg, result >>

C_decide(self) == /\ pc[self] = "C_decide"
                  /\ \/ /\ result' = [result EXCEPT ![self] = "pass"]
                     \/ /\ result' = [result EXCEPT ![self] = "fail"]
                  /\ pc' = [pc EXCEPT ![self] = "C_notify"]
                  /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, 
                                  C_to_B, AUTH_MODULE, DATABASE_MODULE, 
                                  API_MODULE, msg_, result_, msg_D, result_D, 
                                  msg >>

C_notify(self) == /\ pc[self] = "C_notify"
                  /\ IF result[self] = "pass"
                        THEN /\ C_to_A' = Append(C_to_A, "pass")
                             /\ C_to_B' = Append(C_to_B, "pass")
                        ELSE /\ C_to_A' = Append(C_to_A, "fail")
                             /\ C_to_B' = Append(C_to_B, "fail")
                  /\ pc' = [pc EXCEPT ![self] = "C_rcv_A"]
                  /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, AUTH_MODULE, 
                                  DATABASE_MODULE, API_MODULE, msg_, result_, 
                                  msg_D, result_D, msg, result >>

C_rcv_A(self) == /\ pc[self] = "C_rcv_A"
                 /\ Len(A_to_C) > 0
                 /\ msg' = [msg EXCEPT ![self] = Head(A_to_C)]
                 /\ A_to_C' = Tail(A_to_C)
                 /\ pc' = [pc EXCEPT ![self] = "C_rcv_B"]
                 /\ UNCHANGED << A_to_B, B_to_A, B_to_C, C_to_A, C_to_B, 
                                 AUTH_MODULE, DATABASE_MODULE, API_MODULE, 
                                 msg_, result_, msg_D, result_D, result >>

C_rcv_B(self) == /\ pc[self] = "C_rcv_B"
                 /\ Len(B_to_C) > 0
                 /\ msg' = [msg EXCEPT ![self] = Head(B_to_C)]
                 /\ B_to_C' = Tail(B_to_C)
                 /\ pc' = [pc EXCEPT ![self] = "C_done"]
                 /\ UNCHANGED << A_to_B, A_to_C, B_to_A, C_to_A, C_to_B, 
                                 AUTH_MODULE, DATABASE_MODULE, API_MODULE, 
                                 msg_, result_, msg_D, result_D, result >>

C_done(self) == /\ pc[self] = "C_done"
                /\ TRUE
                /\ pc' = [pc EXCEPT ![self] = "Done"]
                /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, 
                                AUTH_MODULE, DATABASE_MODULE, API_MODULE, msg_, 
                                result_, msg_D, result_D, msg, result >>

DEVELOPER_C_proc(self) == C_design(self) \/ C_acquire_auth(self)
                             \/ C_commit_auth(self) \/ C_release_auth(self)
                             \/ C_acquire_api(self) \/ C_commit_api(self)
                             \/ C_release_api(self) \/ C_test(self)
                             \/ C_decide(self) \/ C_notify(self)
                             \/ C_rcv_A(self) \/ C_rcv_B(self)
                             \/ C_done(self)

(* Allow infinite stuttering to prevent deadlock on termination. *)
Terminating == /\ \A self \in ProcSet: pc[self] = "Done"
               /\ UNCHANGED vars

Next == (\E self \in {DEVELOPER_A}: DEVELOPER_A_proc(self))
           \/ (\E self \in {DEVELOPER_B}: DEVELOPER_B_proc(self))
           \/ (\E self \in {DEVELOPER_C}: DEVELOPER_C_proc(self))
           \/ Terminating

Spec == /\ Init /\ [][Next]_vars
        /\ \A self \in {DEVELOPER_A} : WF_vars(DEVELOPER_A_proc(self))
        /\ \A self \in {DEVELOPER_B} : WF_vars(DEVELOPER_B_proc(self))
        /\ \A self \in {DEVELOPER_C} : WF_vars(DEVELOPER_C_proc(self))

Termination == <>(\A self \in ProcSet: pc[self] = "Done")

\* END TRANSLATION 

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
