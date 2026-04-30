"""Retry the short-term mitigation question that hit a 503."""
import os, sys
from datetime import datetime
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except ImportError: pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception: pass

from google import genai
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "").strip())
chat = client.chats.create(model="gemini-2.5-pro")

TRANSCRIPT = []
def say(role, text):
    line = f"\n{'='*72}\n{role}\n{'='*72}\n{text}\n"
    print(line, flush=True)
    TRANSCRIPT.append(line)
def send(msg):
    say("ME", msg)
    resp = chat.send_message(msg)
    reply = (resp.text or "").strip() if resp else ""
    say("GEMINI", reply)
    return reply


send(
    "Quick context: I'm building a restaurant pricing tool that uses "
    "Gemini grounded search to find competitor menu prices. We're "
    "hitting an accuracy problem: when I ask for prices on a SPECIALTY "
    "variant (Meat Lovers Pizza, Double Burger, 30 Pcs Wings), the "
    "grounded search returns prices for the BASE item (Cheese Pizza, "
    "single Burger, 6 Wings) because the base item has way more search "
    "results. The specialty constraint gets relaxed during matching.\n\n"
    "The architecturally-correct fix is a 'base + modifier' estimation "
    "model — anchor a base price, apply a known modifier uplift "
    "(Meat Lovers = +$4-6 on cheese, Double = x1.5 on single burger). "
    "But that's weeks of work building modifier ontology + per-variant "
    "logic.\n\n"
    "I have a shippable product today and need a SHORT-TERM mitigation "
    "I can implement in the existing prompt + parser pipeline, without "
    "building the modifier model.\n\n"
    "What's the highest-impact single change that would noticeably "
    "reduce the specialty/quantity conflation rate? Be concrete:\n"
    "  - If prompt change: write the literal sentences\n"
    "  - If parser rule: describe the rule precisely\n\n"
    "Bias toward simple. I want one or two well-targeted changes, not "
    "five mediocre ones."
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = REPO / "storage" / "logs" / f"gemini_short_term_fix_{ts}.txt"
out.parent.mkdir(exist_ok=True)
out.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n[saved -> {out}]")
