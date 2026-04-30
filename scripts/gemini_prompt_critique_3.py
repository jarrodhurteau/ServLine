"""Third Gemini critique session: review the FINAL design before testing.

We've made significant changes since round 2:
  - Two-pool source model (range = web+platforms, sources = direct only)
  - Lowered floor to 1 (single-source items count as success)
  - Two-phase architecture: Places API discovery -> Gemini extraction
  - Verifiability framing throughout
  - MENU FRESHNESS rule
  - Synonym/quote validator alignment

Show Gemini the final prompt + architecture, ask if anything looks
inconsistent or fragile, push on specific scenarios it flagged before.
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


# Final prompt as it stands now (with placeholders).
FINAL_PROMPT = """\
You are pricing a menu against REAL competitor prices. There
are TWO things you produce per item, and they have different rules:

  (1) The PRICE RANGE (low/high/median) — the numbers that drive the
      "Below Market / Higher Range" badge the owner sees on each item.
      Use as much real local pricing data as you can find from BOTH
      restaurant websites AND third-party platforms (Toast, Slice,
      ChowNow, DoorDash, Uber Eats, Grubhub). More data points = a
      truer range. The range stays internal to the calculation — the
      owner sees the numbers, not the underlying mix of sources.

  (2) The CITED SOURCES array (the clickable list shown to the owner).
      The owner is going to click each cited restaurant name and land
      on that restaurant's actual website. If the price you cited
      isn't visibly on that website, they think it's made up. So
      sources in this array must ONLY contain quotes you found on the
      restaurant's own menu page. No third-party platforms in this
      array, ever — not Toast, not DoorDash, none of them.

This split lets you cast a wide net for accurate ranges (the menu
shows badges everywhere) while keeping every clickable cite
trustworthy. If you find 5 prices on platforms and 1 on a restaurant
website, the range uses all 6 prices but the sources array shows
only the 1 verifiable cite. That's correct.

Read this twice: every entry in the SOURCES array must be backed by
a verbatim quote from the restaurant's OWN menu page. No estimates,
no platform quotes, no "typical range" guesses, no averages from
articles. The range itself can include platform prices internally —
just don't surface them as cites.

If you cannot find ANY data (zero direct sites AND zero platforms),
return zero for the item.

For each item below, give me a low-high price range using REAL price
data from restaurants within 5 miles of {location}.

PRIORITY COMPETITORS — these are confirmed restaurants within 5 miles
of {location}. Search THEIR menus FIRST before broadening to open-web
discovery. Use targeted queries like '"Restaurant Name" item price'
against their websites. Each one is a real local business that may
serve the item you're pricing:
  - [up to 25 restaurant names from Google Places API]

When you've exhausted these for an item, broaden with synonyms or
expand to other local restaurants you find via search. The anchor
list is a STARTING POINT, not a hard limit.

Search order (when an anchor list is present):
  1. FIRST: the priority competitors above. Run a targeted search per
     anchor restaurant — `"Restaurant Name" item-name price`. These
     are confirmed local, vetted by Google Places.
  2. THEN: if you need more data, broaden with the generic queries
     below to find restaurants the anchor list missed.

Generic broadening queries:
  For items WITHOUT sizes:  "(item name) (category) price near {location}"
  For items WITH sizes:     "(size) (item name) (category) price near {location}"

IMPORTANT: Only use restaurants within 5 miles of {location}. Do NOT
include restaurants from other states or distant cities.

VERBATIM QUOTES — every entry in the SOURCES array MUST have a
"quote" field with the exact text from the restaurant's menu page
where you saw the price. The quote must contain BOTH:
  (a) the price you're citing (e.g. "$14.99" or "14.99")
  (b) the item name or a clear synonym
If you can't quote verbatim, DO NOT include the source. ONE real
verifiable cite beats ten fabricated ones.

MENU FRESHNESS — only cite menus that look current. Reject menus
dated from prior years, marked "summer 2020 specials" / "winter 2019",
or showing pricing patterns clearly inconsistent with current local
norms. If you can't tell how old a menu is, but the prices look
reasonable for today's market, accept it. The bar is "doesn't look
obviously stale" — not "must be dated this year".

