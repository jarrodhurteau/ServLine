"""Specific factual research: what APIs / services / data sources exist
that would give us ACCURATE restaurant menu pricing data, instead of us
trying to extract it from grounded search?

We've spent a week building a grounded-search pipeline and the accuracy
is still wrong (specialty pizzas priced as cheese, double burgers
priced as singles, 30-piece wings priced as 6-piece). The cite-
filtering rules we keep adding don't fix the underlying problem:
Gemini's grounded search just isn't precise enough for variant-level
pricing.

User's question: is there an API we could BUY or INTEGRATE with that
would give us this data accurately? Including potentially MenuSpy's
own API (could we white-label or partner?). Or any other data source
we haven't considered.

Need grounded search ON for this — these are real-world product
existence questions, not architecture opinions.
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
    "I need real-world product research with grounded search — facts "
    "about services that exist today, not architectural opinions.\n\n"
    "Context: I'm building a restaurant pricing intelligence tool. "
    "We've been trying to extract competitor menu prices via your "
    "grounded search and the accuracy isn't good enough. Specialty "
    "pizzas (Meat Lovers, Combination) come back priced lower than "
    "plain Cheese Pizza, which is logically impossible. Double burgers "
    "come back priced lower than single burgers. The problem is search "
    "retrieval mixing up variants — too many cheese pizza prices in the "
    "result pool contaminate any specialty-pizza query.\n\n"
    "We've explored: better prompts (10+ iterations), parser-side "
    "backstops, two-pool source models, anchor-list seeding from Google "
    "Places. None of it gets us to the accuracy we need.\n\n"
    "I want to know what other paths exist. Search for:\n\n"
    "  (1) Commercial APIs / data services that provide STRUCTURED\n"
    "      restaurant menu data with item-level prices for INDEPENDENT\n"
    "      restaurants (not just chains). Include subscription and\n"
    "      enterprise services. Things like Datassential, Foursquare,\n"
    "      SafeGraph, etc. — what do they actually offer at the\n"
    "      item-with-price level?\n\n"
    "  (2) Does MenuSpy (menuspy.ai) — our direct competitor — offer\n"
    "      an API or partnership that we could integrate with? Their\n"
    "      product is item-level competitor pricing for restaurant\n"
    "      owners; if they expose data we could rebrand it.\n\n"
    "  (3) Any other unconventional data sources: restaurant industry\n"
    "      co-ops, food distributor data products (Sysco/USFoods),\n"
    "      online ordering platform partnerships (Toast/ChowNow/Slice\n"
    "      who have native menu data), POS data brokers, anything\n"
    "      that would give us per-item pricing for small independents\n"
    "      WITHOUT us having to scrape it ourselves.\n\n"
    "Be concrete: name the services, find their pricing if available, "
    "tell me the realistic constraints (chains-only? enterprise pricing? "
    "data freshness lag?). Don't speculate — cite what you find."
)

send(
    "Drill into the most viable of those. For each one you flagged, what's "
    "the realistic path to ACCESS the data? Walk me through:\n\n"
    "  - Can a 1-person startup actually buy/license this in year 1, or\n"
    "    is it gated behind enterprise sales cycles?\n"
    "  - What's the data quality for INDEPENDENT restaurants (the\n"
    "    mom-and-pop pizzeria, not chains)? If it's only chains, it\n"
    "    doesn't help me.\n"
    "  - What's the freshness — daily, weekly, monthly stale?\n"
    "  - Pricing model — flat fee, per-call, per-restaurant, percent\n"
    "    of revenue?\n\n"
    "I'm trying to figure out if any of these are a 'just buy access' "
    "path for v1, or if they're all gated/expensive enough that I'm\n"
    "stuck with the grounded-search approach until I have funding."
)

send(
    "Last question. Given everything you found: is there ANY commercial "
    "data service today that would get a 1-person SMB-targeted product "
    "to ACCURATE per-item competitor pricing for small independents in "
    "the US northeast, at a cost the SMB can support ($80/mo retail "
    "tier means I can't burn $50/customer on data)?\n\n"
    "If yes — name it and tell me the path.\n"
    "If no — say so directly. Then tell me: is the only realistic v1 "
    "approach to live with imperfect grounded-search accuracy and "
    "differentiate on other features (POS export, supplier marketplace) "
    "until we have funding to either build a data warehouse or buy "
    "enterprise data access?"
)


ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = REPO / "storage" / "logs" / f"gemini_data_apis_{ts}.txt"
out.parent.mkdir(exist_ok=True)
out.write_text("".join(TRANSCRIPT), encoding="utf-8")
print(f"\n[saved -> {out}]")
