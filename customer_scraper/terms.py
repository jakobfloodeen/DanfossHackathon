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

What stops the read (it covers what this tool does - automated reading and extraction of the text):
   TDMRep reservations, and terms / legal / privacy sentences that forbid scraping, crawling, robots, automated
   access or (text and) data mining. The ordinary robots.txt rules for our crawler are respected in core.py.
What is only reported (it is aimed at collecting data to TRAIN AI models, which this tool does not do):
   <meta name="robots" content="noai">, robots.txt blocks of AI crawlers (GPTBot, ClaudeBot, CCBot ...), and
   terms sentences that only forbid using the content to train AI / machine learning models.
A stop means: read nothing more, tell the user which page says so (with the sentence). This is a careful reading
in code, not legal advice.
"""
import json
import re
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

# robots.txt agents of AI crawlers / AI-training data collectors: a block is reported, not a stop - this tool
# is not one of them and identifies itself with its own user agent, whose robots rules are followed
AI_AGENTS_NOTED = ('GPTBot', 'ClaudeBot', 'anthropic-ai', 'CCBot', 'Google-Extended', 'Applebot-Extended',
                   'Bytespider', 'meta-externalagent', 'PerplexityBot', 'AI', 'MachineLearning')

TERMS_LINK_WORDS = ('terms', 'conditions', 'legal', 'disclaimer', 'imprint', 'impressum', 'nutzungsbedingungen',
                    'vilkaar', 'vilkår', 'betingelser', 'mentions-legales', 'conditions-generales', 'note-legali',
                    'aviso-legal', 'condiciones', 'privacy', 'data-policy', 'data policy', 'datapolitik',
                    'privatlivspolitik', 'datenschutz', 'copyright')
MAX_TERMS_PAGES = 4

MINING = re.compile(     # what this tool does: automated reading and extraction
    r"\b(scrap(?:e|es|ed|ing|er|ers)|screen[- ]scraping|data[- ]mining|text and data mining|text- and data[- ]mining|"
    r"tdm|crawl(?:ing|er|ers)?|spider(?:s|ing)?|web ?robots?|robots?|bots?|harvest(?:ing)?|"
    r"automated (?:means|tools?|systems?|programs?|software|access|collection|extraction|quer(?:y|ies)|scripts?|"
    r"processes|devices?|methods?)|data extraction|extract(?:ing)? (?:data|content)|"
    r"text- und data-mining|text und data mining)\b", re.I)
AI_TRAINING = re.compile(  # what this tool does NOT do: building AI training data
    r"\b(machine learning|artificial intelligence|ai (?:training|systems?|models?)|train(?:ing)? (?:an? )?(?:ai|models?|"
    r"algorithms?)|large language models?|llms?|generative ai)\b", re.I)
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
    """The first sentence that forbids scraping / automated access / data mining, or None."""
    for s in _sentences(text):
        if NOT_ABOUT_US.search(s) and not re.search(r"scrap|crawl|data[- ]mining|robot", s, re.I):
            continue
        if not (PROHIBIT.search(s) or LEGAL_REFERENCE.search(s)):
            continue
        if AI_TRAINING.search(s) and not _beyond_training(s):
            continue                       # only about training AI: reported by find_training_clause, not a stop
        if MINING.search(s):
            return s[:400]
    return None


def _beyond_training(sentence: str) -> bool:
    """A sentence that names AI *and* forbids scraping/data mining in general still counts as a stop."""
    without_ai = AI_TRAINING.sub(" ", sentence)
    return bool(MINING.search(without_ai)) and not re.search(r"\b(?:to|for) (?:train|build|develop)\w*", sentence, re.I)


def find_training_clause(text: str) -> str | None:
    """A sentence that only forbids using the content to train AI (reported, not a stop)."""
    for s in _sentences(text):
        if AI_TRAINING.search(s) and PROHIBIT.search(s) and not find_reservation(s):
            return s[:300]
    return None


def meta_reservation(meta: dict) -> str | None:
    """<meta name="tdm-reservation" content="1"> (a text-and-data-mining reservation: stop)."""
    if str(meta.get('tdm-reservation', '')).strip() == '1':
        return 'tdm-reservation = 1 (TDMRep meta tag)'
    return None


def meta_note(meta: dict) -> str | None:
    """<meta name="robots" content="noai"> - aimed at AI training datasets: reported only."""
    robots = str(meta.get('robots', '')).lower()
    if 'noai' in robots or 'noimageai' in robots:
        return f'pages carry robots "{robots}" (no AI training) - reported; this tool does not train AI'
    return None


def robots_ai_check(policy, url: str) -> tuple[str | None, list[str]]:
    """-> (None - AI-crawler blocks are never a stop for this tool, notes about which ones are blocked)."""
    if not isinstance(policy, RobotFileParser):
        return None, []
    noted = [a for a in AI_AGENTS_NOTED if _blocks(policy, a, url)]
    return None, ([f"robots.txt blocks AI crawlers ({', '.join(noted)}) - reported; this tool is not one of them "
                   "and follows the rules for its own user agent"] if noted else [])


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
