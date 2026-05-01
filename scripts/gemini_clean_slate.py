"""Seventh Gemini conversation: clean-slate question.

After six iterations of architecture + prompt design, we have a system
that works decently but keeps surfacing soft-followed-rule failures.
Ask Gemini — given everything we've tried and why — is there a
fundamentally better way to do this that we haven't considered? Don't
lead the witness; want a candid 'if I were starting from scratch'
answer.
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
# The full story.
# ----------------------------------------------------------------------

send(
    "Hey, I want your honest read on whether we're solving this the "
    "right way. I'm going to lay out everything we've tried, how we "
    "got here, what's still wrong, and ask you to tell me — if you "
    "were starting from scratch with this problem, would you build it "
    "this way, or would you take a fundamentally different approach? "
    "Be candid. I'm not invested in the current architecture, I'm "
    "invested in solving the problem.\n\n"
    "THE PRODUCT:\n"
    "MenuFlow. Restaurant owners (mostly small independent places) "
    "upload their menu PDF. We extract their items + prices via OCR. "
    "Then for each item, we tell them what nearby competitors charge "
    "for the same thing — with verifiable source links they can "
    "click. The pitch: 'price your menu with real local data, not "
    "guesses'. They pay $80/month. Gross margin is the constraint — "
    "we can't burn $50 of API per pricing run.\n\n"
    "WHAT WE TRIED, IN ORDER:\n\n"
    "ITERATION 1 — Naive grounded search.\n"
    "Just sent Gemini Pro a prompt: 'find competitor prices for these "
    "items near {address}, return JSON with low/high/median per "
    "item'. Result: thin source counts, frequent hallucinations, "
    "competitors from wrong cities, prices fabricated when no real "
    "data existed. Trust-breaking.\n\n"
    "ITERATION 2 — Verbatim quote requirement.\n"
    "Required every cite to include the verbatim text from the menu "
    "page. Helped reduce fabrication. But created a different "
    "problem: small towns with image-based menus / scanned PDFs "
    "couldn't yield ANY citeable sources, leading to many blank "
    "items.\n\n"
    "ITERATION 3 — Hard 3-source minimum.\n"
    "Said: every item MUST have at least 3 cites. Otherwise drop the "
    "item entirely. We thought this would force you to search harder. "
    "Instead, you started returning items with 1-2 sources anyway — "
    "you told us in a later conversation that you 'helpfully "
    "disobeyed' our rule because following it meant throwing away "
    "real data. We were getting silently broken outputs.\n\n"
    "ITERATION 4 — Lower the floor to 1 + add verifiability framing.\n"
    "Dropped the floor. Single-source items now count as success. The "
    "framing shifted from 'we need a range' to 'every cite must be "
    "clickable and verifiable on the restaurant's own website'. "
    "Improvement, but still leaves the customer feeling thin: '1 src' "
    "doesn't feel like meaningful intelligence on a $80/mo product.\n\n"
    "ITERATION 5 — Two-pool source model.\n"
    "You can use third-party platforms (Toast/DoorDash/Slice/etc.) to "
    "compute the price RANGE, but the cited sources array (clickable "
    "for the customer) must only contain quotes from the restaurant's "
    "own website. This widens the data backing the range without "
    "exposing un-verifiable URLs to the customer.\n\n"
    "ITERATION 6 — Two-phase architecture.\n"
    "Phase 1 (our app, Google Places API): get the 25 closest "
    "confirmed competitors with name + address. Phase 2 (you, "
    "grounded search): get those names + addresses as a PRIORITY "
    "COMPETITORS anchor block. Search those FIRST, then broaden if "
    "needed. Address inclusion was YOUR critical fix recommendation — "
    "before that, 'Joe's Pizza' might match the wrong Joe's Pizza in "
    "another city. With Name+Address, on-anchor citation rate moved "
    "from 49% to 88%.\n\n"
    "ITERATION 7 — Programmatic backstops.\n"
    "We've finally accepted that prompt rules get soft-followed. Now "
    "we have hardcoded post-processing checks: forbidden-source-name "
    "list (kills DoorDash/Yelp/etc. as cited names), compound-size "
    "rejection (drops cites where the quote says 'Med/Large' when our "
    "customer has 'Medium'), price plausibility ceiling.\n\n"
    "WHERE WE LANDED, MEASURED:\n"
    "  - 88% on-anchor citation rate\n"
    "  - 2.4x multiplier between range pool and cited pool\n"
    "  - 66/158 items got data on test run; 92 got nothing\n"
    "  - Gemini still soft-follows per-size matching rules — we just\n"
    "    caught Nicky's Pizza Med/Large ($21) and Large ($26) being\n"
    "    cited under our customer's Medium ($13.95). Wrong sizes, big\n"
    "    price gap, would have made the customer think they're\n"
    "    drastically underpriced when they're actually fine.\n\n"
    "WHAT YOU TOLD US LAST CONVERSATION:\n"
    "When I asked you what's actually enforceable on our side vs "
    "what we have to trust the prompt for, you said: 'the prompt is "
    "for steering, not guarantees. Your application code must be the "
    "final arbiter of correctness'. Then you recommended Option D — "
    "shift the LLM job to PURE structured data extraction (parse the "
    "menu, return a JSON array of all sizes/prices), and do the "
    "MATCHING in our application code with deterministic logic.\n\n"
    "OK that's the whole picture. I'm not necessarily looking to "
    "tear it all down — but I have a feeling there might be an angle "
    "we haven't considered, or an API/service/data-source we don't "
    "know exists. You've worked through the problem with us in great "
    "depth at this point. So three open questions:\n\n"
    "  (1) Looking at the actual problem we're solving — surfacing\n"
    "      verifiable competitor pricing for restaurant menus —\n"
    "      what angles or approaches are we NOT considering? Could be\n"
    "      anything: APIs we don't know about, data sources, ways to\n"
    "      slice the problem differently, products that already do\n"
    "      part of this we could integrate with, hybrid approaches we\n"
    "      haven't thought of.\n\n"
    "  (2) Of the things we ARE doing, which is the weakest link in\n"
    "      the chain? Where would a small change pay off the most?\n\n"
    "  (3) Anything in the way I described the problem that suggests\n"
    "      we've been solving the wrong shape of problem? Like, are\n"
    "      we framing this as 'price extraction' when it should be\n"
    "      something else (price comparison, market-tier signaling,\n"
    "      something more abstract)?\n\n"
    "Tell me what you actually think. I'm looking for the thing I'm "
    "missing that's obvious to you."
)

# Turn 2 — go deeper on whatever it surfaced
send(
    "Walk me through the most concrete one of those — the angle or "
    "API or different framing that you think would actually move the "
    "needle for us. Specific enough that I can evaluate whether it "
    "fits. What would I actually build / integrate / change? And "
    "what's the realistic downside?"
)

# Turn 3 — what's left unsaid
send(
    "Last question. Six conversations in, you've gotten pretty good "
    "at predicting what I'll ask. Is there anything you've held back "
    "because it didn't seem to fit the question I was asking — "
    "something you noticed but didn't volunteer? If yes, what?"
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_clean_slate_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
