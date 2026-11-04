"""Turn Confluence storage XHTML / site HTML / Jira wiki markup into the plain text quotes are checked against."""
import html, re


def text(markup):
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    s = re.sub(r"(?is)<ac:parameter\b.*?</ac:parameter>", " ", s)  # macro params (toc depth, code language)
    s = re.sub(r"(?s)<!\[CDATA\[(.*?)\]\]>", r"\1", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\[([^\]|\[]+)\|[^\]]+\]", r"\1", s)  # Jira link [text|url]
    s = re.sub(r"\{\{|\}\}|\{code[^}]*\}|\{noformat\}|\{quote\}|\{color[^}]*\}", " ", s)  # Jira wiki markup
    return re.sub(r"\s+", " ", s).strip()


def norm(s):
    """Whitespace- and quote-style-insensitive form used for verbatim checks."""
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()
