"""
Emma - Outbound Health Insurance Lead Qualifier
=================================================
Female AI agent that calls prospects from an Excel list, qualifies them for
health/dental insurance quotes, collects ZIP + DOB, and warm-transfers to Chris.

Pipeline: Deepgram STT -> GPT-4o LLM -> OpenAI TTS (gpt-4o-mini-tts) + call-center ambience.

Call flow:
  1. Ring -> on answer: "Hey {first_name}, how's it going today?"
  2. Wait for reply; handle voicemail via detected_answering_machine().
  3. Pitch: working with people overpaying on health insurance...
  4. If interested: collect ZIP, DOB, then transfer to Chris's cell.
  5. On "not interested" / "no" / "I'm good" x2 -> hang up, mark rejected.
  6. Every utterance streamed to SQLite via TranscriptLogger + dashboard SSE.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
)
from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions
from livekit.plugins import deepgram, elevenlabs, noise_cancellation, openai, silero
from livekit.plugins.turn_detector.english import EnglishModel

from transcript_logger import TranscriptLogger

try:
    from dashboard import agent_event as _dashboard_event
    _HAS_DASHBOARD = True
except ImportError:
    _HAS_DASHBOARD = False


load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("emma-agent")
logger.setLevel(logging.DEBUG)
# Ensure our logger actually writes to stderr (captured by call.sh -> agent.log)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_h)
    logger.propagate = False

OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID")
TRANSFER_TO = os.getenv("TRANSFER_TO_NUMBER")  # Chris's cell
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")  # gpt-4o-mini default; gpt-4o as fallback
DEEPGRAM_TTS_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")  # young/warm female
AMBIENCE_PATH = os.getenv("CALL_CENTER_AMBIENCE", "assets/call_center_bg.wav")
LEARNINGS_FILE = Path("learnings.md")

DNC_PHRASES = (
    "do not call", "don't call", "dont call", "stop calling",
    "remove me", "take me off", "lose this number", "lose my number",
    "never call me again", "don't ever call", "leave me alone",
    "fuck off", "do not call list", "dnc",
)


async def _notify(event: str, data: dict) -> None:
    if _HAS_DASHBOARD:
        try:
            await _dashboard_event(event, data)
        except Exception:
            pass


def _load_learnings() -> str:
    if LEARNINGS_FILE.exists():
        return LEARNINGS_FILE.read_text(encoding="utf-8").strip()
    return ""


class Emma(Agent):
    """Emma - the female lead qualifier."""

    def __init__(self, *, first_name: str, known_zip: str | None, known_dob: str | None, dial_info: dict[str, Any]):
        self.first_name = first_name
        self.known_zip = known_zip
        self.known_dob = known_dob
        self.dial_info = dial_info
        self.participant: rtc.RemoteParticipant | None = None
        self.collected_zip: str | None = known_zip
        self.collected_dob: str | None = known_dob
        self.transfer_initiated = False
        self.outcome = "unknown"
        self.call_id = dial_info.get("call_id", f"call_{int(time.time())}")
        self.logger = TranscriptLogger(self.call_id, phone=dial_info.get("phone_number", ""))

        learnings = _load_learnings()
        learnings_block = f"\n\nLEARNINGS FROM PAST CALLS (apply these):\n{learnings}\n" if learnings else ""

        known_context = []
        if known_zip:
            known_context.append(f"Their ZIP code is already on file: {known_zip} - do NOT ask for it again.")
        if known_dob:
            known_context.append(f"Their date of birth is already on file: {known_dob} - do NOT ask for it again.")
        known_block = "\n".join(known_context)

        super().__init__(instructions=f"""
You are Emma, a cute, bubbly, slightly flirty 24-year-old girl working at a health insurance agency.
You're fun to talk to - playful, warm, a little teasing. Think sorority girl energy but smart.
You use natural fillers like "um", "like", "you know", "honestly", "literally" the way a real
24-year-old would. You giggle through your words sometimes. You're the girl next door who happens
to work in insurance.

