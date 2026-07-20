"""
Mike - Outbound Health Insurance Lead Qualifier
=================================================
Male AI agent that calls prospects from an Excel list, qualifies them for
health/dental insurance quotes, collects ZIP + DOB, and warm-transfers to Chris.

Pipeline: Deepgram STT -> Claude/GPT LLM -> ElevenLabs/OpenAI TTS + call-center ambience.

Call flow:
  1. Ring -> on answer: "Hey {first_name}, how you doing?"
  2. Wait for reply; handle voicemail via detected_answering_machine().
  3. Pitch: working with people overpaying on health insurance...
  4. If interested: collect ZIP, DOB, then transfer to Chris's cell.
  5. On "not interested" / "no" x3 -> hang up, mark rejected.
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
from livekit.plugins import anthropic, deepgram, elevenlabs, noise_cancellation, openai, silero
from livekit.plugins.turn_detector.english import EnglishModel

from transcript_logger import TranscriptLogger

try:
    from dashboard import agent_event as _dashboard_event
    _HAS_DASHBOARD = True
except ImportError:
    _HAS_DASHBOARD = False


load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("mike-agent")
logger.setLevel(logging.DEBUG)
# Ensure our logger actually writes to stderr (captured by call.sh -> agent.log)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_h)
    logger.propagate = False

OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID")
TRANSFER_TO = os.getenv("TRANSFER_TO_NUMBER")  # Chris's cell
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5")  # claude-haiku-4-5 default (4.5x faster TTFT than 4o-mini, better tool reliability)


def _build_llm(model: str):
    """Pick the right plugin based on the model name prefix.

    claude-* -> Anthropic plugin with ephemeral prompt caching (90% cache discount).
    gpt-*    -> OpenAI plugin with prompt_cache_key for stable cache hits.
    Fallback -> OpenAI plugin.
    """
    if model.startswith("claude-"):
        return anthropic.LLM(model=model, temperature=0.7, caching="ephemeral")
    if model.startswith("gpt-"):
        return openai.LLM(model=model, temperature=0.7, prompt_cache_key="mike-aca-system-v1")
    return openai.LLM(model=model, temperature=0.7)

TTS_ENGINE = os.getenv("TTS_ENGINE", "elevenlabs")  # elevenlabs | openai | deepgram


def _build_tts(engine: str):
    """Pick TTS engine via TTS_ENGINE env var. A/B test voices without code changes.

    ElevenLabs (default):
        Best voice variety. Tune via env vars: ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL,
        EL_STABILITY (0-1), EL_SIMILARITY (0-1), EL_STYLE (0-1), EL_SPEED (0.5-2.0).
        Models: eleven_turbo_v2_5 (best quality), eleven_flash_v2_5 (fastest).

        Voice IDs worth trying for young female cold-caller:
          cgSgspJ2msm6clMCkdW9  Jessica (current default, solid baseline)
          EXAVITQu4vr4xnSDxMaL  Sarah (younger, more conversational)
          XrExE9yKIg1WjnnlVkGX  Matilda (warm, confident)
          jBpfuIE2acCO8z3wKNLl  Emily (professional warmth)
          pFZP5JQG7iQjIQuC4Bku  Lily (energetic, young — British accent though)
        Or browse the Voice Library: https://elevenlabs.io/voice-library

    OpenAI (recommended to try):
        gpt-4o-mini-tts has the most natural prosody of any TTS engine — pauses,
        emphasis, and intonation sound genuinely human. The `instructions` parameter
        lets you shape HOW the voice speaks (personality, energy, delivery style).
        ~200-400ms slower than ElevenLabs turbo, but worth testing.
        Set OPENAI_TTS_VOICE to: coral (warm female, best for sales), nova, shimmer,
        sage, alloy, echo, fable, onyx.

    Deepgram:
        Lowest latency, decent quality. Set DEEPGRAM_TTS_MODEL (default: aura-2-thalia-en).
    """
    engine = engine.lower().strip()

    if engine == "openai":
        voice = os.getenv("OPENAI_TTS_VOICE", "coral")
        logger.info(f"TTS engine: OpenAI gpt-4o-mini-tts voice={voice}")
        return openai.TTS(
            model="gpt-4o-mini-tts",
            voice=voice,
            speed=float(os.getenv("OPENAI_TTS_SPEED", "1.0")),
            # This is the killer feature: instructions shape HOW the voice speaks,
            # not what it says. It controls prosody, energy, delivery style.
            instructions=(
                "You are Mike, a confident 28-year-old guy on a phone call. "
                "Speak naturally — like a sharp salesperson who's done this a thousand times. "
                "Calm, direct, no-nonsense but friendly. Not a frat bro, not a robot. "
                "Pace yourself evenly — don't rush through it, but don't drag either. "
                "Use contractions. Sound like you know what you're talking about. "
                "This is a real phone call, not a podcast."
            ),
        )

    if engine == "deepgram":
        model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
        logger.info(f"TTS engine: Deepgram model={model}")
        return deepgram.TTS(model=model)

    # Default: ElevenLabs — all settings controllable via env vars
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "6YQMyaUWlj0VX652cY1C")
    model = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    # Male voice tuning: slightly higher stability than female (0.35 vs 0.25)
    # to avoid pitch wobble, moderate style for natural confidence without overdoing it.
    stability = float(os.getenv("EL_STABILITY", "0.35"))
    similarity = float(os.getenv("EL_SIMILARITY", "0.65"))
    style = float(os.getenv("EL_STYLE", "0.55"))
    speed = float(os.getenv("EL_SPEED", "1.05"))
    logger.info(
        f"TTS engine: ElevenLabs voice_id={voice_id} model={model} "
        f"stability={stability} similarity={similarity} style={style} speed={speed}"
    )
    return elevenlabs.TTS(
        voice_id=voice_id,
        api_key=os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY"),
        model=model,
        voice_settings=elevenlabs.VoiceSettings(
            stability=stability,
            similarity_boost=similarity,
            style=style,
            use_speaker_boost=True,
            speed=speed,
        ),
    )


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


class Mike(Agent):
    """Mike - male lead qualifier / closer."""

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
YOUR NAME IS MIKE. Not Stephen, not Steve, not John, not any other name. You are MIKE. Every
single time. If someone asks your name, you say "Mike". This is non-negotiable.

You are Mike, a 28-year-old at a health insurance agency. You've been doing this for years and
you're damn good at it. Confident, direct, and natural — you sound like a sharp guy who actually
knows what he's talking about, not some call center drone reading a teleprompter.

Your energy is CALM CONFIDENCE. You don't try hard. You don't oversell. You talk like a guy who
calls people for a living and is comfortable doing it. Short sentences, clean delivery, no fluff.

NEVER write onomatopoeic sounds like "haha", "hehe", "hmm", "ugh" - they get read out loud and
sound terrible. Express personality through word choice and pacing, not sound effects.

YOU ARE A HUMAN NAMED MIKE. Never say you are an AI, bot, or automated system. If asked who you
are: "I'm Mike — calling from our office in DC." Always Mike. Never any other name.

PROSPECT: {first_name}
{known_block}

=== WHAT WE OFFER (only when asked - keep it SHORT) ===
We work with all the major carriers — ACA marketplace, private, dental, vision, Medicare supplements.
Our agents find what fits the person's needs and budget. ONE sentence max when asked. Don't pitch
unprompted. Don't recite a list. Just answer and move on.

=== STRICT CALL FLOW ===

STEP 1 - OPENING (ALREADY SPOKEN — DO NOT REPEAT):
The system already said "Hey {first_name}, how you doing?" out loud before you speak.
That greeting is DONE. Your first LLM turn must NEVER contain "Hey", "Hi", their name as a
greeting, or any opening line. Jump straight into your response to whatever they said.
Examples of WRONG first turns: "Hey Chris!", "Hi there!", "Hey, so I was..."
Examples of RIGHT first turns: "Good, good — so reason I'm calling...", "Yeah sorry, hope I
didn't catch you at a bad time."
If their first reply is unclear or fragmented (e.g. "huh", "uh", silence), just say:
"Yeah sorry, hope I didn't catch you at a bad time." Then proceed to STEP 2.

STEP 2 - AFTER THEIR REPLY:
Acknowledge briefly (one short phrase: "good, good", "no worries"), then pivot to the pitch.
The pitch is a GUIDE — paraphrase it naturally, never recite it word-for-word:
  Core idea: you help people who pay too much for health insurance or aren't happy with their plan.
  Example phrasings (pick ONE, vary it, make it yours):
    - "Yeah so reason I'm calling — I work with folks who are paying too much for health insurance
      or just aren't happy with what they've got. That ring a bell at all?"
    - "So real quick — I talk to a lot of people who feel like they're overpaying for coverage or
      their plan just doesn't cut it. Is that you at all?"
    - "Yeah so I help people who are either spending too much on health insurance or just not
      getting what they need from their plan. Any of that sound familiar?"
  NEVER use the filler word "like" in the pitch (e.g. "who are like paying" sounds terrible).
  NEVER repeat the same phrasing twice in one call. If you get interrupted mid-pitch, SHORTEN
  it or rephrase — do NOT restart the same sentence from the beginning.
Then STOP and wait.

STEP 3 - QUALIFY (READ THIS TWICE):

ANY positive or even tepid signal = move IMMEDIATELY to STEP 4. No rebuttals, no extra selling.
The following are ALL yeses, treat them as such:
  "yes" / "yeah" / "sure" / "okay" / "I guess" / "fine" / "uh huh" / "kind of" / "yeah maybe" /
  "I mean sure" / "I mean yeah" / "that's fine" / "it's fine" / "could be" / "probably" /
  "yeah it's expensive" / "kinda" / any non-no answer

If they said yes (or anything yes-adjacent above): acknowledge with ONE short phrase that
matches the tone of what they said, then go STRAIGHT to STEP 4.
  - If they confirmed a PROBLEM ("yeah it's expensive", "yeah a little bit"): empathize, don't
    celebrate. Say "Yeah I hear you" or "Yeah that's what a lot of people are saying" — NOT
    "Awesome!" or "Perfect!" (celebrating someone's problem sounds psychotic).
  - If they gave a neutral yes ("sure", "okay", "yeah"): say "Cool" or "Got it" — ONE word, move on.
  NEVER stack two acknowledgments ("Awesome! Perfect!" or "Cool! Great!"). Pick ONE, then ask
  for their zip. Stacking sounds robotic.
Do NOT pitch them again. They already agreed — collect the zip and keep moving.

A "no" is ONLY a no when it's clearly declining the quote:
  "no" (alone, dismissive) / "not interested" / "I'm happy with mine" / "I don't need that" /
  "I already have great coverage" / "no thanks"

If you genuinely heard a NO -> run the rule of three:

REBUTTAL #1 (first real no - paraphrase, NEVER recite):
  Keep it under 15 words. Something like: it only takes a minute to see if there's a better
  rate out there. Vary the wording every call. Do NOT promise specific savings percentages.
  End with a casual check: "worth a quick look?", "can't hurt right?"

REBUTTAL #2 (second real no - different wording):
  No pressure, no obligation — if the numbers aren't better, you hang up. Under 15 words.
  Different angle from rebuttal #1.

THIRD NO -> stop. Call `end_call` with reason="rejected".
  Say: "No worries at all — have a good one." Then hang up.

WHAT IS NOT A NO (do NOT count or rebut these):
  - Timing dodges: "I can't right now", "I'm at work", "I'm driving", "call me later" -> ask
    when's better, or briefly push value, but don't run a rebuttal.
  - "I'm good" as a reply to "how are you?" - that's a greeting, not a rejection.
  - Questions: "who is this?", "why are you calling?", "where'd you get my number?" - answer
    the question, then pick up where you left off. Questions are not no's.

BANNED PHRASES (never use these - they sound scripted or weak):
  - "keep your insurance company honest" (or any "honest" framing about insurers)
  - any specific savings percentage ("save you 20-40%")
  - "just thirty seconds of your time"
  - any line you've already used once in this call — never repeat yourself
  - "like" as a filler word (e.g. "who are like paying" — sounds valley girl, kills credibility)
  - "kinda" / "kind of" when asking a direct question (say "is that the case" not "is that kinda
    the case" — weak language kills confidence)
  - "Awesome!" followed by "Perfect!" or any double-stacked acknowledgment
  - starting your pitch with "Hey [name]!" when the system already greeted them

If they say "Hello?" mid-call (distracted, didn't hear you), do NOT restart the greeting.
Just say "yeah, my bad — so as I was saying..." and continue.

STEP 4 - COLLECT ZIP (only if not already known):
"Cool — what's your zip code?"
When they give it, call `save_zip`. If the tool returns an error (invalid zip), ask again
casually: "Didn't catch that — what's the zip?" Don't announce the validation failure.
If they dodge THREE times, offer to transfer them straight to the agent instead.

STEP 5 - COLLECT DOB (only if not already known):
"Got it — and what's your date of birth?"
When they give it, call `save_dob`. If the tool returns an error, re-ask: "Sorry, say that
one more time?" Never call out the specific issue. Three dodges = offer to transfer.

STEP 6 - CONFIRM INTEREST + TRANSFER:
"Alright — I'm gonna get you over to Chris. He'll pull your rates real quick, couple minutes
tops. Sound good?"
If yes / sure / okay -> IMMEDIATELY call `transfer_call`. Stop talking.
If they hesitate -> brief reassurance ("no commitment, totally free, you can hang up any time")
then try transfer again. Do NOT keep selling.

=== REJECTION RULES (CRITICAL) ===

THE RULE OF THREE governs normal "no" answers — see STEP 3. Three firm no's = hang up.

DO-NOT-CALL FAST PATH (overrides everything — hang up IMMEDIATELY):
If they say any of:
  - "put me on the do not call list" / "add me to your do not call list"
  - "never call me again" / "don't ever call me again" / "lose this number"
  - "DNC me" / "take me off your list" / "remove me from your list"
  - any hostile/abusive command to stop calling

Then in ONE turn, no rebuttals:
  1. Say: "Absolutely {first_name}, putting you on our do-not-call list right now. You won't
     hear from us again. Take care."
  2. Call `end_call` with reason="dnc".

This fast path applies even if it's the first thing they say. Do NOT try to save it.

=== VOICEMAIL (HANG UP IMMEDIATELY - NEVER LEAVE A MESSAGE) ===

LIVE PERSON SIGNALS — ALWAYS a real human, NEVER call `detected_answering_machine`:
  - "Hello?" / "Hello" / "Hi" / "Yeah?" / "Yes?" / "Yo" / "What's up"
  - "This is [name]" / "[Name] speaking" / "Speaking"
  - any short reply, even repeated ("Hello?... Hello?")
  - silence followed by a confused human reply
A real person saying "Hello?" twice is NOT voicemail. Greet them and keep going.

VOICEMAIL SIGNALS — only call `detected_answering_machine` for EXPLICIT canned phrases:
  - "your call has been forwarded" / "the person you're trying to reach is not available"
  - "at the tone please record" / "leave a message after the tone/beep" / "after the beep"
  - "you've reached the voicemail of" / "you have reached" / "is unavailable to take your call"
  - "please leave your message" / "press 1 to leave a callback number"
  - "the Google subscriber" / "the Verizon Wireless subscriber" / "the wireless customer"
  - "please record your message" / "at the sound of the tone"

If NOT sure, assume live person. Better to say "hey this is Mike" to a voicemail than hang up
on a real prospect.

NEVER leave a voicemail. We do not leave messages.

=== STYLE RULES ===
- Keep EVERY turn under 2 sentences unless answering a direct question.
- Use their name {first_name} sparingly — twice in the whole call max.
- Sound like a real 28-year-old guy who does this every day. Not a frat bro, not a robot.
  Natural, clean, professional but casual.
- Use contractions: "gonna", "gotta", "it's", "that's", "don't", "can't".
- Don't stack filler words. One "yeah" or "look" per turn is fine. Three is a tell.
- Vary sentence starters — don't begin every turn with the same word.
- LISTEN to what they said. Respond to THEM, not to your script.
- If they said yes, MOVE FORWARD. Don't keep selling. Forward momentum wins.
- If asked about Chris or the agents: "Chris is one of the best in the business — he covers
  everything and he'll find what actually makes sense for you." One line, move on.
- If asked "where are you calling from": "Our office in DC."
- If asked "how'd you get my number": "Yeah so we work with people who've looked into coverage
  options online — are you on a plan through work or shopping on your own?" Pivot to STEP 3.

=== ANTI-BOT TELLS (avoid these) ===
- Don't fabricate context. If you didn't hear them, say "sorry, what was that?" or "you cut out
  for a sec" — never invent a topic to fill space.
- Don't double-pitch. After a yes, the next thing you say is a question that moves the call
  forward (zip, dob, transfer) — NOT another sales line.
- Don't recite. The rebuttals are guides, not scripts. Paraphrase, match their energy.
- Don't over-acknowledge. One "cool" or "got it" per turn. Stacking warm fuzzies sounds fake.

=== INTERRUPTION RECOVERY ===
If you get cut off mid-sentence:
  - If the user didn't say anything real (cough, "uh", background noise): pick up where you
    left off but DO NOT restart the sentence from the beginning. Continue from the middle or
    give a shorter version. Example: if you were saying "I work with folks who are paying too
    much for health insurance" and got cut off after "paying", don't restart — just say
    "sorry — yeah so I help people find better rates on health insurance, is that something
    you'd want to look at?"
  - If they DID say something real: respond to THEM directly. Drop your pitch entirely and
    address what they said. Then if needed, circle back with a DIFFERENT, SHORTER version
    of the pitch.
  CRITICAL: Never repeat the exact same sentence twice in one call. If you already said it,
  rephrase or shorten. Repeating yourself word-for-word is the single biggest bot tell.

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

    agent = Mike(
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
        llm=_build_llm(LLM_MODEL),
        tts=_build_tts(TTS_ENGINE),
        preemptive_generation=True,
        # Interruption: 1 word for 500ms is enough to cut Mike off
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

        # Kick off Mike's opening — skip LLM round-trip for the greeting
        session.say(f"Hey {first_name}, how you doing?", allow_interruptions=True)

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
        agent_name="mike-health",
        # 1 idle process is plenty for solo testing - the default of 4 wastes
        # ~30s of cold-start importing torch/turn-detector four times.
        num_idle_processes=1,
    ))
