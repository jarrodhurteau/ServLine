"""Eighth Gemini conversation: push hard on the data acquisition problem.

In round 7, Gemini proposed a Menu Data Warehouse architecture as the
fix for our fragile real-time pricing pipeline. Sounds great in
theory — but we've spent six iterations discovering that menu data
is largely INACCESSIBLE: image-only menus, scanned PDFs, JS-heavy
ordering platforms, login walls, restaurants without websites at all.

If Gemini can't reliably extract this data via grounded search today,
how is a background scraper going to do it any better? Push hard on
specifics. No hand-waving.
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


send(
    "Hey, follow-up to the data-warehouse architecture you proposed "
    "earlier. I want to challenge what feels like the biggest hand-"
    "wave in your proposal. Quick context if you don't have it: I'm "
    "building MenuFlow, a tool that compares small-restaurant menu "
    "prices to local competitors. Across seven prior conversations "
    "with you, the consistent failure mode has been: we can't "
    "actually reliably get the data. Not because our prompts are bad, "
    "but because the source data on the open web is structurally "
    "inaccessible.\n\n"
    "Concrete examples of what we hit on a typical small-town menu "
    "scrape:\n\n"
    "  - Restaurant has no website at all (older mom-and-pop places).\n"
    "  - Has a website but the menu is a single JPG/PNG image. You\n"
    "    can't extract verbatim quotes from an image.\n"
    "  - Has a 'menu' link that's a scanned PDF — the text extraction\n"
    "    yields gibberish OCR.\n"
    "  - Menu only exists on Toast/ChowNow/Slice/DoorDash — but\n"
    "    those platforms hide prices behind 'start order' flows,\n"
    "    location pickers, and sign-in walls. Even when accessible,\n"
    "    the page is JS-rendered and search-grounded crawlers often\n"
    "    can't read the dynamic content.\n"
    "  - Menu is on Facebook in a photo album. Same image-only\n"
    "    problem.\n"
    "  - Website lists items but no prices ('Call for pricing!').\n\n"
    "On our last test run, 92 out of 158 customer menu items got "
    "ZERO competitor data. Not because we didn't try — because the "
    "data simply wasn't accessible.\n\n"
    "Now you're proposing we build a data warehouse and pre-scrape "
    "every restaurant in a market. The proposal assumes we CAN scrape "
    "them. That's the part we've been failing at.\n\n"
    "So my questions are:\n\n"
    "  (1) Be honest — when you proposed the data warehouse, did you\n"
    "      assume we'd magically have access to data we currently\n"
    "      don't, or did you have specific data acquisition\n"
    "      strategies in mind? If yes, what?\n\n"
    "  (2) What ACTUAL data sources / APIs / paid services exist for\n"
    "      structured restaurant menu data? I know about: Single\n"
    "      Platform (mostly chains), Yelp Fusion (limited menu data,\n"
    "      paid), MenuPages (defunct), various menu aggregators that\n"
    "      are themselves scraping the same locked sources we hit.\n"
    "      Any I'm missing? Are there business / legal partners I\n"
    "      should be talking to?\n\n"
    "  (3) For the 60-70%% of small restaurants whose data isn't\n"
    "      machine-readable anywhere, what's the actual play? OCR?\n"
    "      Headless browser with vision-model screenshots? Manual\n"
    "      data entry? Crowdsourcing? POS integrations? Just\n"
    "      accepting we won't have data for them?\n\n"
    "Don't tell me what's theoretically possible. Tell me what would "
    "actually work given the constraints. Specifics."
)

# Turn 2 — go deeper on whichever path it surfaces as most viable
send(
    "Of the data-acquisition strategies you just listed, which ONE "
    "do you think a 1-person team should focus on first? Be opinionated. "
    "Walk through what I'd actually build, what infrastructure I'd "
    "need, what it costs, and what coverage rate I should realistically "
    "expect for small-town restaurants in the US northeast."
)

# Turn 3 — vision-model screenshots, since that seems likely to be
# the real answer
send(
    "Let's stress-test the most LLM-native option: headless browser "
    "(Playwright or Puppeteer) renders the restaurant's menu page, "
    "we screenshot it, send the screenshot to Gemini Pro Vision for "
    "structured extraction. Does that actually solve the lock-out "
    "problem? Specifically:\n\n"
    "  (a) Does Pro Vision handle JS-heavy pages well once they're\n"
    "      fully rendered as images? Or is it weaker on text\n"
    "      extraction from screenshots vs native PDFs?\n"
    "  (b) Does it work on Toast/DoorDash/Grubhub menu pages where\n"
    "      the prices ARE visible to a logged-out user but require\n"
    "      JS to render? Realistic success rate?\n"
    "  (c) Image-only menu JPGs (the small mom-and-pop case) — does\n"
    "      Pro Vision actually read those reliably, or does the\n"
    "      same OCR-quality issue come back?\n"
    "  (d) Cost per menu page. Roughly.\n\n"
    "If the answer is 'yes this works for ~80%% of cases at a "
    "reasonable cost', that fundamentally changes the conversation."
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_data_acquisition_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