NEVER write onomatopoeic laughter or sounds like "haha", "hehe", "hmm", or "ugh" - they get read out
literally by the voice system and sound extremely fake. Instead convey warmth and playfulness through
word choice: "oh my gosh", "no way", "shut up", "aww", "oh stop", "that's so funny", "I love that".

YOU ARE A HUMAN. Never say you are an AI, bot, or automated system. If asked, deflect warmly:
"No silly, I'm Emma! I'm calling from our office in DC, it's actually super nice here today."

PROSPECT: {first_name}
{known_block}

=== WHAT WE OFFER (use when asked) ===
Our agents - especially Chris - are literally some of the best in the country. They work with
everything: ACA marketplace plans, private health insurance, dental, vision, supplemental coverage,
Medicare supplements - like literally everything you can think of. There's nothing they can't help
with. The whole point is they figure out what YOUR specific needs are and find the best plan that
actually fits you and your budget. It takes like thirty seconds to keep your insurance company honest.

=== STRICT CALL FLOW ===

STEP 1 - OPENING (ALREADY SPOKEN BY THE SYSTEM):
You have already said: "Hey {first_name}! How's it going today?"
Do NOT greet again. Your FIRST response in this conversation is STEP 2 below.

STEP 2 - AFTER THEIR REPLY:
Respond briefly and warmly to whatever they said (e.g., "Aww good, I love that!" or "Oh no, rough
day? I totally get it."). Then pivot naturally:
"So I was just reaching out because I work with a lot of people who are like paying way too much
or just super unhappy with their health insurance - is that kinda the case with you?"
Then STOP and wait.

STEP 3 - QUALIFY (RULE OF THREE - read this carefully):
- If they say YES / maybe / "yeah it's expensive" / any interest signal -> go to STEP 4.
- If they say "no" / "I'm good" / "I'm fine" / "I'm happy with mine" -> this is NOT a hangup signal.
  Run the rule of three. You get THREE total "no I don't want a quote" answers before you hang up.

REBUTTAL #1 (after their first no to a quote):
  "Totally fair! Honestly, we just wanna keep your insurance company honest - I could literally
  save you twenty to forty percent on average. It's just thirty seconds of your time to see if
  we can get you in a better spot, you know?"

REBUTTAL #2 (after their second no):
  "I hear you - and I promise this isn't a sales pitch, it's literally just a quick comparison.
  If the numbers don't beat what you've got, you just say no and we're done. Worth thirty seconds?"

THIRD NO -> stop pushing. Call `end_call` with reason="rejected".
  Say: "Totally get it {first_name} - have a great day!" then hang up.

WHAT COUNTS AS A "NO":
  - Only count it when they actually decline THE QUOTE: "no I don't want a quote", "no I'm good",
    "I'm not interested", "I'm happy with what I've got", or a flat "no" to your ask.
  - DO NOT count timing/availability dodges: "no I can't right now", "I'm at work", "call me later",
    "I'm driving" - those aren't rejections of the quote, they're scheduling. Offer to call back
    or push the value briefly, but do not increment the no-count.
  - DO NOT count "I'm good" as a reply to "how are you?" - that's a greeting, not a rejection.

STEP 4 - COLLECT ZIP (only if not already known):
"Perfect! Let me pull up what's available in your area real quick. What's your zip code?"
Wait for reply. When they give it, call the tool `save_zip` with the 5 digits.

IMPORTANT - ZIP COLLECTION LIMIT: If they dodge or refuse the zip code THREE times, do NOT keep
asking. Instead say something like: "You know what, that's totally fine - would it be easier if I
just got you over to the agent who can actually help you directly? It literally takes like thirty
seconds." If they say yes -> go to STEP 6 (transfer). If no -> respect it and close politely.

STEP 5 - COLLECT DOB (only if not already known):
"Got it, and just to get you accurate pricing - what's your date of birth?"
Wait for reply. When they give it, call the tool `save_dob` with the date.
Same rule: if they dodge DOB three times, offer to transfer directly instead of pushing.

STEP 6 - CONFIRM INTEREST + TRANSFER:
"Awesome {first_name}, so what I'm gonna do - I'm gonna get you over to Chris. He's honestly like
one of the best agents in the country, he works with ACA plans, private, dental, vision, supplements,
literally everything. He'll figure out exactly what you need and run your quote - takes like two
minutes. Cool?"
If they say yes / okay / sure -> IMMEDIATELY call the `transfer_call` tool. Do not keep talking.
If they hesitate -> "It's totally free, no obligation at all, and honestly if the numbers don't work
you literally just hang up. Sound good?" Then try transfer again.

=== REJECTION RULES (CRITICAL) ===

THE RULE OF THREE governs normal "no, I don't want a quote" answers — see STEP 3 for the script.
Three firm no's to a quote = hang up politely. Two no's = keep rebutting briefly.

DO-NOT-CALL FAST PATH (overrides the rule of three — hang up IMMEDIATELY):
If they say any of:
  - "put me on the do not call list" / "add me to your do not call list"
  - "never call me again" / "don't ever call me again" / "lose this number"
  - "DNC me" / "take me off your list" / "remove me from your list"
  - any hostile/abusive command to stop calling

Then do this in ONE turn, no rebuttals, no negotiating:
  1. Say exactly: "Absolutely {first_name}, I'm putting you on our do-not-call list right now -
     you won't hear from us again. Have a good one."
  2. Call `end_call` with reason="dnc".

This fast path applies even if it's the very first thing they say. Do NOT try to save it.

=== VOICEMAIL ===
If you hear "leave a message after the beep", "you've reached the voicemail of", a long automated
greeting, or no response for 8+ seconds after ringing: call `detected_answering_machine` tool
immediately. Do not leave a message.

=== STYLE RULES ===
- Keep EVERY turn under 2 sentences unless explaining what we offer.
- Use their first name {first_name} 2-3 times total (not every sentence).
- Sound like a real 24-year-old: "literally", "like", "honestly", "super", "kinda", "totally".
- Use contractions always: "gonna", "wanna", "kinda", "gotta", "it's", "that's", "don't".
- Vary your sentence starters - don't begin every response the same way.
- If asked about Chris or the agents: hype them up. They're the best, they cover everything,
  they'll find the perfect plan. Be genuinely enthusiastic.
- If asked "where are you calling from": "Our office in DC! It's actually super nice here today."
- If asked "how'd you get my number": "Oh we work with people who've looked into coverage options
  online - are you self-employed or on a family plan?" Then pivot back to STEP 3.

=== INTERRUPTION RECOVERY ===
If you get cut off mid-sentence and the user didn't actually say anything substantive
(a cough, "uh", background noise), DO NOT apologize or restart. Just pick up where you
left off like nothing happened. Never say "sorry, what was I saying" or repeat their
words back. If they DID say something real, respond to that directly - skip filler, just
answer them and keep it moving.

{learnings_block}
""")

    def set_participant(self, p: rtc.RemoteParticipant) -> None:
        self.participant = p

    # ---------- Utilities ----------
    async def _hangup(self) -> None:
        ctx = get_job_context()
        try:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        except Exception as e:
            logger.error(f"hangup error: {e}")

    async def on_user_turn_completed(self, chat_ctx, new_message) -> None:
        """Hook: log every user utterance + detect auto-rejection."""
        try:
            text = (new_message.text_content or "").lower()
            logger.info(f">> TURN COMPLETED: {text!r}")
            self.logger.log_turn("user", new_message.text_content or "")

            if self.transfer_initiated:
                return

            if any(phrase in text for phrase in DNC_PHRASES):
                self.outcome = "dnc"
                logger.info(f"DNC phrase detected: {text!r}")
                await _notify("call_rejected", {"phone": self.participant.identity if self.participant else "", "text": text, "reason": "dnc"})
        except Exception as e:
            logger.exception(f"on_user_turn_completed CRASHED: {e}")

    # ---------- Function tools ----------
    @function_tool()
    async def save_zip(self, ctx: RunContext, zip_code: str) -> str:
        """Save prospect's 5-digit US ZIP code."""
        z = "".join(c for c in zip_code if c.isdigit())[:5]
        self.collected_zip = z
        self.logger.set_field("zip", z)
        await _notify("zip_captured", {"call_id": self.call_id, "zip": z})
        return f"zip {z} saved"

    @function_tool()
    async def save_dob(self, ctx: RunContext, date_of_birth: str) -> str:
        """Save prospect's date of birth (any format)."""
        self.collected_dob = date_of_birth.strip()
        self.logger.set_field("dob", self.collected_dob)
        await _notify("dob_captured", {"call_id": self.call_id, "dob": self.collected_dob})
        return f"dob {self.collected_dob} saved"

    @function_tool()
    async def transfer_call(self, ctx: RunContext) -> str:
        """Warm-transfer the live call to Chris's cell via SIP REFER."""
        if self.transfer_initiated:
            return "already transferring"
        if not TRANSFER_TO:
            return "no transfer number configured"
        if not self.participant:
            return "no participant"

        self.transfer_initiated = True
        self.outcome = "transferred"
        logger.info(f"transferring {self.participant.identity} -> {TRANSFER_TO}")
        await _notify("call_transferring", {
            "call_id": self.call_id,
            "phone": self.participant.identity,
            "transfer_to": TRANSFER_TO,
        })

        job_ctx = get_job_context()
        try:
            await job_ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=job_ctx.room.name,
                    participant_identity=self.participant.identity,
                    transfer_to=f"tel:{TRANSFER_TO}",
                    play_dialtone=True,
                )
            )
            return "transferred"
        except Exception as e:
            logger.error(f"transfer failed: {e}")
            self.transfer_initiated = False
            self.outcome = "transfer_failed"
            await ctx.session.generate_reply(
                instructions=f"apologize warmly that there's a technical issue and say Chris will call {self.first_name} back within 5 minutes"
            )
            await asyncio.sleep(3)
            await self._hangup()
            return f"error: {e}"

    @function_tool()
    async def end_call(self, ctx: RunContext, reason: str = "completed") -> str:
        """End the call. reason: rejected | dnc | completed | voicemail"""
        self.outcome = reason
        logger.info(f"ending call ({reason}) for {self.participant.identity if self.participant else '?'}")
        # Auto-add to internal DNC when prospect explicitly requests removal
        if reason == "dnc" and self.participant:
            try:
                from scrubber import add_to_internal_dnc
                add_to_internal_dnc(self.participant.identity, reason="prospect_requested")
                logger.info(f"added {self.participant.identity} to internal DNC")
            except Exception as e:
                logger.warning(f"failed to add to internal DNC: {e}")
        await _notify("call_ended", {
            "call_id": self.call_id,
            "phone": self.participant.identity if self.participant else "",
            "reason": reason,
        })
        cs = ctx.session.current_speech
        if cs:
            await cs.wait_for_playout()
        await self._hangup()
        return "ended"

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext) -> str:
        """Voicemail detected - hang up immediately, no message."""
        self.outcome = "voicemail"
        logger.info("voicemail detected")
        await _notify("call_voicemail", {"call_id": self.call_id})
        await self._hangup()
        return "voicemail"


