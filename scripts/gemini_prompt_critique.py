"""Open a multi-turn chat with Gemini 2.5 Pro and ask it to introspect on
why it under-delivers on our pricing prompt. Conversation is driven from
this script — each turn is hand-written to feel like a colleague
walking another colleague through a tricky problem.

Output: prints every turn to stdout and saves full transcript to
storage/logs/gemini_critique_YYYYMMDD_HHMMSS.txt.

Usage:
    python scripts/gemini_prompt_critique.py
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows console defaults to cp1252 which chokes on em-dashes / arrows
# in the prompt text. Force UTF-8 so the printed turns survive.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except ImportError:
    pass

from google import genai

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    print("GEMINI_API_KEY not set", file=sys.stderr)
    sys.exit(1)

MODEL = "gemini-2.5-pro"
client = genai.Client(api_key=API_KEY)

TRANSCRIPT = []

def say(role, text):
    line = f"\n{'='*72}\n{role}\n{'='*72}\n{text}\n"
    print(line, flush=True)
    TRANSCRIPT.append(line)


# Open a chat. No tools — we want Gemini reasoning about itself, not
# searching. The conversation is the product.
chat = client.chats.create(model=MODEL)


def send(msg):
    """Send a user turn, print it, get reply, print it, return reply text."""
    say("ME", msg)
    resp = chat.send_message(msg)
    reply = (resp.text or "").strip() if resp else ""
    say("GEMINI", reply)
    return reply


# Real failure cases pulled from draft 310 (Agawam, MA pizzeria).
# These are items where Gemini-the-pricing-pipeline returned <3 sources
# even though the area clearly has many restaurants serving them.
FAILED_ITEMS = """
- Combination Pizza (Pizza category) — 1 source returned
- Pesto Chicken Pizza (Pizza category) — 2 sources
- Mediterranean Pizza (Pizza category) — 2 sources
- Margarita Pizza (Pizza category) — 2 sources
- Mushroom Burger (6 Oz Angus Burgers) — 1 source
- BLT (Club Sandwiches) — 1 source
- Ham Club Sandwich — 1 source
- Grilled Chicken Sandwich (Club Sandwiches) — 1 source
- Crispy Chicken Ranch BLT (Wraps) — 2 sources
"""


CURRENT_PROMPT = '''You are pricing a menu against REAL competitor prices. Restaurant
owners pay for this data because they need ACTUAL local benchmarks
to set their prices. Estimates are useless to them — they can guess
prices themselves. Your value is finding what real local restaurants
actually charge, on their real menu pages, today.

Read this twice: every price you return MUST be backed by a verbatim
quote from a real restaurant's menu page on the open web. No
estimates. No "typical range" guesses. No averages from articles. If
you cannot find real prices on real menu pages, return zero for the
item — that's the correct answer when real data isn't available, and
infinitely better than a confident estimate that misleads the owner.

For each item below, give me a low-high price range using REAL price
data from restaurants within 5 miles of {location}.

For items WITHOUT sizes, search: "(item name) (category) price near {location}"
For items WITH sizes, search EACH size separately: "(size) (item name) (category) price near {location}"

IMPORTANT: Only use restaurants within 5 miles of {location}. Do NOT
include restaurants from other states or distant cities.

VERBATIM QUOTES REQUIRED — this is the most important rule. For every
single source you cite, include a "quote" field with the exact text
from the restaurant's menu page where you saw the price. The quote
must contain BOTH:
  (a) the price you're citing (e.g. "$14.99" or "14.99")
  (b) the item name or a clear synonym
If you can't quote verbatim, DO NOT include the source. We'd rather
have two real cites than ten fabricated ones.

Sources we REJECT (do not cite):
  - "Average pizza price in Massachusetts" type articles or roundups
  - Yelp/Google review snippets that don't quote the menu
  - National chain pricing pages (unless that chain has a location
    within 5 miles AND you can quote the local franchise's menu)
  - Reddit threads, blog posts, news articles about restaurant pricing
  - Your own training-data knowledge of "what cheese pizza usually costs"

Sources we ACCEPT:
  - The restaurant's own website menu page
  - Online ordering platforms (Toast, Square, ChowNow, Slice,
    DoorDash, Uber Eats, Grubhub) showing the restaurant's actual
    menu items
  - PDF menus hosted on the restaurant's site

REQUIRED: every item MUST have at least 3 sources. No exceptions. If
you cannot find 3 sources for an item, do not return it. Set
low_cents to 0 and omit sources. Returning an item with 1-2 sources
is worse than returning no item at all.

[then JSON output schema with examples, plus a synonym list for
common items like Wings → Buffalo/Boneless/Chicken/Hot Wings, etc.]'''


# ----------------------------------------------------------------------
# CONVERSATION
# ----------------------------------------------------------------------

# Turn 1 — context + ask permission
send(
    "Hey, I want to run something by you. I'm building a tool called "
    "MenuFlow that helps small restaurant owners price their menu "
    "competitively. The pitch is simple: upload your menu, we tell you "
    "what the 5-10 restaurants near you charge for the same items, "
    "with real source links the owner can click.\n\n"
    "You are the pricing engine. We send you the menu items + the "
    "restaurant's address, and you use Google Search grounding to find "
    "what nearby places charge. The whole product depends on you "
    "finding real prices on real menu pages — not estimates.\n\n"
    "I've been iterating on the prompt for a while and I'm hitting a "
    "wall. Most items come back with 2 sources when I'm asking for 3+, "
    "or sometimes 0 even on basic items like wings. Before I keep "
    "guessing at words to add, I'd rather just ask you. Can I walk "
    "you through what I've got and where I'm stuck?"
)

# Turn 2 — show the prompt
send(
    "OK here's the current prompt. Read it carefully — pretend you're "
    "receiving this as a real task with real items attached. Tell me "
    "your honest first impression as the model that has to act on it.\n\n"
    "----- PROMPT START -----\n"
    f"{CURRENT_PROMPT}\n"
    "----- PROMPT END -----\n\n"
    "Concrete location for context: Agawam, MA 01001 (small town, "
    "Western Mass). Don't analyze line-by-line yet, just give me your "
    "gut take."
)

# Turn 3 — show the failures
send(
    "Now here are real items where you under-delivered on a recent run "
    "for an Agawam pizzeria. These came from your actual response, not "
    "a hypothetical:\n\n"
    f"{FAILED_ITEMS}\n"
    "Agawam has dozens of pizza places, sub shops, and burger joints "
    "within 5 miles. There are real menu pages out there for all of "
    "these. So when you returned 1 source for Combination Pizza, what "
    "actually happened on your end? Walk me through it — were the "
    "searches returning nothing useful? Were you finding pages but "
    "rejecting them on the verbatim-quote rule? Were you stopping at 1 "
    "because the rule said \"if you can't find 3, set to zero\" and you "
    "split the difference? I want the real answer, not a polite one."
)

# Turn 4 — based on whatever they said, push on it
send(
    "Stay with this for a sec. The prompt explicitly says \"if your "
    "first search returns fewer than 3, you have not searched hard "
    "enough — broaden the query and search again with synonyms.\" "
    "And there's a synonym list right in the prompt with 8 alternate "
    "names for Wings, multiple for Cheese Pizza, etc.\n\n"
    "But you came back with 2 sources for Pesto Chicken Pizza and 1 for "
    "BLT. Either:\n"
    "  (a) you didn't actually broaden the search — and if so, why? was "
    "      that instruction too easy to skip past?\n"
    "  (b) you DID broaden, but the second-pass results genuinely "
    "      didn't have verbatim quotable prices for those items in that "
    "      area\n"
    "  (c) something else I'm not seeing\n\n"
    "Which is closest to what actually happened?"
)

# Turn 5 — push on retrieval mechanics
send(
    "Help me understand the mechanics. When I tell you to find prices "
    "via Google Search grounding, what's the actual sequence on your "
    "side? Like — do you do one query and pick from those results? Do "
    "you fetch and parse the resulting pages? When the prompt says "
    "\"search again with synonyms,\" do you treat that as a literal "
    "instruction to fire another grounded search call, or does it just "
    "color how you interpret the first batch of results?\n\n"
    "I'm asking because if \"search again with synonyms\" doesn't "
    "actually trigger a second retrieval, then telling you to do it is "
    "useless — and I should rewrite the prompt to give you the right "
    "queries upfront instead of asking you to broaden later."
)

# Turn 6 — ask Gemini to write the prompt itself
send(
    "OK now flip this around. Forget my prompt. If you were writing the "
    "instructions YOU'D actually want to receive for this task — "
    "knowing how your retrieval works, knowing what you're good at, "
    "knowing what trips you up — what would the prompt look like? "
    "Don't worry about my structure or my JSON schema, I'll handle "
    "that. Just write the natural-language part: how would YOU ask you "
    "to do this?\n\n"
    "Be concrete. If there are queries you'd want pre-baked, write the "
    "literal query strings. If there are constraints that work better "
    "as positives instead of negatives, swap them. Whatever you'd "
    "actually want."
)

# Turn 7 — pressure test the new prompt
send(
    "Useful, thanks. Now I want to pressure-test that. Two questions:\n\n"
    "1. If I sent you that exact prompt and asked for prices on "
    "   \"Combination Pizza\" in Agawam, MA right now, what would "
    "   actually change vs. what happened on the original run? Be "
    "   honest — would you find more sources, or would the same "
    "   underlying retrieval limits hit and you'd still come back with "
    "   1?\n\n"
    "2. The thing that worries me about prompt redesign: a lot of the "
    "   instructions feel like they should change behavior but in "
    "   practice the model goes wherever the search results lead. Is "
    "   that fair? What's the actual leverage I have via the prompt "
    "   vs. what's just the ceiling of what's findable?"
)

# Turn 8 — the lever question
send(
    "Last one and then I'll let you go. If you had to rank what "
    "ACTUALLY moves the needle on source count for this task, in "
    "order, what's the list? I'm trying to figure out where to spend "
    "my next iteration:\n"
    "  - Tighter prompt language (what we've been doing)\n"
    "  - Pre-computed query strings I send you instead of asking you "
    "    to construct them\n"
    "  - Multi-turn structure: I send you results from one search and "
    "    ask you to follow up\n"
    "  - Letting you do the Google Places API work to find competitor "
    "    URLs first, then a separate pass to extract prices from those "
    "    URLs\n"
    "  - Something else I haven't thought of\n\n"
    "Rank them by impact and tell me why."
)


# ----------------------------------------------------------------------
# SAVE TRANSCRIPT
# ----------------------------------------------------------------------
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_critique_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
