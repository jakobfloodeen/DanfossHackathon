"""Does the site owner reserve their website against automated reading / data mining?  (no AI involved)

Under EU law (DSM Directive 2019/790 art. 4, in Denmark ophavsretsloven § 11 b) text and data mining of public
web pages for commercial purposes is allowed UNLESS the rightholder has reserved it "in an appropriate manner,
such as machine-readable means". Courts have accepted robots.txt, TDMRep and even a prohibition written in a
site's terms or data policy (Danish Maritime and Commercial Court, Oct 2025). So before the AI reads a customer's
website, the crawler checks, in code:

1. robots.txt rules for AI agents that cover what we do (our text goes to Anthropic's Claude)
2. TDMRep: /.well-known/tdmrep.json and <meta name="tdm-reservation" content="1">
3. <meta name="robots"> values noai / noimageai
4. the site's terms of use / legal notice / imprint / privacy pages: a sentence that combines scraping / data
   mining / automated access / AI with a prohibition ("prohibited", "may not", "without our consent" ...)

A match means: stop, read nothing more, tell the user which page says so (with the sentence). This is a careful
reading in code, not legal advice: it errs on the side of stopping.
"""
import json
import re
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

# robots.txt user agents whose block covers our use (we send the text to an AI from Anthropic) ...
AI_AGENTS_RESERVED = ('anthropic-ai', 'ClaudeBot', 'Claude-User', 'AI', 'MachineLearning')
# ... and AI-training crawlers of others: blocking them is reported, not treated as a reservation for us
AI_AGENTS_NOTED = ('GPTBot', 'CCBot', 'Google-Extended', 'Applebot-Extended', 'Bytespider', 'meta-externalagent')

TERMS_LINK_WORDS = ('terms', 'conditions', 'legal', 'disclaimer', 'imprint', 'impressum', 'nutzungsbedingungen',
                    'vilkaar', 'vilkår', 'betingelser', 'mentions-legales', 'conditions-generales', 'note-legali',
                    'aviso-legal', 'condiciones', 'privacy', 'data-policy', 'data policy', 'datapolitik',
                    'privatlivspolitik', 'datenschutz', 'copyright')
MAX_TERMS_PAGES = 4

MINING = re.compile(
    r"\b(scrap(?:e|es|ed|ing|er|ers)|screen[- ]scraping|data[- ]mining|text and data mining|text- and data[- ]mining|"
    r"tdm|crawl(?:ing|er|ers)?|spider(?:s|ing)?|web ?robots?|robots?|bots?|harvest(?:ing)?|"
    r"automated (?:means|tools?|systems?|programs?|software|access|collection|extraction|quer(?:y|ies)|scripts?|"
    r"processes|devices?|methods?)|data extraction|extract(?:ing)? (?:data|content)|"
    r"machine learning|artificial intelligence|ai (?:training|systems?|models?)|train(?:ing)? (?:an? )?(?:ai|models?)|"
    r"large language models?|llms?|text- und data-mining|text und data mining)\b", re.I)
PROHIBIT = re.compile(
    r"\b(prohibit(?:ed|s)?|not (?:be )?(?:permitted|allowed|authori[sz]ed)|may not|must not|shall not|cannot be used|"
    r"forbidden|without (?:our |the |prior |express |explicit |written )*(?:consent|permission|approval|authori[sz]ation)|"
    r"reserves?(?: all)?(?: the| its)? rights?|expressly reserve[sd]?|rights? reservation|opt(?:s|ed)?[- ]out|"
    r"unauthori[sz]ed|untersagt|verboten|nicht gestattet|ikke tilladt|forbudt|vorbehalten)\b", re.I)
LEGAL_REFERENCE = re.compile(r"(2019/790|article 4\b.*(?:dsm|directive)|§\s*44b|§\s*11\s*b|text and data mining)", re.I)
NOT_ABOUT_US = re.compile(r"automated (?:individual )?decision|profiling", re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if len(s.strip()) > 20]


def find_reservation(text: str) -> str | None:
    """The first sentence that reserves the site against scraping / data mining / AI, or None."""
    for s in _sentences(text):
        if NOT_ABOUT_US.search(s) and not re.search(r"scrap|crawl|data[- ]mining|robot", s, re.I):
            continue
        if MINING.search(s) and (PROHIBIT.search(s) or LEGAL_REFERENCE.search(s)):
            return s[:400]
    return None


def meta_reservation(meta: dict) -> str | None:
    """<meta name="tdm-reservation" content="1"> or <meta name="robots" content="noai">."""
    if str(meta.get('tdm-reservation', '')).strip() == '1':
        return 'tdm-reservation = 1 (TDMRep meta tag)'
    robots = str(meta.get('robots', '')).lower()
    if 'noai' in robots or 'noimageai' in robots:
        return f'robots meta tag: "{robots}"'
    return None


def robots_ai_check(policy, url: str) -> tuple[str | None, list[str]]:
    """-> (reservation text or None, notes about other AI crawlers being blocked)."""
    if not isinstance(policy, RobotFileParser):
        return None, []
    blocked = [a for a in AI_AGENTS_RESERVED if _blocks(policy, a, url)]
    noted = [a for a in AI_AGENTS_NOTED if _blocks(policy, a, url)]
    reservation = f"robots.txt blocks AI agents: {', '.join(blocked)}" if blocked else None
    return reservation, ([f"robots.txt blocks AI-training crawlers ({', '.join(noted)}); "
                          "this tool is not a training crawler and still reads the public pages"] if noted else [])


def _blocks(policy: RobotFileParser, agent: str, url: str) -> bool:
    """True only when a rule NAMES this agent (the general '*' rules are handled by the normal robots check)."""
    entries = [e for e in getattr(policy, 'entries', []) if any(
        a != '*' and a.lower() == agent.lower() for a in e.useragents)]
    return any(not e.allowance(urlsplit(url).path or '/') for e in entries)


def tdmrep_reservation(data: bytes, url: str) -> str | None:
    """/.well-known/tdmrep.json: [{"location": "/*", "tdm-reservation": 1, ...}]"""
    try:
        rules = json.loads(data.decode('utf-8', errors='replace'))
    except ValueError:
        return None
    path = urlsplit(url).path or '/'
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict) or str(rule.get('tdm-reservation')) != '1':
            continue
        pattern = re.escape(str(rule.get('location', '/*'))).replace(r'\*', '.*')
        if re.match(pattern, path):
            return f"tdmrep.json reserves {rule.get('location', '/*')} against text and data mining"
    return None


def terms_links(links: list[dict], base: str, hosts: set) -> list[str]:
    """Links to the terms / legal / privacy pages on the customer's own site, most relevant first."""
    out = []
    for link in links:
        target = urljoin(base, link.get('href', ''))
        p = urlsplit(target)
        if p.scheme not in ('http', 'https') or p.hostname not in hosts:
            continue
        label = (link.get('label', '') + ' ' + p.path).lower()
        hits = [w for w in TERMS_LINK_WORDS if w in label]
        if hits:
            rank = 0 if any(w in label for w in ('terms', 'conditions', 'legal', 'nutzungs', 'vilk', 'betingelser')) \
                else 1 if any(w in label for w in ('imprint', 'impressum', 'disclaimer', 'copyright')) else 2
            out.append((rank, target.split('#')[0]))
    return [u for _, u in sorted(dict.fromkeys(out))][:MAX_TERMS_PAGES]
