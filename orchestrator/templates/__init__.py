"""Jinja2 templates for issue comments and the PR body, rendered from named fields only.

A template declares the fields it uses; ``render_comment`` refuses a field
it does not declare and a field it misses, so no agent free text can reach
a comment outside the places the template names (architecture.md · Templates).
"""

import re
from functools import cache, lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, meta

TEMPLATES_DIR = Path(__file__).resolve().parent
COMMENTS_DIR = TEMPLATES_DIR / "comments"
MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9][\w-]*)")


def nomention(text: object) -> str:
    """``@login`` in agent-authored text becomes a code span; GitHub notifies nobody."""
    return MENTION.sub(r"`@\1`", str(text))


def cell(text: object) -> str:
    """One Markdown table cell: pipes escaped, newlines flattened."""
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


@lru_cache(maxsize=1)
def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        autoescape=False,  # Markdown, not HTML
    )
    env.filters["nomention"] = nomention
    env.filters["cell"] = cell
    return env


def comment_templates() -> tuple[str, ...]:
    return tuple(sorted(p.name.removesuffix(".md.j2") for p in COMMENTS_DIR.glob("*.md.j2")))


def _relpath(name: str) -> str:
    return name if name.endswith(".j2") else f"comments/{name}.md.j2"


@cache
def template_fields(name: str) -> frozenset[str]:
    env = environment()
    assert env.loader is not None
    source, _, _ = env.loader.get_source(env, _relpath(name))
    return frozenset(meta.find_undeclared_variables(env.parse(source)))


def render_template(name: str, **fields: object) -> str:
    """Render a template by comment name or by path under ``templates/``; strict both ways."""
    unknown = sorted(set(fields) - template_fields(name))
    if unknown:
        raise ValueError(
            f"template {name!r} declares no field {unknown}; templates render named fields only"
        )
    text = environment().get_template(_relpath(name)).render(**fields)
    return text.strip()


def render_comment(name: str, **fields: object) -> str:
    return render_template(name, **fields)
