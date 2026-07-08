---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS Video_context, Audio_context

(* --algorithm Protocol {
variables
  video_context_to_audio_context = <<>>; \* video_context -> audio_context, labels: ['handoff']
  video_context_resource = "FREE"; \* Lock
  audio_context_resource = "FREE"; \* Lock

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

fair process (video_context_proc \in {Video_context})
variables msg = "";
{
  video_context_start:
    acquire_lock(video_context_resource);
  video_context_work:
    skip; \* domain: video_context
  video_context_send:
    send(video_context_to_audio_context, "handoff");
  video_context_release:
    release_lock(video_context_resource);
  video_context_done:
    skip;
}

fair process (audio_context_proc \in {Audio_context})
variables msg = "";
{
  audio_context_start:
    receive(video_context_to_audio_context, msg);
  audio_context_acquire:
    acquire_lock(audio_context_resource);
  audio_context_work:
    skip; \* domain: audio_context
  audio_context_release:
    release_lock(audio_context_resource);
  audio_context_done:
    skip;
}

} *)

AllDone == \A p \in {Video_context, Audio_context}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {Video_context, Audio_context}: pc[p] \in STRING
  /\ video_context_resource \in {Video_context, Audio_context, "FREE"}
  /\ audio_context_resource \in {Video_context, Audio_context, "FREE"}
  /\ video_context_to_audio_context \in Seq(STRING)

NoOrphanLocks ==
  AllDone => (video_context_resource = "FREE" /\ audio_context_resource = "FREE")

ChannelsDrained ==
  AllDone => (Len(video_context_to_audio_context) = 0)

ChannelBound ==
  Len(video_context_to_audio_context) <= 3

====
