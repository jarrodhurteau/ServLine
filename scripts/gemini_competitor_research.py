"""Ninth Gemini conversation: market research on existing competitors.

Different from prior sessions — this one needs Gemini to actually
SEARCH for real services that exist today. Enabling Google Search
grounding so it can name specific products, pricing, coverage. We
don't want architectural opinions here; we want a real list of who
already does this and where the gaps are.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except ImportError:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from google import genai
from google.genai import types

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    print("GEMINI_API_KEY not set", file=sys.stderr)
    sys.exit(1)

client = genai.Client(api_key=API_KEY)

# IMPORTANT: this conversation needs Google Search grounding because
# the questions are factual ("who builds X right now"), not
# architectural. Other critique sessions used pure reasoning; this
# one needs real-world product info.
chat = client.chats.create(
    model="gemini-2.5-pro",
    config=types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.1,
    ),
)

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
    "Quick market research question — please search the web for this, "
    "I want real product information not architectural opinions.\n\n"
    "I'm building MenuFlow — a tool that helps small independent "
    "restaurant owners (mom-and-pop pizzerias, sandwich shops, "
    "diners) see what nearby competitors charge for the same items "
    "on their menu. Specifically: small operators upload their PDF "
    "menu, we tell them 'your Cheese Pizza is $13.95; the 4 closest "
    "competitors charge $12.50 to $15.00 for theirs, here are the "
    "links.' We sell at $80/month.\n\n"
    "I want to know if this product already exists. NOT generic "
    "menu engineering consulting (Aaron Allen, etc), and NOT "
    "enterprise menu trend data for suppliers (Datassential, NPD, "
    "Circana). Specifically: a software product that an independent "
    "restaurant owner — say a single-location pizza shop in a small "
    "town — could subscribe to and immediately get a list of what "
    "their 5-25 closest neighbors charge for the same menu items.\n\n"
    "Three questions:\n\n"
    "  (1) Does that product currently exist? If yes, name names —\n"
    "      include any startups, recent YC companies, or products\n"
    "      that have launched in the last 12-24 months. Pricing,\n"
    "      coverage, geographic focus if you can find it.\n\n"
    "  (2) If multiple products exist, who are the front-runners?\n"
    "      What are people saying about coverage and accuracy?\n\n"
    "  (3) If NOTHING exists at the small-operator level — what's\n"
    "      the explanation? Is the market too small? Is the data\n"
    "      acquisition too hard for unit economics to work? Has\n"
    "      it been tried and failed?"
)

send(
    "Now search specifically for any product or service that does "
    "PER-ITEM HYPERLOCAL competitor menu pricing — i.e., 'show me "
    "what the 5 nearest restaurants charge for this exact item.' "
    "Filter out anything that's just trend reports, food cost "
    "tracking, or recipe/inventory tools. I'm looking for the "
    "specific use case: a restaurant owner asks 'what does Joe's "
    "Pizza three blocks away charge for a medium pepperoni?' and "
    "gets a real answer.\n\n"
    "If you find anything, dig in: is it actually delivering on "
    "that promise, or is it vapor / dead / pivoted to something "
    "else? Real product reviews, not press releases."
)

send(
    "Last one. Based on what you found: is the MenuFlow concept a "
    "white space (no one's done it), a graveyard (people have tried "
    "and failed), or a competitive market (multiple working "
    "products)? One sentence each + brief reasoning."
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_competitor_research_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
