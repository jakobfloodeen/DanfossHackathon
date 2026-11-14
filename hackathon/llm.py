"""
Everything that talks to the AI lives here (owner: P2).

Two jobs:
  1. extract_attributes(): read datasheet pages -> fill the schema, with page + quote
  2. explain(): write sales notes using ONLY the comparison rows we give it

Set your key in a file called .env (see .env.example). Never commit .env!
"""
import json
import os
import re

from dotenv import load_dotenv

from schema import attributes

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")


def has_api_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _ask(prompt: str, max_tokens: int = 4000) -> str:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    msg = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=0,  # deterministic = more repeatable results
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def _parse_json(text: str):
    """The model sometimes wraps JSON in ```json fences - strip them."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


# ---------------------------------------------------------------- 1. EXTRACT
def extract_attributes(pages: dict[int, str], family: str, product_name: str) -> list[dict]:
    """pages = {page_number: page_text}. Returns one dict per attribute."""
    attr_lines = "\n".join(f'- "{name}": {spec["hint"]}' for name, spec in attributes(family).items())
    page_block = "\n\n".join(f"=== PAGE {p} ===\n{t}" for p, t in pages.items())

    prompt = f"""You extract technical data from a hydraulic product datasheet.
Product we care about: {product_name}  (family: {family})

Attributes to find:
{attr_lines}

RULES (follow strictly):
- Use ONLY the pages below. Never use outside knowledge. Never guess.
- If an attribute is not stated, return value null, unit null, page null, quote null.
- "quote" must be copied EXACTLY from the page text (max 25 words) and must contain the value.
- If the datasheet covers several sizes, use the size matching "{product_name}".
- Keep the unit exactly as written in the datasheet (we convert units later in code).

Return ONLY a JSON list, no other text:
[{{"attribute": "...", "value": "...", "unit": "...", "page": 3, "quote": "..."}}]

DATASHEET PAGES:
{page_block}
"""
    return _parse_json(_ask(prompt))


# ---------------------------------------------------------------- 2. EXPLAIN
def explain(comparison_rows: list[dict], customer_context: str) -> str:
    """Sales notes for the one-pager. The model may only use the rows we pass in."""
    prompt = f"""You help a Danfoss salesperson prepare for a customer meeting.

Customer context (may be empty): {customer_context or "none given"}

Comparison rows (the ONLY facts you may use; each row has an id like R1):
{json.dumps(comparison_rows, indent=1, ensure_ascii=False)}

Write:
1. "Key differences" - 3 bullets
2. "Talking points for this customer" - 3 bullets
3. "Trade-offs / risks of switching" - 2 bullets
4. "Open questions to verify" - list every missing, conflicting or unverified value

RULES: Every bullet must cite its row ids in brackets, e.g. [R2, R5].
Do not invent numbers, prices, or facts that are not in the rows. Be short and concrete.
"""
    return _ask(prompt, max_tokens=1500)