SOURCES ARRAY rules — what counts as a citeable source:

ACCEPT in the sources array:
  - The restaurant's own website menu page (HTML or text-based PDF
    hosted on their domain). The owner must be able to click and
    see the cited price immediately on that page.

REJECT from the sources array (these can still feed the price RANGE
calculation, just not the cited list shown to the owner):
  - Third-party ordering and delivery platforms — Toast, Square,
    ChowNow, Slice, DoorDash, Uber Eats, Grubhub. Use them for the
    range, never cite them.
  - Yelp, TripAdvisor, Google Maps, Zomato, or any review/aggregator
    site. Outdated and unauthoritative — exclude from BOTH the range
    and the sources array.
  - Articles, roundups, blog posts, news pieces, Reddit threads,
    social media posts. Exclude from BOTH range and sources.
  - National chain corporate pricing pages (unless that chain has a
    location within 5 miles AND the local franchise's menu shows the
    price on their own page).
  - Image-only menus you can't extract a verbatim quote from.
  - Scanned/photocopied PDFs whose text extraction yields gibberish.
  - Your own training-data knowledge of typical prices.

Per-size matching: if the source's menu uses different size labels
than ours (e.g. their "Small" vs our "12 Sml"), only cite that
source's price under our size if the SOURCE's size description is
in your quote and is unambiguously the same item size. If a
competitor's size doesn't map cleanly to ours, OMIT THE SOURCE for
that size — don't approximate.

[+ JSON output schema with 5 example sources, synonym list for
common items, and per-cents pricing format spec]
"""


ARCHITECTURE = """\
Two-phase architecture:

PHASE 1 — Discovery (our app):
  When the user uploads their menu, we kick off a Google Places API
  call (`Place Search Nearby`) for the restaurant's address. We get
  back the 20 closest restaurants ranked by Places, with: name,
  address, rating, price level, lat/lng, place_id. We cache these in
  a SQL table keyed by restaurant_id and a JSON file for the editor
  UI to draw map pins. Phase 1 runs SYNCHRONOUSLY — Phase 2 doesn't
  start until Phase 1 has populated the table.

PHASE 2 — Extraction (you):
  We pull the cached competitor list (just the names, capped at 25)
  and inject them into the prompt as the PRIORITY COMPETITORS block.
  Then send the prompt + a batch of 20 menu items to you with
  Google Search grounding enabled. You search, filter, extract,
  return JSON. Empty anchor list -> the block is omitted, you fall
  back to fully open search.

PIPELINE NOTES:
  - Items batched 20 at a time, parallel workers cap=2.
  - Per-batch retry on transient 503/429.
  - Per-item retry pass for items missing from the batched response.
  - Quote validator on our side rejects sources whose quote doesn't
    contain BOTH the cited price AND a recognizable form of the item
    name (with a synonym map for common cross-naming).
  - 3-source minimum was REMOVED after our previous conversation.
    Floor is 1 — we accept single-source items as success.
  - Items with zero data points (across both pools) are skipped
    silently; the UI shows them with no badge.
