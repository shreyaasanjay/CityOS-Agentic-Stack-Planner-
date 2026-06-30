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
\* BEGIN TRANSLATION (chksum(pcal) = "16ab64d9" /\ chksum(tla) = "e1e98eab")
\* Process variable msg of process DEVELOPER_A_proc at line 75 col 11 changed to msg_
\* Process variable msg of process DEVELOPER_B_proc at line 93 col 11 changed to msg_D
VARIABLES pc, A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, AUTH_MODULE, 
          DATABASE_MODULE, API_MODULE, msg_, msg_D, msg

vars == << pc, A_to_B, A_to_C, B_to_A, B_to_C, C_to_A, C_to_B, AUTH_MODULE, 
           DATABASE_MODULE, API_MODULE, msg_, msg_D, msg >>

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
        (* Process DEVELOPER_B_proc *)
        /\ msg_D = [self \in {DEVELOPER_B} |-> ""]
        (* Process DEVELOPER_C_proc *)
        /\ msg = [self \in {DEVELOPER_C} |-> ""]
        /\ pc = [self \in ProcSet |-> CASE self \in {DEVELOPER_A} -> "DEVELOPER_A_start"
                                        [] self \in {DEVELOPER_B} -> "DEVELOPER_B_start"
                                        [] self \in {DEVELOPER_C} -> "DEVELOPER_C_start"]

DEVELOPER_A_start(self) == /\ pc[self] = "DEVELOPER_A_start"
                           /\ TRUE
                           /\ pc' = [pc EXCEPT ![self] = "DEVELOPER_A_done"]
                           /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, 
                                           C_to_A, C_to_B, AUTH_MODULE, 
                                           DATABASE_MODULE, API_MODULE, msg_, 
                                           msg_D, msg >>

DEVELOPER_A_done(self) == /\ pc[self] = "DEVELOPER_A_done"
                          /\ TRUE
                          /\ pc' = [pc EXCEPT ![self] = "Done"]
                          /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, 
                                          C_to_A, C_to_B, AUTH_MODULE, 
                                          DATABASE_MODULE, API_MODULE, msg_, 
                                          msg_D, msg >>

DEVELOPER_A_proc(self) == DEVELOPER_A_start(self) \/ DEVELOPER_A_done(self)

DEVELOPER_B_start(self) == /\ pc[self] = "DEVELOPER_B_start"
                           /\ TRUE
                           /\ pc' = [pc EXCEPT ![self] = "DEVELOPER_B_done"]
                           /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, 
                                           C_to_A, C_to_B, AUTH_MODULE, 
                                           DATABASE_MODULE, API_MODULE, msg_, 
                                           msg_D, msg >>

DEVELOPER_B_done(self) == /\ pc[self] = "DEVELOPER_B_done"
                          /\ TRUE
                          /\ pc' = [pc EXCEPT ![self] = "Done"]
                          /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, 
                                          C_to_A, C_to_B, AUTH_MODULE, 
                                          DATABASE_MODULE, API_MODULE, msg_, 
                                          msg_D, msg >>

DEVELOPER_B_proc(self) == DEVELOPER_B_start(self) \/ DEVELOPER_B_done(self)

DEVELOPER_C_start(self) == /\ pc[self] = "DEVELOPER_C_start"
                           /\ TRUE
                           /\ pc' = [pc EXCEPT ![self] = "DEVELOPER_C_done"]
                           /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, 
                                           C_to_A, C_to_B, AUTH_MODULE, 
                                           DATABASE_MODULE, API_MODULE, msg_, 
                                           msg_D, msg >>

DEVELOPER_C_done(self) == /\ pc[self] = "DEVELOPER_C_done"
                          /\ TRUE
                          /\ pc' = [pc EXCEPT ![self] = "Done"]
                          /\ UNCHANGED << A_to_B, A_to_C, B_to_A, B_to_C, 
                                          C_to_A, C_to_B, AUTH_MODULE, 
                                          DATABASE_MODULE, API_MODULE, msg_, 
                                          msg_D, msg >>

DEVELOPER_C_proc(self) == DEVELOPER_C_start(self) \/ DEVELOPER_C_done(self)

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
