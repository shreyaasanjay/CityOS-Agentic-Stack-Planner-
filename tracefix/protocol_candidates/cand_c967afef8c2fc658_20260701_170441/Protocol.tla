---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS TASK_AGENT

(* --algorithm Protocol {
variables
  \* No shared variables

fair process (TASK_AGENT_proc \in {TASK_AGENT})
variables msg = "";
{
  TASK_AGENT_start:
    skip; \* TODO: replace with TASK_AGENT's protocol logic
  TASK_AGENT_done:
    skip;
}

} *)

AllDone == \A p \in {TASK_AGENT}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {TASK_AGENT}: pc[p] \in STRING

====