"""robots.txt rules with Google semantics (``*`` and ``$`` wildcards, longest match wins, Allow
wins ties). ``urllib.robotparser`` does plain prefix matching and ignores wildcards, so a rule
like ``Disallow: /*`` or ``Disallow: /*LAC*`` would be missed; PoliteClient checks both."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class RobotsRules:
    """robots.txt rules for ``User-agent: *`` with Google semantics (``*``, ``$``, longest
    match wins, Allow wins ties). The stdlib parser ignores wildcards and would allow
    everything on JobTeaser's school hosts."""

    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)

    @classmethod
    def parse(cls, text: str, agent: str = "*") -> RobotsRules:
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))
        agent = agent.lower()
        specific = [r for a, r in groups if any(x != "*" and x in agent for x in a)]
        if specific:
            return cls([rule for group in specific for rule in group])
        return cls([rule for a, r in groups if "*" in a for rule in r])

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if _robots_match(pattern, path):
                cand = (len(pattern), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]


def _robots_match(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.match(regex + ("$" if anchored else ""), path) is not None