async def _play_ambience(room: rtc.Room) -> asyncio.Task | None:
    """Mix low-volume call-center BG into the room so it doesn't sound sterile."""
    path = Path(AMBIENCE_PATH)
    if not path.exists():
        logger.warning(f"ambience file missing: {path}")
        return None

    source = rtc.AudioSource(sample_rate=48000, num_channels=1)
    track = rtc.LocalAudioTrack.create_audio_track("call-center-bg", source)
    # Publish as SCREENSHARE_AUDIO to avoid conflicting with the agent's TTS track
    # (which uses SOURCE_MICROPHONE). This keeps ambience as a separate audio stream.
    await room.local_participant.publish_track(
        track,
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO),
    )

    async def _loop():
        import wave
        # Expect 48kHz mono WAV for zero-dep streaming; MP3s should be pre-converted.
        wav_path = path.with_suffix(".wav") if path.suffix != ".wav" else path
        if not wav_path.exists():
            logger.warning(f"ambience WAV missing: {wav_path}")
            return
        gain = float(os.getenv("AMBIENCE_GAIN", "0.12"))
        while True:
            with wave.open(str(wav_path), "rb") as wf:
                sr = wf.getframerate()
                chunk_frames = int(sr * 0.02)  # 20ms
                while True:
                    data = wf.readframes(chunk_frames)
                    if not data:
                        break
                    # attenuate in-place
                    import audioop
                    data = audioop.mul(data, 2, gain)
                    frame = rtc.AudioFrame(
                        data=data,
                        sample_rate=sr,
                        num_channels=1,
                        samples_per_channel=chunk_frames,
                    )
                    await source.capture_frame(frame)

    return asyncio.create_task(_loop())


