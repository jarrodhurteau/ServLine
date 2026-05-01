"""Why are our market ranges noisy even AFTER we grouped items by
(category, size, customer_price) and ran ONE Gemini grounded search
per cluster?

Real evidence from a fresh run on Pasquale's Pizza (Agawam, MA):

  Specialty Pizzas, all 12" Sml, all priced at $17.95 by the customer
    Combination:  range $13.99-$19.95   Fair Range
    Mexican:      range $11.00-$14.00   Above Market
    Hawaiian:     range $18.00 (1 src)  Below Market
    Pollo Chicken:range $12.00-$15.00   Above Market
    Veggie:       range $15.99 (1 src)  Above Market

  Calzones, all "Small", all priced at $14.75 by the customer
    Buffalo Chicken Bacon Ranch:  $16.00 (1 src)   Below Market
    Chicken Bacon Ranch:          $7.50-$10.00     Above Market
    Chicken Pesto:                $12.99-$15.99    Fair Range
    Veggie Calzone:               $15.00-$25.29    Below Market

These are SAME category, SAME size variant, SAME customer price.
We're now sending ONE Gemini call per (cat, size, price) cluster
asking: "What's the typical range for Small Calzones priced near
$14.75 at restaurants near Agawam, MA?"

Yet under the OLD (cat, size) grouping (one call per (Calzones, Small)),
the system was producing per-item-looking variance like above. So
either:
  (a) The old code wasn't actually batching per group (a bug)
  (b) Gemini's grounded-search returns are so non-deterministic that
      the same prompt yields wildly different ranges across calls
  (c) The "per-item search keeps sources" path is overwriting the
      batch range somewhere downstream
  (d) Something about per-item Haiku estimates is contaminating the
      final stored range even after we override with the batch number

We've spent ~2 weeks iterating on prompt + parser + grouping. This
isn't a prompt-engineering question anymore — it's a question about
whether Gemini grounded search is the right TOOL for this job at all.
"""
import os, sys
from datetime import datetime
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except ImportError: pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception: pass

