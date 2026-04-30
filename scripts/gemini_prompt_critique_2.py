"""Follow-up conversation with Gemini after the initial critique.
The user pushed back on Gemini's "1 source is fine" recommendation:
they need a 2-source floor because a low-high range requires at least
two data points to be meaningful.

We replay enough of the prior context for Gemini to pick up where we
left off, then put the 2-source-minimum constraint to it and probe
how it would actually behave.

Output: prints every turn + saves transcript to storage/logs/.
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

# Force UTF-8 stdout (Windows console defaults to cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from google import genai

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    print("GEMINI_API_KEY not set", file=sys.stderr)
    sys.exit(1)

client = genai.Client(api_key=API_KEY)
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


# ----------------------------------------------------------------------
# Replay context — this is a fresh chat session, so we have to give
# Gemini enough to pick up where we left off without re-running every
# turn. Compressed but complete.
# ----------------------------------------------------------------------
send(
    "Hey, picking up where we left off yesterday. Quick recap so we're "
    "synced — we talked about my MenuFlow tool, where you're the pricing "
    "engine doing grounded search to find real competitor menu prices. "
    "I had a hard 3-source minimum rule, and you told me you'd been "
    "actively disobeying it because for small towns like Agawam, MA the "
    "rule was forcing you to throw away 1-2 perfectly verified sources "
    "in exchange for an empty-set failure. Your recommendation was to "
    "drop the floor entirely — accept 1 source as a valid result, since "
    "1 verified competitor price beats 0.\n\n"
    "I went and pitched that to my collaborator. They pushed back on "
    "one thing and I think they have a point. I want your take.\n\n"
    "Their argument: the product output is a price RANGE — low/high. A "
    "range mathematically needs at least 2 data points. With 1 source "
    "you don't have a range, you have a single quoted price, and that's "
    "a different (less useful) product for the restaurant owner. They'd "
    "rather see \"competitors charge $14-$17\" than \"one competitor "
    "charges $15.\" So they want a HARD 2-source minimum. Not 1, not 3. "
    "Two.\n\n"
    "Before I commit to that, I want to know: does a 2-source floor "
    "trigger the same disobedience pattern we hit at 3? Or is 2 actually "
    "a threshold you can meet on most items in a small town? Honest read."
)

# Follow-up: walk through the same Agawam tracer round under the new rule
send(
    "Walk me through what changes for the items I gave you yesterday — "
    "the 9 underperformers from the Agawam pizzeria run. Specifically:\n\n"
    "  - Combination Pizza (you returned 1 source)\n"
    "  - Mushroom Burger (1 source)\n"
    "  - BLT (1 source)\n"
    "  - Ham Club Sandwich (1 source)\n"
    "  - Grilled Chicken Sandwich (1 source)\n"
    "  - Pesto Chicken Pizza (2 sources)\n"
    "  - Mediterranean Pizza (2 sources)\n"
    "  - Margarita Pizza (2 sources)\n"
    "  - Crispy Chicken Ranch BLT wrap (2 sources)\n\n"
    "Under a hard 2-source rule, what's the actual partition? Which of "
    "these would have come back with their existing 2 sources accepted "
    "as success, which would have been outright skipped (set to zero), "
    "and would you have actually pushed harder on the 1-source ones to "
    "find a second — or would you have just given up and skipped them?\n\n"
    "And the deeper question: when an item only has ONE genuinely "
    "findable source in the area, does forcing a 2-minimum cause you to "
    "either (a) skip it entirely, or (b) start lowering quality "
    "standards to manufacture a second source? I really want to know "
    "which way you'd lean."
)

# Final turn: get a concrete recommendation
send(
    "OK, two things to wrap up:\n\n"
    "1. Given everything we just said, what's the actual prompt language "
    "you'd want for the 2-source rule? Write the literal sentences you'd "
    "want to receive — phrased the way that makes you most likely to "
    "(a) hit 2 when 2 is genuinely findable, (b) skip cleanly when it's "
    "not, and (c) NOT lower your quality bar to fake a second source.\n\n"
    "2. For the items where you'd skip (genuinely 1 source available), "
    "what should I do with that empty result downstream? The owner is "
    "going to see SOME items with pricing data and SOME with nothing — "
    "is there a way to message that gracefully without making the empty "
    "ones look like a system failure? Like, what would the customer-"
    "facing copy say when an item's pricing column is blank because we "
    "couldn't find 2 sources?"
)


# ----------------------------------------------------------------------
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_critique_2_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
