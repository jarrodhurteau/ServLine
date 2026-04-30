"""Sixth critique session: actual Nicky's menu surfaces a size-mismatch
problem Gemini's prior diagnosis missed.

In round 5, Gemini said the duplicate Nicky's cites ($21 + $26) were
"two different items at Nicky's that both plausibly matched Medium
Pizza" and recommended aggregating them as a $21-$26 range.

Then we pulled up Nicky's actual menu. They list FIVE sizes for
Cheese Pizza:
  Small      $14
  Small/Med  $17
  Med/Large  $21
  Large      $26
  Party      $29

So the duplicates are different SIZES, not different items. Gemini
mapped Med/Large and Large onto our "Medium" variant ($13.95
customer price), giving a wildly wrong comparison. The actual
Medium-equivalent at Nicky's is Small ($14).

Gemini's per-size matching rule in our prompt explicitly says
"If a competitor's size doesn't map cleanly to ours, OMIT THE
SOURCE for that size — don't approximate." That rule was
soft-followed.

Want Gemini's honest read on what happened and how to fix it.
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
    "Hey, follow-up to a conversation we had earlier today. Quick "
    "context: I'm building MenuFlow, a tool that prices restaurant "
    "menus against local competitors. You're the pricing engine via "
    "grounded search. We've designed a two-phase architecture (Google "
    "Places anchor list + your grounded search) and a two-pool source "
    "model (range can use Toast/DoorDash; cited sources are direct-"
    "website only). All your prior recommendations have been "
    "implemented.\n\n"
    "An hour ago you helped me diagnose a UI issue where the same "
    "restaurant appeared twice in the cited sources for the same "
    "menu variant:\n\n"
    "    Nicky's Pizza:    $21.00\n"
    "    Nicky's Pizza:    $26.00\n"
    "    Sorrento:         $23.95\n\n"
    "...all cited under the customer's 'Medium' pizza variant priced "
    "at $13.95.\n\n"
    "Your diagnosis at the time: 'I am correctly extracting two "
    "distinct, real prices from Nicky's menu that both match the "
    "search query. Most likely two different ITEMS at Nicky's that "
    "both plausibly match Medium Pizza — like Medium Cheese $21 and "
    "Medium Special $26.' You strongly objected to me deduplicating "
    "in the parser, calling it 'dangerous data destruction', and "
    "recommended aggregating same-restaurant cites as a range "
    "('Nicky's: $21-$26' on a single line).\n\n"
    "I pushed it to my collaborator. They went and pulled up Nicky's "
    "actual menu page. New information changes the picture:\n\n"
    "Nicky's Cheese Pizza, from their actual website, lists FIVE\n"
    "sizes:\n"
    "    Small      — $14.00\n"
    "    Small/Med  — $17.00\n"
    "    Med/Large  — $21.00\n"
    "    Large      — $26.00\n"
    "    Party      — $29.00\n\n"
    "So your $21 and $26 cites aren't different items — they're "
    "different SIZES of the same Cheese Pizza. Specifically, "
    "Med/Large and Large.\n\n"
    "And the customer's variant is 'Medium' priced at $13.95. The "
    "actual Medium-equivalent at Nicky's by size+price is Small "
    "($14), maybe Small/Med ($17). Med/Large and Large are LARGER "
    "than the customer's Medium, which is why they all came in $7-13 "
    "above the customer's $13.95 price.\n\n"
    "Three questions:\n\n"
    "  (1) The prompt explicitly says: 'Per-size matching: if the "
    "      source's menu uses different size labels than ours (e.g.\n"
    "      their Small vs our 12 Sml), only cite that source's price "
    "      under our size if the SOURCE's size description is in "
    "      your quote and is unambiguously the same item size. If a "
    "      competitor's size doesn't map cleanly to ours, OMIT THE "
    "      SOURCE for that size — don't approximate.'\n"
    "      \n"
    "      Why did you cite Nicky's Med/Large and Large under our "
    "      Medium? What part of the rule fell down? Be honest — was\n"
    "      it ignored, soft-followed, or did the input data make it\n"
    "      hard to apply?\n\n"
    "  (2) Your aggregate-as-range fix from earlier ('Nicky's: $21-"
    "      $26') would NOT solve this case. Aggregating Med/Large +\n"
    "      Large into a single Nicky's range still misrepresents\n"
    "      the comparison. The customer would compare their $13.95\n"
    "      Medium against 'Nicky's $21-$26' and conclude they're way\n"
    "      underpriced when they're actually pricing a different\n"
    "      (smaller) size correctly. Acknowledge this and tell me\n"
    "      what the correct fix actually is.\n\n"
    "  (3) Walk me through what would have happened if I'd given you "
    "      Nicky's full size breakdown as part of the input — would\n"
    "      you have done better? Where in the pipeline does the\n"
    "      size-classification fail?"
)

send(
    "OK, given everything we now understand, I want to lock in the "
    "actual fix. My collaborator and I are looking at three options:\n\n"
    "  (A) TIGHTEN THE PROMPT — restate per-size matching with "
    "      concrete examples that mirror this Nicky's case. Spell\n"
    "      out: 'If competitor has Med/Large at $21 and your item is\n"
    "      a 12-inch Medium, those don't match — Med/Large is\n"
    "      larger. Omit it. Cite only the size that's actually\n"
    "      yours, even if that means citing nothing for this\n"
    "      competitor.' Risk: still soft-followed.\n\n"
    "  (B) SURFACE THE QUOTE in the customer-facing popover. Today\n"
    "      we show 'Nicky's Pizza: $21.00'. We capture the verbatim\n"
    "      quote (which contains 'Med/Large $21') but throw it away\n"
    "      at render time. If we surfaced it as 'Nicky's Pizza —\n"
    "      Med/Large $21', the customer would immediately spot the\n"
    "      mismatch and discount it themselves. Let them judge.\n\n"
    "  (C) BOTH — tighten the prompt AND surface the quote so the\n"
    "      rules are stricter AND the customer has the context to\n"
    "      catch what slips through.\n\n"
    "Two questions:\n\n"
    "  (1) Which is the right approach in your view, and why?\n\n"
    "  (2) Are there other fixes I'm not considering? Like — could "
    "      we have you return the SIZE LABEL as a structured field\n"
    "      separate from the quote, so we could programmatically\n"
    "      validate the match instead of relying on prompt rules\n"
    "      OR human eyeballs?"
)

send(
    "Last thing. Now that we've gone five rounds of design + one real "
    "test, the pattern is becoming clear: every time we deploy a "
    "rule, you 'soft-follow' it under pressure when the data is "
    "messy. The 3-source minimum, the per-size matching, the strict "
    "REJECT list — every rule has been bent in some real-world "
    "scenario.\n\n"
    "I'm not blaming you — this is how LLMs work, and the fact that "
    "you flag this honestly is good. But it means I can't trust the "
    "prompt to be the FINAL line of defense. What's actually "
    "enforceable on my side, after you return the JSON, that I can "
    "use as a backstop?\n\n"
    "Concretely: what programmatic checks should I add in our "
    "post-processing layer that would catch the bad data BEFORE it "
    "reaches the customer? Not stylistic things — real correctness "
    "checks. List the top 3 with the failure mode each one catches. "
    "Be concise."
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_critique_6_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