from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "").strip())
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
    "I'm building MenuFlow — a competitor-pricing tool for restaurant "
    "owners. We use you (Gemini grounded search) as the pricing engine. "
    "We've iterated for 2 weeks on prompt + parser. We're now hitting "
    "a fundamental noise problem and I want your honest diagnosis "
    "before I commit more time to this approach.\n\n"
    "ARCHITECTURE (current):\n"
    "  Phase 1: Google Places nearby search → ~50 anchor restaurants\n"
    "  Phase 2: Per-item Gemini grounded search for each menu item, with\n"
    "           anchors injected into the prompt. Returns range + cited\n"
    "           sources.\n"
    "  Phase 3 (new): cluster items by (category, size_label, customer_\n"
    "           price). For each cluster, ONE Gemini grounded search asks\n"
    "           'typical range for {size} {category} priced near ${price}\n"
    "           in {location}'. Cluster range REPLACES the per-item range\n"
    "           but per-item source citations stay (the 'cooler').\n\n"
    "EVIDENCE OF THE PROBLEM (Pasquale's Pizza, Agawam MA):\n\n"
    "  Specialty pizzas, ALL 12\" Sml, ALL priced $17.95 on the source\n"
    "  menu — these are EXACT same category, EXACT same variant size,\n"
    "  EXACT same customer price:\n"
    "    Combination:   range $13.99-$19.95   pill: Fair Range\n"
    "    Mexican:       range $11.00-$14.00   pill: Above Market\n"
    "    Hawaiian:      range $18.00 (1 src)  pill: Below Market\n"
    "    Pollo Chicken: range $12.00-$15.00   pill: Above Market\n"
    "    Veggie:        range $15.99 (1 src)  pill: Above Market\n\n"
    "  Calzones, ALL 'Small', ALL priced $14.75:\n"
    "    Buffalo Chicken Bacon Ranch: $16.00 (1 src)  Below Market\n"
    "    Chicken Bacon Ranch:         $7.50-$10.00    Above Market\n"
    "    Chicken Pesto:               $12.99-$15.99   Fair Range\n"
    "    Veggie:                      $15.00-$25.29   Below Market\n\n"
    "Each row is supposed to share a cluster range with the others "
    "above/below it. They obviously aren't sharing. The user looks at "
    "this and says (rightly): 'how can one calzone be Above Market, "
    "another Below Market, another Fair, when they're all the same "
    "price?'\n\n"
    "Three honest diagnoses I want from you:\n\n"
    "  (1) Given the architecture above, where is the most likely\n"
    "      failure point? Is it (a) the cluster grouping never actually\n"
    "      ran or got bypassed, (b) the per-item search ran AFTER the\n"
    "      cluster override and clobbered it, (c) the per-item-looking\n"
    "      variance you see above is actually you (Gemini) returning\n"
    "      different ranges for slightly different prompts even when I\n"
    "      think I'm asking the 'same' thing, or (d) something else?\n\n"
    "  (2) When you receive a grounded-search prompt like 'what's the\n"
    "      typical range for Small Calzones priced near $14.75 at\n"
    "      restaurants near Agawam, MA' — how stable is your output\n"
    "      across calls? If I called you 5 times with that exact prompt\n"
    "      back-to-back, would I get 5 similar ranges or 5 wildly\n"
    "      different ones? Be honest about non-determinism in grounded\n"
    "      search.\n\n"
    "  (3) Step back. Is grounded search the wrong tool for this job?\n"
    "      The customer wants 'what does a Small Calzone go for around\n"
    "      here' — a stable, reproducible answer. Grounded search\n"
    "      synthesizes a different answer each time. Should we be\n"
    "      pre-fetching competitor menus into a database (scrape +\n"
    "      cache) and then querying that database deterministically,\n"
    "      rather than asking you for a synthesized range every time?\n\n"
    "Don't hedge. Tell me the truth even if the answer is 'this approach\n"
    "is structurally noisy and will never give consistent ranges.'"
)

send(
    "Drill into question 3. If we shift to 'scrape competitor menus into a\n"
    "database' as the architecture:\n\n"
    "  - For a 1-person startup with no funding, what's the cheapest\n"
    "    realistic implementation? Apify actors? Custom Playwright? Per-\n"
    "    domain scrapers? How many sites do we realistically need to\n"
    "    cover for a single market (e.g., Agawam, MA + 10mi radius) to\n"
    "    have stable data?\n\n"
    "  - How do we handle the FRESHNESS problem? Restaurant menus change.\n"
    "    A scraped menu from 6 months ago is wrong. Do we re-scrape\n"
    "    weekly? Monthly? Trigger on user-reports-of-stale-data?\n\n"
    "  - What's the minimum viable schema we need to store? Just\n"
    "    (restaurant, item_name, price, scraped_at)? Or do we need\n"
    "    canonical item categorization too (so 'Cheese Pizza' from\n"
    "    Restaurant A matches 'Plain Pizza' from Restaurant B)?\n\n"
    "  - Realistic time-to-build for a single dev: 2 weeks? 2 months?\n"
    "    What are the realistic landmines (anti-bot, CAPTCHAs, dynamic\n"
    "    JS menus, image-only menus, PDF menus, etc.)?"
)

send(
    "Last question — be brutal. Given everything you said: should I\n"
    "abandon Gemini grounded search as the pricing engine entirely and\n"
    "build a small competitor-menu scraper / cache instead? Or is there\n"
    "a hybrid (use grounded search ONLY when I have NO scraped data,\n"
    "and accept the noise as the 'cold start' cost)?\n\n"
    "Frame this as: 'if you were me, here's what you'd do this week.'"
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = REPO / "storage" / "logs" / f"gemini_range_noise_{ts}.txt"
out.parent.mkdir(exist_ok=True)
out.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n[saved -> {out}]")
