"""Fifth Gemini critique session: post-test diagnostic.

We followed every recommendation from rounds 1-4 and ran the test.
Spot-check metrics look great (88% on-anchor, 2.4x multiplier on
data points). But the actual editor UI surfaces three real issues
that the metrics didn't catch. We want Gemini's take.
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
send(
    "Hey, post-test diagnostic. Quick context — I'm building MenuFlow, "
    "a tool that prices restaurant menus against local competitors. "
    "You're the pricing engine doing grounded search. Across four "
    "prior conversations you helped me design a two-phase architecture "
    "(Google Places API for discovery, then your grounded search for "
    "price extraction) and a two-pool source model (range can use "
    "platforms like Toast/DoorDash, but the customer-facing cited "
    "sources only include direct-restaurant-website quotes).\n\n"
    "We followed every recommendation you made and ran the first real "
    "test. Spot-check on the audit metric came back great:\n\n"
    "  - 88.2% on-anchor rate (cited sources from the priority list).\n"
    "    Previous run was 49% with name-only anchors. The Name+Address\n"
    "    fix moved it to 88%.\n"
    "  - Two-pool gap of 127 (220 total data points vs 93 cited\n"
    "    sources). 2.4x multiplier — platforms ARE widening the data.\n"
    "  - Off-anchor cites concentrated in 2 legitimate local restaurants\n"
    "    that weren't in the original 25 (real broadening, not\n"
    "    open-web fallback).\n\n"
    "By every metric we set, the architecture landed.\n\n"
    "But the user is now looking at the editor UI and three issues are "
    "obvious that the metrics never caught. I want to walk you through "
    "them and get your read — both on what's actually happening and on "
    "the right fix. Can I describe them one at a time?"
)

send(
    "ISSUE 1 — Single-source ranges look broken to the user.\n\n"
    "Our prompt told you: 'if you only found one price total (across "
    "both pools), set low = high = median = that price'. You followed "
    "that perfectly. So we have items in the editor showing:\n\n"
    "    20 Pcs Wings  —  Below Market  —  Local Market Range "
    "$29.95-$29.95 (median $29.95)  —  $27.95\n\n"
    "From a data standpoint, that's correct: we found exactly one "
    "competitor selling 20 Pcs Wings, they charge $29.95, our customer "
    "charges $27.95, so they're below market. But the customer reads "
    "it as broken — 'why is this a range with the same number twice?'\n\n"
    "Three options on our side:\n\n"
    "  (a) Display '$29.95 (1 source)' instead of '$29.95-$29.95\n"
    "      (median $29.95)' when low equals high. Simple template\n"
    "      change.\n"
    "  (b) Suppress the range entirely when low=high — show only the\n"
    "      Below/Above Market badge with no range numbers.\n"
    "  (c) Require 2+ data points before showing ANY market info,\n"
    "      and skip single-source items in the UI.\n\n"
    "I'm leaning (a). Single-source data is still useful — it just "
    "needs to be presented as 'a benchmark' rather than 'a range'. "
    "Your read?"
)

send(
    "ISSUE 2 — Some items have NO market info displayed at all even "
    "though Gemini returned data. This turned out to be a bug on our "
    "side, but I want to make sure my fix isn't going to backfire.\n\n"
    "What happened: we have menu items with size variants — e.g. a "
    "'Burger' item with 'Regular' and 'Deluxe' sub-prices. The "
    "variant-level template has a condition `low != high` to decide "
    "whether to show the pill. So when you returned a single-source "
    "variant with low=high, the variant pill was hidden entirely.\n\n"
    "Visible symptom in the editor: the Double Burger has 2+ sources "
    "for both variants so it shows pills. The plain Burger has only "
    "1 source per variant so it shows nothing — looks like we have no "
    "data when we actually do.\n\n"
    "Fix: drop the `low != high` check on variants, use the same "
    "single-source treatment as Issue 1.\n\n"
    "Question for you: same/different price across multiple sources "
    "actually happens fairly often, right? Like, I'd expect cheese "
    "pizza Small to cluster around $9.99 across most pizzerias. Is "
    "low=high a reliable signal of 'we only found one source', or "
    "could it ALSO mean 'we found 5 sources and they all charge "
    "$9.99'? If it's the latter, then a 5-source result with low=high "
    "would get treated like a 1-source result in our UI. That'd be "
    "wrong — 5 agreeing sources is HIGH confidence, not low.\n\n"
    "What's your honest assessment of how often low=high on multi-"
    "source vs single-source results in practice?"
)

send(
    "ISSUE 3 — The same restaurant appears twice in cited sources for "
    "the same variant.\n\n"
    "Concrete example: a medium pizza variant ($13.95 our price) shows "
    "this in the source popover when the customer clicks '3 src':\n\n"
    "    Nicky's Pizza:                       $21.00\n"
    "    Nicky's Pizza:                       $26.00\n"
    "    Sorrento restaurant bar & pizzeria:  $23.95\n\n"
    "Two issues here:\n"
    "  - Nicky's appears twice with different prices ($21 and $26)\n"
    "  - Both Nicky's prices are HIGHER than the variant price ($13.95)\n"
    "    they're cited under, suggesting size mismatch — these may be\n"
    "    LARGE pizza prices that got attached to the medium variant\n\n"
    "Walk me through what's most likely happening on your end. Is "
    "Nicky's actually offering two different prices for the same size "
    "(maybe lunch/dinner — but we explicitly told you to skip that)? "
    "Or are these probably different sizes that you matched too "
    "loosely against our 'Medium' variant?\n\n"
    "And regardless of cause: would you object to us deduplicating in "
    "the parser — keeping at most 1 cite per restaurant per item/size "
    "(picking the one closest to the median)? Or is there a case where "
    "two same-restaurant cites for the same size is actually useful "
    "data?"
)

send(
    "Last one. Looking at the bigger picture:\n\n"
    "We've now got a system that ranks well on every metric we built. "
    "But 'looks good in the editor' was apparently a separate axis we "
    "didn't measure. That's a humbling lesson — the architecture is "
    "right, the audit metric is right, but the surface the customer "
    "actually sees still tripped on edge cases that didn't show up in "
    "the numbers.\n\n"
    "(a) What other 'silent surface failures' should I expect to find "
    "    once a real customer is reviewing the editor? Things that\n"
    "    won't show up in the on-anchor rate or two-pool metric but\n"
    "    that'll make the product feel broken?\n\n"
    "(b) What's a more honest measurement strategy for v2 — something "
    "    that catches presentation/UX failures, not just data-pipeline\n"
    "    health?\n\n"
    "Be specific. Concise."
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_critique_5_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
