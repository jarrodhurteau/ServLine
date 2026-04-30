"""Fourth Gemini critique session: final sign-off before testing.

Show Gemini the three specific changes we made in response to round 3
and ask if anything still looks risky before we run it for real.

Changes since round 3:
  - Anchor list now carries Name + Address (Joe's Pizza problem)
  - Phantom Price explicitly handled in prompt + parser
  - total_data_points field added (the audit metric Gemini suggested)
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
# Quick recap (this is a fresh chat, no memory of round 3) + the three
# changes we made + ask for final red-flag check.
# ----------------------------------------------------------------------
send(
    "Hey, I want a final sign-off before we run this for real. Quick "
    "context — I'm building MenuFlow, a tool that helps restaurant "
    "owners benchmark their menu prices against local competitors. "
    "You're the pricing engine, doing grounded search to find real "
    "competitor prices. We've been iterating on the prompt + "
    "architecture across three prior conversations.\n\n"
    "Where we landed:\n\n"
    "ARCHITECTURE: Two-phase. Phase 1 (our app) calls Google Places "
    "API to get the 25 closest confirmed competitors with name + "
    "address + place_id. Phase 2 (you) gets that anchor list in the "
    "prompt and runs targeted searches against each known competitor "
    "before broadening to open-web discovery.\n\n"
    "TWO-POOL SOURCE MODEL:\n"
    "  - PRICE RANGE pool: any real local price counts — restaurant "
    "    websites + Toast/Slice/DoorDash/Grubhub/UberEats/ChowNow.\n"
    "  - CITED SOURCES array: only quotes from the restaurant's own "
    "    website. Owner clicks a cite -> lands on that restaurant's "
    "    site -> can verify the price visually. Platforms feed the "
    "    range but never the cites.\n\n"
    "FLOOR: 1 source minimum. We accept single-source items as "
    "success. If only platform data exists for an item (the 'Phantom "
    "Price' case), we return populated range + empty sources array.\n\n"
    "Now — three specific changes I made in response to your prior "
    "feedback. Tell me if these actually solve what you flagged, "
    "or if my fixes have new flaws.\n\n"
    "----- CHANGE 1: Address in anchor list -----\n"
    "You flagged the 'Joe's Pizza' problem — name-only anchors meant I "
    "could pull pricing from the wrong Joe's Pizza in a different city. "
    "Fix: each anchor is now `Name (Address)`. The relevant prompt "
    "section now reads:\n\n"
    "  PRIORITY COMPETITORS — these are confirmed restaurants within 5\n"
    "  miles of {location}, vetted by Google Places. Each anchor is\n"
    "  shown as `Name (Address)`. Use BOTH the name AND address in\n"
    "  your targeted search to make sure you're pulling pricing from\n"
    "  the EXACT competitor we identified — e.g. '\"Joe's Pizza 123\n"
    "  Main St\" combination pizza price'. There may be other\n"
    "  restaurants sharing the same name in nearby cities; the address\n"
    "  is how you confirm you've got the right one.\n"
    "    - Athena Pizza (623 Main St, Agawam, MA)\n"
    "    - Villa Pizza (455 Springfield St, Agawam, MA)\n"
    "    - [...up to 25 more...]\n\n"
    "----- CHANGE 2: Phantom Price made explicit -----\n"
    "You flagged that the prompt didn't say what to do when ALL prices "
    "came from platforms with zero direct-site cites. The prompt now "
    "has this explicit block:\n\n"
    "  PHANTOM PRICE CASE — if every price you found came from third-\n"
    "  party platforms (DoorDash/Toast/Slice/Grubhub/UberEats/ChowNow)\n"
    "  and ZERO restaurant websites had the item: return the\n"
    "  calculated low/high/median range from the platform data, with\n"
    "  an EMPTY sources array. This is correct, intended output. The\n"
    "  owner sees a price range badge with no clickable cites. Don't\n"
    "  suppress the result, don't fabricate a website cite, don't\n"
    "  skip the item — just return the range and empty sources.\n\n"
    "I also fixed the parser, which was dropping items with empty "
    "sources as 'no real data'. Now it keeps them when "
    "total_data_points > 0.\n\n"
    "----- CHANGE 3: total_data_points audit metric -----\n"
    "You suggested watching `total_data_points_found` vs "
    "`citable_sources_found` to verify the two-pool architecture is "
    "actually widening the data. Output schema now includes:\n\n"
    "  {\n"
    '    \"id\": 123,\n'
    '    \"low_cents\": 800,\n'
    '    \"high_cents\": 1400,\n'
    '    \"median_cents\": 1100,\n'
    '    \"total_data_points\": 8,    // ← new\n'
    '    \"sources\": [...5 entries from direct websites...]\n'
    "  }\n\n"
    "Per-batch logging: 'Two-pool metric: 87 total data points (range "
    "pool), 42 cited sources (direct sites only). gap=45 (higher gap "
    "= platforms widening data).'\n\n"
    "Three questions:\n"
    "  (a) Do these three changes actually address what you flagged?\n"
    "  (b) Do any of MY fixes introduce new problems?\n"
    "  (c) Anything else we still haven't addressed that'll bite us?"
)

# Turn 2 — push on the actual behavior under the new spec
send(
    "Walk me through what changes for you mechanically with these three "
    "fixes in place. Specifically:\n\n"
    "(1) The address-in-anchor change: how does that actually shift "
    "your search behavior? Are you now firing different queries than "
    "you would have before? Is the address you'll use in queries the "
    "FULL address ('123 Main St, Agawam, MA') or just the street "
    "('123 Main St')? Be honest about whether this is a meaningful "
    "behavioral change or just feel-good prompt language.\n\n"
    "(2) On total_data_points: how confident are you that you'll "
    "actually count these correctly? Like — if you find a Toast price "
    "you can't quote-validate but the price seems real, does it count "
    "toward total_data_points? What about a price you DID quote-"
    "validate from a direct site that we then accept into the sources "
    "array — is that price counted ONCE in total_data_points or "
    "double-counted?\n\n"
    "Just want to make sure my audit metric isn't going to be garbage."
)

# Turn 3 — final go/no-go
send(
    "Last thing. Two questions and then we'll go test:\n\n"
    "(1) Go / no-go? Are we ready to run this for real, or is there "
    "    one specific thing you'd still want fixed first?\n\n"
    "(2) When the test runs and I look at the results, what's the "
    "    SINGLE most diagnostic thing for me to check that'll tell me "
    "    fast whether the architecture is actually delivering on its "
    "    promise? Not 'are pills appearing' (that's the surface) but "
    "    something deeper — what's the smoke-test that'd surface a "
    "    silent failure in either Phase 1 or Phase 2?\n\n"
    "Be concise."
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_critique_4_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