"""


# ----------------------------------------------------------------------
# CONVERSATION
# ----------------------------------------------------------------------

# Turn 1 — set the stage
send(
    "Hey, third round. Big update since we last talked. We took your "
    "feedback and made some real architectural changes — I want to walk "
    "you through everything as it stands now and have you stress-test "
    "it before we run it for real.\n\n"
    "Quick recap of the decisions we've made:\n\n"
    "1. Dropped the 3-source minimum entirely. Floor is now 1.\n"
    "2. Adopted your two-phase architecture: we run Google Places API "
    "first to get a clean list of confirmed local competitors, then "
    "feed those names to you as a PRIORITY COMPETITORS block in the "
    "prompt. You search those FIRST, then broaden if needed.\n"
    "3. Adopted a TWO-POOL model for sources: the price range gets "
    "computed from BOTH restaurant websites AND third-party platforms "
    "(Toast, Slice, DoorDash, Grubhub, UberEats, ChowNow). But the "
    "clickable SOURCES array shown to the owner only includes quotes "
    "from the restaurant's own website. Reasoning: the owner clicks "
    "the cited link and lands on that restaurant's site; if the cited "
    "price isn't visibly there, trust breaks.\n"
    "4. Added a MENU FRESHNESS rule (reject menus marked summer 2020 "
    "etc.).\n"
    "5. Tightened synonym lists, dropped 'Sandwich' from sub synonyms.\n\n"
    "Want me to send you the full final prompt and the architecture "
    "summary so you can review? Heads up: I want you to be brutal "
    "about anything that looks contradictory, fragile, or naive."
)

# Turn 2 — show the architecture and prompt
send(
    "Here's the architecture as it'll actually run:\n\n"
    "----- ARCHITECTURE -----\n"
    f"{ARCHITECTURE}\n"
    "----- END ARCHITECTURE -----\n\n"
    "And here's the final prompt with placeholders for {location} and "
    "the anchor list:\n\n"
    "----- PROMPT -----\n"
    f"{FINAL_PROMPT}\n"
    "----- END PROMPT -----\n\n"
    "Read it as the model that has to act on it. Three things I want "
    "from you:\n"
    "  (a) Any contradictions or things that fight each other.\n"
    "  (b) Anything that looks like it'd be hard to interpret in edge "
    "      cases (e.g. an item that's on a platform but not on any "
    "      direct site — does the prompt give you clear guidance?).\n"
    "  (c) Anything we DIDN'T address that you think will bite us "
    "      when we test it for real.\n\n"
    "Don't be polite. If something is still wrong, say so."
)

# Turn 3 — push on the trickiest scenario
send(
    "Walk me through a specific scenario. Say I send you 'Combination "
    "Pizza' for the Agawam, MA pizzeria, with the priority list "
    "containing the actual 25 nearest restaurants. Tell me, "
    "step-by-step, what you'd do — and tell me what the FINAL JSON "
    "for that item would look like, including the populated sources "
    "array.\n\n"
    "I'm specifically interested in: how does the new flow handle the "
    "case where you find Combination Pizza on Athena Pizza's DoorDash "
    "page (so it counts for the range) but Athena Pizza's own website "
    "isn't accessible / doesn't list it / is image-only? Do you cite "
    "Athena Pizza in the sources array, or not? Make sure your answer "
    "is concrete enough that I can verify it against the prompt."
)

# Turn 4 — the failure modes Gemini flagged before
send(
    "Two things you raised in our previous chats I want to revisit:\n\n"
    "(1) You said hard rules create perverse incentives to lower "
    "    quality. We dropped the 3-rule and replaced it with a 1-floor "
    "    plus the two-pool split. Do you still see ANY rule in this "
    "    prompt that could push you toward lowering the quality bar "
    "    to make a result look better than it is? Pick at it.\n\n"
    "(2) You walked me through 'helpful disobedience' last time — "
    "    where you violated my hard rule because following it would "
    "    have been useless. Does THIS prompt have any spots where "
    "    you'd be tempted to do the same? If yes, where, and what "
    "    would the disobedience look like?"
)

# Turn 5 — what's missing
send(
    "Last big question. The places where the prompt feels weakest "
    "to me are around how the menu items are actually NAMED. Like, "
    "if my item is 'Mediterranean Pizza' and a competitor lists "
    "'Greek Pizza' or 'Mediterranean (Feta, Olives, Spinach)', is "
    "the prompt currently giving you enough to handle the matching, "
    "or are you going to drop those because the names don't match "
    "exactly? What about partial-name overlap (e.g. our 'Bacon Cheese "
    "Burger' vs their 'Bacon & Cheese' or 'Bacon Burger'). Walk me "
    "through how the current prompt handles these edge cases and "
    "tell me where you'd improve it."
)

# Turn 6 — the wrap-up: green light or red flags
send(
    "OK, last turn. Bottom line:\n\n"
    "  (a) Are we ready to test? Yes / No / Yes-but.\n"
    "  (b) If yes-but, what's the ONE most important fix to make "
    "      before the first real run?\n"
    "  (c) What metric should I watch on the first test run that "
    "      will tell me whether the new architecture is actually "
    "      working — not just whether items have sources, but a real "
    "      tell that the design itself landed?\n\n"
    "Be concise."
)


# Save transcript
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_critique_3_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
