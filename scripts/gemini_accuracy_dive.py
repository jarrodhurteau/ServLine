"""Specific accuracy diagnostic with concrete failures from the rerun.

Three patterns of failure showing up in the editor right now:
  1. Specialty pizzas (Meat Lovers, Combination, 4 Cheese) are getting
     ranges that look like Cheese Pizza prices — premium versions
     priced as base versions.
  2. Premium burger variants (Double Burger) showing ranges LOWER than
     the regular burger, which is logically impossible.
  3. Wings quantity drift — 30 Pcs Wings showing range $17-$21 (real
     answer: ~$30-40 for 30 wings).

User's instinct: MenuSpy has this figured out somehow. We don't.
Want Gemini to diagnose mechanically what's failing AND look at how
the public competitors handle it.
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
    "Quick context: I'm building MenuFlow, a tool that benchmarks small "
    "restaurant menu prices against local competitors. You're the "
    "pricing engine via grounded search. We've spent 9 conversations "
    "iterating on prompts, two-pool source models, anchor lists, "
    "programmatic backstops. We have a real competitor (MenuSpy at "
    "$39-79/mo) shipping with the same approach.\n\n"
    "I just ran our latest version against an Agawam, MA pizzeria's "
    "menu. The cite count is OK, but the ACCURACY of the ranges is "
    "embarrassing. Three patterns of failure:\n\n"
    "PATTERN 1: SPECIALTY PIZZAS PRICED AS PLAIN CHEESE\n"
    "  Customer's Cheese Pizza, 12 inch: range $11.00-$19.25  ← OK\n"
    "  Customer's Meat Lovers, 12 inch:  range $12.00-$15.00  ← LOWER than Cheese!\n"
    "  Customer's Combination, 12 inch:  range $11.00-$14.00  ← LOWER than Cheese!\n"
    "  Customer's 4 Cheese, 12 inch:     range $13.55-$17.99  ← essentially Cheese prices\n\n"
    "Reality: a Meat Lovers pizza (pepperoni, sausage, bacon, ham,\n"
    "hamburger) should be ~$5 MORE than plain cheese, not equal-to-or-\n"
    "less. The cited prices are clearly cheese pizza data being\n"
    "applied to specialty-pizza items.\n\n"
    "PATTERN 2: PREMIUM BURGERS PRICED AS BASE BURGERS\n"
    "  Customer's Burger:        range $11.70-$15.60   ← OK\n"
    "  Customer's Double Burger: range $9.00-$11.00    ← LOWER than single!\n\n"
    "A Double Burger has TWO patties — has to cost more than a single\n"
    "patty Burger. The Double Burger range is somehow citing single-\n"
    "burger prices.\n\n"
    "PATTERN 3: WINGS QUANTITY DRIFT\n"
    "  Customer's 10 Pcs Wings:  range $18.99-$19.50   ← OK\n"
    "  Customer's 20 Pcs Wings:  range $12.00-$15.00   ← that's 6-piece pricing\n"
    "  Customer's 30 Pcs Wings:  range $17.00-$21.00   ← that's 10-piece pricing\n"
    "  Customer's 50 Pcs Wings:  range $27.00-$33.00   ← that's 20-piece pricing\n\n"
    "Each larger-quantity variant is being priced from a smaller "
    "quantity's market. We added a quantity-mismatch parser backstop "
    "but it only fires when the quote contains a piece count token. If "
    "the source quote just says \"Wings $13.99\" without specifying how "
    "many wings, the backstop has nothing to compare and the cite is "
    "accepted.\n\n"
    "The unifying root cause: you're treating SPECIALTY/QUANTITY "
    "VARIANTS as if they're the same item as the base. Cheese pizza "
    "prices flow into Meat Lovers. Single Burger prices flow into "
    "Double Burger. Small Wings prices flow into Large Wings. We've "
    "tried prompt-side rules and parser-side backstops; neither has "
    "stuck.\n\n"
    "Three questions:\n\n"
    "  (1) Diagnose. What's actually happening on your end that lets\n"
    "      Cheese Pizza data leak into Meat Lovers' range? Walk me\n"
    "      through the search → matching → citation steps where the\n"
    "      conflation happens.\n\n"
    "  (2) MenuSpy ($39-79/mo, claims 'dish by dish' competitor "
    "      pricing) ships this product successfully. Their methodology\n"
    "      page admits 'estimated or partial data when public source\n"
    "      unavailable.' What do you think they do that we don't to\n"
    "      keep specialty/quantity variants distinct from base items?\n\n"
    "  (3) Concrete fix recommendation. Not 'tighten the prompt' — at\n"
    "      this point we've tightened the prompt 11 times. What's the\n"
    "      ARCHITECTURAL fix that prevents this class of error?"
)

send(
    "Stay with question 3. You said in our previous conversations that "
    "the ARCHITECTURAL fix is 'extract structured data, do matching in "
    "code' (Option D). But for the specialty-vs-base-item conflation, "
    "extraction-then-code-matching has a different problem: even with "
    "perfect structured extraction (Cheese Pizza $X, Meat Lovers $Y "
    "from each competitor), our app code has to KNOW that Meat Lovers "
    "is a different item from Cheese Pizza when matching the customer's "
    "Meat Lovers item to competitor data.\n\n"
    "How does that mapping work in practice? Is this where embeddings "
    "come in (semantic similarity on item names + descriptions)? Or "
    "do we need a domain-specific item ontology (Cheese Pizza is "
    "category=pizza, type=plain; Meat Lovers is category=pizza, "
    "type=meat-lovers)? Walk me through the data structure that would "
    "make 'find competitor's Meat Lovers' a deterministic operation."
)

send(
    "OK so the real fix requires per-item canonical IDs and a competitor "
    "menu warehouse to query against — not realtime grounded search. "
    "Got it. But that's weeks of architecture work and I have a "
    "shippable-but-imperfect product TODAY.\n\n"
    "What's the BEST short-term mitigation that doesn't require building "
    "the warehouse? Specifically: what's the highest-impact change I "
    "can make in the existing prompt + parser pipeline that would "
    "noticeably reduce the specialty/quantity conflation rate? Not a "
    "full fix — a meaningful improvement.\n\n"
    "Be concrete. If it's a prompt change, write the literal sentences. "
    "If it's a parser rule, describe the rule precisely."
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = REPO / "storage" / "logs"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / f"gemini_accuracy_dive_{ts}.txt"
out_path.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n\n[saved transcript -> {out_path}]")
