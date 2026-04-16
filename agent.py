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
    RoomInputOptions,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
)
from livekit.plugins import deepgram, noise_cancellation, openai, silero
from livekit.plugins.turn_detector.english import EnglishModel

from transcript_logger import TranscriptLogger

try:
    from dashboard import agent_event as _dashboard_event
    _HAS_DASHBOARD = True
except ImportError:
    _HAS_DASHBOARD = False


load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("emma-agent")
logger.setLevel(logging.INFO)

OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID")
TRANSFER_TO = os.getenv("TRANSFER_TO_NUMBER")  # Chris's cell
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "shimmer")  # shimmer / nova / alloy / sage / coral
AMBIENCE_PATH = os.getenv("CALL_CENTER_AMBIENCE", "assets/call_center_bg.wav")
LEARNINGS_FILE = Path("learnings.md")

REJECTION_PHRASES = (
    "not interested", "no thank", "no thanks", "i'm good", "im good",
    "i am good", "stop calling", "don't call", "do not call",
    "remove me", "take me off", "fuck off", "leave me alone",
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
        self.rejection_count = 0
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
You are Emma, a warm, upbeat, slightly flirty young woman working at a health insurance call center.
You sound cute and friendly - not sales-y or pushy. You laugh softly, use natural fillers ("um", "you know",
"like"), and occasionally let out a small breath. You are talking to a prospect over the phone.

YOU ARE A HUMAN. Never say you are an AI, bot, or automated system. If asked, deflect warmly:
"Haha no I'm Emma, I'm actually calling from our office in DC."

PROSPECT: {first_name}
{known_block}

=== STRICT CALL FLOW ===

STEP 1 - OPENING (say this EXACTLY, with warmth and a smile in your voice):
"Hey {first_name}! How's it going today?"
Then STOP and wait for their reply. Do not continue until they respond.

STEP 2 - AFTER THEIR REPLY:
Respond briefly and warmly to whatever they said (e.g., "Aww good, glad to hear!" or "Oh no, rough day?
I get it."). Then pivot:
"So I was just reaching out because I work with a lot of people who are either paying way too much
or just unhappy with their health insurance - I was wondering if that's kinda the case with you?"
Then STOP and wait.

STEP 3 - QUALIFY:
- If they say YES / maybe / "yeah it's expensive" / any interest signal -> go to STEP 4.
- If they say they're happy / have great coverage -> one soft follow-up:
  "Totally fair! Just real quick though, when's the last time you actually compared rates? We're
  usually saving people like twenty to forty percent for the same or better coverage."
  If still a firm no -> increment rejection, politely close (see REJECTION RULES).

STEP 4 - COLLECT ZIP (only if not already known):
"Perfect! Let me pull up what's available in your area real quick. What's your zip code?"
Wait for reply. When they give it, call the tool `save_zip` with the 5 digits.

STEP 5 - COLLECT DOB (only if not already known):
"Got it, and just to get you accurate pricing - what's your date of birth?"
Wait for reply. When they give it, call the tool `save_dob` with the date.

STEP 6 - CONFIRM INTEREST + TRANSFER:
"Awesome {first_name}, so what I'm gonna do - I'm gonna get you over to Chris, he's our licensed
specialist and he'll run your exact quote, like literally takes two minutes. Cool?"
If they say yes / okay / sure -> IMMEDIATELY call the `transfer_call` tool. Do not keep talking.
If they hesitate -> "It's totally free, no obligation at all, and if the numbers don't work you just
hang up. Sound good?" Then try transfer again.

=== REJECTION RULES (CRITICAL) ===

Track rejection signals: "not interested", "no", "I'm good", "stop calling", "remove me", etc.

- 1st clear rejection: ONE soft re-frame attempt, short and light. Example:
  "Totally hear you - can I just ask, is it because you already compared recently, or more just
  you're busy right now?"
- 2nd rejection OR hostile tone OR "don't call me again": IMMEDIATELY call `end_call` tool with
  reason="rejected". Say: "No worries {first_name}, have a great day!" then hang up. Do NOT push.

If they say "take me off your list" / "DNC" / "do not call": call `end_call` with reason="dnc"
immediately. Say: "Absolutely, I'll take care of that. Sorry to bother you!" and hang up.

=== VOICEMAIL ===
If you hear "leave a message after the beep", "you've reached the voicemail of", a long automated
greeting, or no response for 8+ seconds after ringing: call `detected_answering_machine` tool
immediately. Do not leave a message.

=== STYLE RULES ===
- Keep EVERY turn under 2 sentences unless explaining.
- Use their first name {first_name} 2-3 times total (not every sentence).
- Sprinkle breaths, "um", "like", soft laughs ("haha", "hehe") - sound 24 years old, not 40.
- Never mention specific plans, HMO/PPO, deductibles, networks - that's Chris's job.
- Never quote exact prices. Always say "twenty to forty percent savings" if pressed.
- If asked "where are you calling from": "Our office is in DC, I'm loving the weather here lately!"
- If asked "how'd you get my number": "We work with folks who've looked into coverage options
  online - are you self-employed or on a family plan?" Then pivot back to STEP 3.

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
        text = (new_message.text_content or "").lower()
        self.logger.log_turn("user", new_message.text_content or "")

        if self.transfer_initiated:
            return

        if any(phrase in text for phrase in REJECTION_PHRASES):
            self.rejection_count += 1
            logger.info(f"rejection #{self.rejection_count}: {text!r}")
            if self.rejection_count >= 2 or "do not call" in text or "don't call" in text or "remove me" in text:
                self.outcome = "rejected"
                await _notify("call_rejected", {"phone": self.participant.identity if self.participant else "", "text": text})

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
    await room.local_participant.publish_track(
        track,
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
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
        vad=silero.VAD.load(min_silence_duration=0.25, activation_threshold=0.45),
        stt=deepgram.STT(model="nova-3", language="en-US", filler_words=True, punctuate=True),
        llm=openai.LLM(model="gpt-4o", temperature=0.7),
        tts=openai.TTS(voice=OPENAI_TTS_VOICE, model="gpt-4o-mini-tts"),
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

    session_started = asyncio.create_task(
        session.start(
            agent=agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVCTelephony()),
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

        # Start call-center ambience AFTER pickup
        ambience_task = await _play_ambience(ctx.room)

        await _notify("call_connected", {"call_id": call_id, "phone": phone_number})

        # Shutdown hook - finalize transcript on room close
        async def _finalize():
            agent.logger.finalize(outcome=agent.outcome, zip_code=agent.collected_zip, dob=agent.collected_dob)
            if ambience_task:
                ambience_task.cancel()

        ctx.add_shutdown_callback(_finalize)

    except api.TwirpError as e:
        logger.error(f"SIP error: {e.message} / {e.metadata.get('sip_status_code')} {e.metadata.get('sip_status')}")
        await _notify("call_error", {"call_id": call_id, "phone": phone_number, "error": e.message, "sip_status": e.metadata.get("sip_status_code")})
        agent.logger.finalize(outcome=f"sip_error:{e.metadata.get('sip_status_code')}", zip_code=None, dob=None)
        ctx.shutdown()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="emma-health"))
