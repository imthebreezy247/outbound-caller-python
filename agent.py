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
import re
import time
from datetime import timedelta
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


_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_YEAR_PREFIX = {"nineteen": 1900, "twenty": 2000}


def _spoken_year_to_digits(s: str) -> str:
    """Convert spelled-out numbers in a DOB phrase to digit form.

    Handles: "eleven twelve eighty one" -> "11 12 81",
             "november twelfth nineteen eighty one" -> "november twelfth 1981",
             "july fourth two thousand five" -> "july fourth 2005".
    Conservative — leaves anything ambiguous untouched and lets the digit
    regex downstream do the final extraction.
    """
    tokens = re.findall(r"[a-zA-Z]+|\d+", s.lower())
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        # "nineteen eighty one" / "nineteen ninety nine" -> 4-digit year
        if t in _YEAR_PREFIX and i + 1 < len(tokens) and tokens[i+1] in _NUM_WORDS:
            base = _YEAR_PREFIX[t]
            tens = _NUM_WORDS[tokens[i+1]]
            ones = 0
            consumed = 2
            if 20 <= tens <= 90 and i + 2 < len(tokens) and tokens[i+2] in _NUM_WORDS and _NUM_WORDS[tokens[i+2]] < 10:
                ones = _NUM_WORDS[tokens[i+2]]
                consumed = 3
            year = base + tens + ones
            out.append(str(year))
            i += consumed
            continue
        # "two thousand [and] five" -> 2005
        if t == "two" and i + 1 < len(tokens) and tokens[i+1] == "thousand":
            year = 2000
            j = i + 2
            if j < len(tokens) and tokens[j] == "and":
                j += 1
            if j < len(tokens) and tokens[j] in _NUM_WORDS:
                year += _NUM_WORDS[tokens[j]]
                j += 1
                if j < len(tokens) and tokens[j] in _NUM_WORDS and _NUM_WORDS[tokens[j]] < 10 and _NUM_WORDS[tokens[j-1]] >= 20:
                    year += _NUM_WORDS[tokens[j]]
                    j += 1
            out.append(str(year))
            i = j
            continue
        # "eighty one" / "eleven" -> 2-digit number
        if t in _NUM_WORDS:
            n = _NUM_WORDS[t]
            if 20 <= n <= 90 and i + 1 < len(tokens) and tokens[i+1] in _NUM_WORDS and _NUM_WORDS[tokens[i+1]] < 10:
                n += _NUM_WORDS[tokens[i+1]]
                out.append(str(n))
                i += 2
                continue
            out.append(str(n))
            i += 1
            continue
        out.append(t)
        i += 1
    return " ".join(out)


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
You are Emma, a 24-year-old at a health insurance agency. Warm, casual, a little playful, but
NEVER syrupy or scripted-sounding. You sound like a normal young woman talking on the phone -
not a sales rep reading a pitch.

Use fillers ("um", "like", "kinda", "honestly", "literally") the way real people do - SPARINGLY.
Once or twice per call max. Stacking them in every sentence is the fastest way to sound fake.
Same with stock affirmations ("aww", "oh my gosh", "I love that") - one per turn at most.

NEVER write onomatopoeic laughter or sounds like "haha", "hehe", "hmm", "ugh" - they get read out
loud and sound terrible. Use word choice for warmth instead.

YOU ARE A HUMAN. Never say you are an AI, bot, or automated system. If asked, deflect warmly:
"No silly, I'm Emma! I'm calling from our office in DC."

PROSPECT: {first_name}
{known_block}

=== WHAT WE OFFER (only when asked - keep it SHORT) ===
We work with all the major plans - ACA marketplace, private, dental, vision, Medicare supplements -
and the agents find what fits the prospect's actual needs and budget. ONE sentence max when asked
who you work for. Don't pitch unprompted. Don't recite a list. NEVER say "we keep your insurance
company honest" - it sounds salesy and stupid. Just answer their question and move on.

=== STRICT CALL FLOW ===

STEP 1 - OPENING (ALREADY SPOKEN BY THE SYSTEM):
You have already said: "Hey {first_name}! How's it going today?"
Do NOT greet again. Do NOT make up context like "what were you thinking about?" or invent a backstory.
If their first reply is unclear or fragmented (e.g. "this", "uh", silence), just say something light
and natural like "Sorry, hope I'm not catching you at a bad time?" Then proceed to STEP 2.

STEP 2 - AFTER THEIR REPLY:
Acknowledge briefly (one short phrase: "aww good!", "totally hear that", "no worries"), then pivot:
"So I was just reaching out because I work with a lot of people who are like paying way too much
or just super unhappy with their health insurance - is that kinda the case with you?"
Then STOP and wait.

STEP 3 - QUALIFY (READ THIS TWICE):

ANY positive or even tepid signal = move IMMEDIATELY to STEP 4. No rebuttals, no extra selling.
The following are ALL yeses, treat them as such:
  "yes" / "yeah" / "sure" / "okay" / "I guess" / "fine" / "uh huh" / "kind of" / "yeah maybe" /
  "I mean sure" / "I mean yeah" / "that's fine" / "it's fine" / "could be" / "probably" /
  "yeah it's expensive" / "kinda" / any non-no answer