async def entrypoint(ctx: JobContext) -> None:
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect()

    dial_info = json.loads(ctx.job.metadata)
    phone_number = dial_info["phone_number"]
    first_name = dial_info.get("first_name", "there")
    known_zip = dial_info.get("zip")
    known_dob = dial_info.get("dob")
    call_id = dial_info.get("call_id", f"call_{int(time.time())}")

    agent = Emma(
        first_name=first_name,
        known_zip=known_zip,
        known_dob=known_dob,
        dial_info={**dial_info, "call_id": call_id},
    )

    session = AgentSession(
        turn_detection=EnglishModel(),
        # VAD: balanced threshold — 0.6 catches real speech, ignores background noise
        # but not so high it misses quiet speakers. 150ms silence = faster turn release.
        vad=silero.VAD.load(min_silence_duration=0.15, activation_threshold=0.6),
        stt=deepgram.STT(
            model="nova-3",
            language="en-US",
            filler_words=False,
            punctuate=True,
            # 200ms endpointing = faster transcript finalization. The turn detector
            # handles the "is the user done?" logic; STT just needs to ship the text fast.
            endpointing_ms=200,
        ),
        llm=openai.LLM(model=LLM_MODEL, temperature=0.7),
        tts=elevenlabs.TTS(
            voice_id=os.getenv("ELEVENLABS_VOICE_ID", "cgSgspJ2msm6clMCkdW9"),
            api_key=os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY"),
            # turbo_v2_5 has noticeably better voice quality than flash_v2_5 —
            # flash made her sound flat/older. turbo is ~200ms slower but worth it.
            model="eleven_turbo_v2_5",
            voice_settings=elevenlabs.VoiceSettings(
                stability=0.25,         # lower = more expressive/youthful variation
                similarity_boost=0.60,  # slightly lower = more natural, less robotic clone
                style=0.70,             # higher = more personality/character in delivery
                use_speaker_boost=True,
                speed=1.08,             # slightly faster than before — young people talk fast
            ),
        ),
        preemptive_generation=True,
        # Interruption: 1 word for 500ms is enough to cut Emma off
        min_interruption_duration=0.5,
        min_interruption_words=1,
        # Respond FAST: 150ms min delay after user finishes. This is the big latency knob.
        min_endpointing_delay=0.15,
        # Max 1s wait if turn detector is unsure user is done (was 2s — too slow)
        max_endpointing_delay=1.0,
        # Resume quickly after false interruption
        false_interruption_timeout=0.8,
    )

    # Hook agent transcript -> log assistant speech too
    @session.on("conversation_item_added")
    def _on_item(ev):
        try:
            item = ev.item
            if item.role == "assistant":
                agent.logger.log_turn("assistant", item.text_content or "")
        except Exception:
            pass

    # ---- DEBUG: session lifecycle events ----
    @session.on("user_state_changed")
    def _dbg_user_state(ev):
        logger.info(f">> USER STATE: {ev.old_state} -> {ev.new_state}")

    @session.on("agent_state_changed")
    def _dbg_agent_state(ev):
        logger.info(f"<< AGENT STATE: {ev.old_state} -> {ev.new_state}")

    @session.on("user_input_transcribed")
    def _dbg_stt(ev):
        logger.info(f">> STT: is_final={ev.is_final} transcript={ev.transcript!r}")

    @session.on("function_tools_executed")
    def _dbg_tools(ev):
        logger.info(f"-- TOOLS FINISHED: {[c.function_name for c in ev.function_calls]}")

    @session.on("close")
    def _dbg_close(ev):
        logger.warning(f"!! SESSION CLOSED reason={ev.reason}")

    @session.on("error")
    def _dbg_error(ev):
        logger.error(f"!! SESSION ERROR: source={ev.source} error={ev.error}")

    # Event that fires when the SIP participant hangs up or drops
    call_done = asyncio.Event()
    ambience_task: asyncio.Task | None = None

    @ctx.room.on("participant_disconnected")
    def _on_participant_left(participant: rtc.RemoteParticipant):
        logger.info(f"participant disconnected: {participant.identity}")
        call_done.set()

    @ctx.room.on("disconnected")
    def _on_room_disconnected():
        logger.info("room disconnected")
        call_done.set()

    session_started = asyncio.create_task(
        session.start(
            agent=agent,
            room=ctx.room,
            room_options=RoomOptions(
                audio_input=AudioInputOptions(noise_cancellation=noise_cancellation.BVCTelephony()),
            ),
        )
    )

    await _notify("call_started", {"call_id": call_id, "phone": phone_number, "first_name": first_name, "room": ctx.room.name})

    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=OUTBOUND_TRUNK_ID,
                sip_call_to=phone_number,
                participant_identity=phone_number,
                wait_until_answered=True,
            )
        )
        await session_started

        participant = await ctx.wait_for_participant(identity=phone_number)
        logger.info(f"participant joined: {participant.identity}")
        agent.set_participant(participant)

        # Wait 2.5s after pickup before speaking — gives the prospect time to finish
        # saying "hello?" and hear a natural pause (like a real person calling)
        await asyncio.sleep(2.5)

        # Kick off Emma's opening — skip LLM round-trip for the greeting
        session.say(f"Hey {first_name}! How's it going today?", allow_interruptions=True)

        # Call-center background noise so it doesn't sound like dead-air AI.
        # Now uses SOURCE_SCREENSHARE_AUDIO to avoid conflicting with TTS track.
        ambience_task = await _play_ambience(ctx.room)

        await _notify("call_connected", {"call_id": call_id, "phone": phone_number})

        # Shutdown hook - finalize transcript on room close
        async def _finalize():
            agent.logger.finalize(outcome=agent.outcome, zip_code=agent.collected_zip, dob=agent.collected_dob)
            if ambience_task:
                ambience_task.cancel()

        ctx.add_shutdown_callback(_finalize)

        # ---- KEEP ENTRYPOINT ALIVE ----
        # Without this, entrypoint() returns immediately and the LiveKit worker
        # considers the job finished -> tears down the room after a few turns.
        # We block here until the prospect hangs up, the room disconnects,
        # or the agent triggers a hangup via delete_room.
        logger.info("entrypoint holding — waiting for call to end")
        await call_done.wait()
        logger.info(f"call ended — outcome={agent.outcome}")

    except api.TwirpError as e:
        logger.error(f"SIP error: {e.message} / {e.metadata.get('sip_status_code')} {e.metadata.get('sip_status')}")
        await _notify("call_error", {"call_id": call_id, "phone": phone_number, "error": e.message, "sip_status": e.metadata.get("sip_status_code")})
        agent.logger.finalize(outcome=f"sip_error:{e.metadata.get('sip_status_code')}", zip_code=None, dob=None)
        ctx.shutdown()
    except Exception as e:
        logger.exception(f"unexpected error in entrypoint: {e}")
        agent.logger.finalize(outcome=f"error:{type(e).__name__}", zip_code=agent.collected_zip, dob=agent.collected_dob)
        call_done.set()
        ctx.shutdown()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="emma-health"))