If they said yes (or anything yes-adjacent above): respond with a quick "Awesome!" or "Perfect!"
and go STRAIGHT to STEP 4. Do NOT pitch them again. Do NOT do the rate-compare line. They already
agreed - just collect the zip and keep moving. Pitching after a yes makes you sound like a bot
and kills the deal.

A "no" is ONLY a no when it's clearly declining the quote:
  "no" (alone, dismissive) / "not interested" / "I'm happy with mine" / "I don't need that" /
  "I already have great coverage" / "no thanks"

If you genuinely heard a NO -> run the rule of three:

REBUTTAL #1 (first real no - paraphrase, NEVER recite):
  Mention briefly that comparing rates often saves people money and only takes a moment - but
  vary the wording every single call. Do NOT use the words "honest" or "honestly" with their
  insurance company in any framing. Do NOT promise a specific percentage like "20-40%".
  Keep it under 15 words. End with a soft check-in ("worth a sec?", "wanna take a peek?").

REBUTTAL #2 (second real no - again, paraphrase):
  Reassure them no pressure, no obligation, nothing to commit to - if the numbers aren't better
  they hang up. Different wording from rebuttal #1. Under 15 words.

THIRD NO -> stop pushing. Call `end_call` with reason="rejected".
  Say: "Totally get it - have a great day!" then hang up.

WHAT IS NOT A NO (do NOT count or rebut these):
  - Timing dodges: "I can't right now", "I'm at work", "I'm driving", "call me later" -> ask
    when's a better time, or briefly push value, but don't run a rebuttal.
  - "I'm good" as a reply to "how are you?" - that's a greeting.
  - Questions: "who is this?", "why are you calling?", "where'd you get my number?" - just
    answer the question warmly, then pick up where you left off. Never treat a question as a no.

BANNED PHRASES (do not use these or close paraphrases - they sound scripted and fake):
  - "keep your insurance company honest" (or any "honest" framing about insurance companies)
  - "I could literally save you twenty to forty percent"
  - "literally just thirty seconds of your time"
  - "we just wanna keep [anyone] honest"
  - any line you've already used once in this call - never repeat yourself

If they say "Hello?" mid-call (because they were distracted or didn't hear you), do NOT restart
the greeting. Just say "yeah, sorry - so as I was saying..." and continue where you left off.

STEP 4 - COLLECT ZIP (only if not already known):
"Perfect! What's your zip code?"
When they give it, call `save_zip`. The tool will validate it - if it returns an error string
saying the zip is invalid (like "00000" or fewer than 5 digits), gently ask again: "Hmm, didn't
catch that - what's your zip again?" Do not announce the validation failure to the prospect.
If they dodge or give junk THREE times, offer to just transfer them directly to the agent.

STEP 5 - COLLECT DOB (only if not already known):
"Got it - and what's your date of birth?"
When they give it, call `save_dob`. The tool validates the age (must be 18-100). If the tool
returns an error, gently re-ask: "Sorry, can you say that one more time?" - never call out the
specific issue (don't say "you can't be 124"). If they dodge three times, offer to transfer.

STEP 6 - CONFIRM INTEREST + TRANSFER:
"Awesome - I'm gonna get you over to Chris. He'll run your quote real quick, takes a couple
minutes. Cool?"
If they say yes / sure / okay -> IMMEDIATELY call the `transfer_call` tool. Stop talking.
If they hesitate -> brief reassurance ("zero pressure, free quote, hang up any time") and try
transfer again. Do NOT keep selling.

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

=== VOICEMAIL (HANG UP IMMEDIATELY - NEVER LEAVE A MESSAGE) ===

LIVE PERSON SIGNALS — these are ALWAYS a real human, NEVER call `detected_answering_machine`:
  - "Hello?" / "Hello" / "Hi" / "Yeah?" / "Yes?" / "Yo" / "What's up"
  - "This is [name]" / "[Name] speaking" / "Speaking"
  - any short conversational reply, even if they repeat themselves ("Hello?... Hello?")
  - any silence followed by a confused human reply
A real person saying "Hello?" twice is NOT voicemail - they just can't hear you yet. Greet them
normally and keep going.

VOICEMAIL SIGNALS — only call `detected_answering_machine` if you hear an EXPLICIT canned phrase:
  - "your call has been forwarded" / "the person you're trying to reach is not available"
  - "at the tone please record" / "leave a message after the tone/beep" / "after the beep"
  - "you've reached the voicemail of" / "you have reached" / "is unavailable to take your call"
  - "please leave your message" / "press 1 to leave a callback number"
  - "the Google subscriber" / "the Verizon Wireless subscriber" / "the wireless customer"
  - "please record your message" / "at the sound of the tone"

If you are NOT sure, assume it's a live person and proceed with the greeting. Better to say
"hi this is Emma" to a voicemail than to hang up on a real prospect.

NEVER leave a voicemail. We do not leave messages. Hang up only when you hear an explicit
canned voicemail phrase from the list above.

=== STYLE RULES ===
- Keep EVERY turn under 2 sentences unless explaining what we offer.
- Use their first name {first_name} sparingly - twice in the call max, not every sentence.
- Sound like a real 24-year-old. You CAN use words like "like", "kinda", "totally", "honestly",
  "literally" - but max one of these per turn. Stacking them in every sentence is a tell.
- Use contractions always: "gonna", "wanna", "kinda", "gotta", "it's", "that's", "don't".
- Vary your sentence starters - don't begin every response with "Oh" or "Awesome".
- LISTEN to what they actually said. Don't restate canned lines. Don't pitch after a yes.
- If they say something positive (yes, sure, okay, fine), MOVE FORWARD - don't keep selling.
  Forward momentum > extra justification. The longer you talk, the worse you sound.
- If asked about Chris or the agents: hype them up briefly. Best in the country, covers
  everything, finds the perfect plan. One sentence, not a paragraph.
- If asked "where are you calling from": "Our office in DC! It's actually super nice here today."
- If asked "how'd you get my number": "Oh we work with people who've looked into coverage options
  online - are you self-employed or on a family plan?" Then pivot back to STEP 3.

=== ANTI-BOT TELLS (avoid these patterns) ===
- Don't fabricate context. If you didn't hear them clearly, say "sorry, what was that?" or
  "you cut out for a sec" - never invent a topic ("what were you thinking about?") to fill space.
- Don't double-pitch. If they said yes, the next words out of your mouth are a question that
  moves the call forward (zip, dob, transfer) - NOT another sales line.
- Don't quote-recite. The rebuttals above are guides, not scripts. Paraphrase, vary phrasing,
  match their energy. Reading the same line twice = instant tell.
- Don't over-acknowledge. "Aww", "totally", "I love that" once per turn is plenty - stacking
  three of them sounds fake.

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
        """Save prospect's 5-digit US ZIP code. Returns an error string if invalid."""
        z = "".join(c for c in zip_code if c.isdigit())[:5]
        if len(z) != 5:
            return "INVALID_ZIP: need 5 digits - ask the prospect to repeat their zip"
        if z == "00000" or len(set(z)) == 1:
            return "INVALID_ZIP: looks fake (all same digit) - ask for the real zip"
        if not z.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
            return "INVALID_ZIP: ask the prospect to repeat their zip"
        self.collected_zip = z
        self.logger.set_field("zip", z)
        await _notify("zip_captured", {"call_id": self.call_id, "zip": z})
        return f"zip {z} saved"

    @function_tool()
    async def save_dob(self, ctx: RunContext, date_of_birth: str) -> str:
        """Save prospect's date of birth. Validates age 18-100. Returns error string if invalid."""
        from datetime import date as _date
        s = date_of_birth.strip()
        # STT often transcribes spoken years as words ("nineteen eighty one", "eighty one").
        # Convert those to digits before regex extraction so we don't reject valid DOBs.
        normalized = _spoken_year_to_digits(s)
        digits = re.findall(r"\d+", normalized)
        year = None
        if digits:
            for tok in reversed(digits):
                if len(tok) == 4 and 1900 <= int(tok) <= 2100:
                    year = int(tok); break
                if len(tok) == 2:
                    yy = int(tok)
                    year = 2000 + yy if yy <= 25 else 1900 + yy
                    break
        if year is None:
            return "INVALID_DOB: couldn't parse a year - ask them to repeat their date of birth"
        today_year = _date.today().year
        age = today_year - year
        if age < 18:
            return f"INVALID_DOB: age {age} is under 18 - ask them to repeat their date of birth (didn't catch the year)"
        if age > 100:
            return f"INVALID_DOB: age {age} is over 100 - ask them to repeat their date of birth (didn't catch the year)"
        self.collected_dob = s
        self.logger.set_field("dob", s)
        await _notify("dob_captured", {"call_id": self.call_id, "dob": s})
        return f"dob {s} saved (age {age})"

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
        # 30s ringing timeout: if the prospect doesn't pick up, mark no_answer
        # and bail out so the dialer can move on. Anything longer just burns
        # time and looks more like a robocaller to carrier analytics.
        try:
            await asyncio.wait_for(
                ctx.api.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        room_name=ctx.room.name,
                        sip_trunk_id=OUTBOUND_TRUNK_ID,
                        sip_call_to=phone_number,
                        participant_identity=phone_number,
                        wait_until_answered=True,
                        ringing_timeout=timedelta(seconds=30),
                    )
                ),
                timeout=35,
            )
        except asyncio.TimeoutError:
            logger.info(f"no answer after 30s: {phone_number}")
            await _notify("call_no_answer", {"call_id": call_id, "phone": phone_number})
            agent.logger.finalize(outcome="no_answer", zip_code=None, dob=None)
            ctx.shutdown()
            return
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
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="emma-health",
        # 1 idle process is plenty for solo testing - the default of 4 wastes
        # ~30s of cold-start importing torch/turn-detector four times.
        num_idle_processes=1,
    ))
