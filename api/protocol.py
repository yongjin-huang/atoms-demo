"""Agent protocol v2: prose, then a manifest, then whole files.

The model's reply is a stream of lines. Everything before the first directive
is conversation. A build looks like:

    Sure — a pomodoro timer. Plain HTML, state in localStorage.
    ===MANIFEST===
    {"template": "static", "delete": []}
    ===FILE index.html===
    <!DOCTYPE html>
    ...
    ===END===

Rules the parser enforces:
  - MANIFEST comes first, exactly once, before any FILE.
  - Files are whole documents. A revision emits only the files that change,
    plus a manifest `delete` list; the server merges with the previous
    version's files (`merge_files`).
  - ===END=== is mandatory for a build. A reply with no directives at all is
    a chat turn — fine, nothing to build.

This module is deliberately pure: no SDKs, no database, no I/O. That is what
makes it testable and what keeps sandbox providers (phase C) from ever seeing
model output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterator

# ---------------------------------------------------------------------------
# Templates: commands are data owned by the server, never text from the model.
# `steps`/`serve` are consumed by the sandbox engine in phase C; validation
# uses `required` and `enabled` today.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    id: str
    enabled: bool                    # offered to the model in the system prompt
    runtime: str                     # "srcdoc" | "sandbox"
    required: tuple[str, ...]        # files that must exist after merging
    steps: tuple[tuple[str, ...], ...] = ()   # finite exec steps (phase C)
    serve: tuple[str, ...] = ()               # long-running command (phase C)
    port: int = 0


TEMPLATES: dict[str, Template] = {
    "static": Template(
        "static", enabled=True, runtime="srcdoc", required=("index.html",)
    ),
    # Defined now so the protocol, schema and UI are ready; switched on when
    # the sandbox engine lands (phase C). Until then the model never sees them.
    "vite-react": Template(
        "vite-react", enabled=False, runtime="sandbox",
        required=("package.json", "index.html"),
        steps=(("npm", "ci", "--no-audit", "--no-fund"),),
        serve=("npm", "run", "dev", "--", "--host", "0.0.0.0"),
        port=5173,
    ),
    "node": Template(
        "node", enabled=False, runtime="sandbox",
        required=("package.json", "server.js"),
        steps=(("npm", "ci", "--no-audit", "--no-fund"),),
        serve=("node", "server.js"),
        port=3000,
    ),
}


def enabled_templates() -> list[Template]:
    return [t for t in TEMPLATES.values() if t.enabled]


# ---------------------------------------------------------------------------
# Limits and path rules
# ---------------------------------------------------------------------------

MAX_FILES = 64
MAX_FILE_BYTES = 256_000
MAX_TOTAL_BYTES = 1_500_000

_PATH_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class ProtocolError(Exception):
    """The model broke the protocol. Message is safe to show and to feed back
    to the model as the strict-retry instruction."""


def validate_path(path: str) -> str:
    p = path.strip()
    if not p or len(p) > 200:
        raise ProtocolError(f"Bad file path: {path!r}")
    if not _PATH_OK.match(p) or ".." in p or "//" in p:
        raise ProtocolError(f"Bad file path: {path!r}")
    return p


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    template: Template
    delete: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"template": self.template.id, "delete": list(self.delete)}


def parse_manifest(raw: str) -> Manifest:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"Manifest is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ProtocolError("Manifest must be a JSON object.")

    template_id = data.get("template")
    template = TEMPLATES.get(template_id) if isinstance(template_id, str) else None
    if template is None or not template.enabled:
        allowed = ", ".join(t.id for t in enabled_templates())
        raise ProtocolError(f"Unknown template {template_id!r}. Use one of: {allowed}.")

    delete = data.get("delete", [])
    if not isinstance(delete, list) or not all(isinstance(d, str) for d in delete):
        raise ProtocolError('"delete" must be a list of paths.')

    return Manifest(template=template, delete=tuple(validate_path(d) for d in delete))


# ---------------------------------------------------------------------------
# The streaming parser
# ---------------------------------------------------------------------------

_DIRECTIVE = re.compile(r"^===(MANIFEST|END|FILE (?P<path>.+?))===\s*$")

# States
_CHAT, _MANIFEST, _FILE = "chat", "manifest", "file"


@dataclass
class Parser:
    """Feed it text chunks; it yields events. Line-buffered by design — a
    directive is a whole line, so holding at most one incomplete line is all
    the lookahead the protocol ever needs.

    Events:
        ("chat", text)          prose, newline-terminated pieces
        ("manifest", Manifest)  the manifest block closed and parsed
        ("file_open", path)
        ("file", text)          content for the currently open file
        ("file_close", path)
    """

    state: str = _CHAT
    ended: bool = False
    chat_parts: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    manifest: Manifest | None = None

    _buf: str = ""
    _manifest_raw: list[str] = field(default_factory=list)
    _current: str | None = None
    _current_parts: list[str] = field(default_factory=list)

    # -- public ------------------------------------------------------------

    def feed(self, text: str) -> Iterator[tuple[str, object]]:
        self._buf += text
        while (i := self._buf.find("\n")) != -1:
            line, self._buf = self._buf[:i], self._buf[i + 1 :]
            yield from self._line(line)

    def finish(self) -> Iterator[tuple[str, object]]:
        """Call once, after the stream ends. Raises if a build was started
        but never properly ended."""
        if self._buf:
            # A final line with no trailing newline. It may still be a
            # directive (===END=== as the very last bytes is common).
            line, self._buf = self._buf, ""
            yield from self._line(line)

        if self.ended:
            return
        if self.state == _CHAT and self.manifest is None:
            return  # pure conversation — nothing was built, nothing to check
        raise ProtocolError("The reply started a build but never reached ===END===.")

    @property
    def chat(self) -> str:
        return "".join(self.chat_parts).strip()

    # -- internals -----------------------------------------------------------

    def _line(self, line: str) -> Iterator[tuple[str, object]]:
        if self.ended:
            return  # ignore anything after END
        if m := _DIRECTIVE.match(line):
            yield from self._directive(m)
            return

        if self.state == _CHAT:
            self.chat_parts.append(line + "\n")
            yield ("chat", line + "\n")
        elif self.state == _MANIFEST:
            self._manifest_raw.append(line)
        else:
            self._current_parts.append(line + "\n")
            yield ("file", line + "\n")

    def _directive(self, m: re.Match) -> Iterator[tuple[str, object]]:
        kind = m.group(1)

        if kind == "MANIFEST":
            if self.state != _CHAT or self.manifest is not None:
                raise ProtocolError("===MANIFEST=== must appear once, before any file.")
            self.state = _MANIFEST
            return

        if kind.startswith("FILE"):
            if self.manifest is None and self.state != _MANIFEST:
                raise ProtocolError("A ===FILE=== block needs a ===MANIFEST=== first.")
            yield from self._close_block()
            path = validate_path(m.group("path"))
            if path in self.files:
                raise ProtocolError(f"File {path!r} was emitted twice.")
            if len(self.files) >= MAX_FILES:
                raise ProtocolError(f"Too many files (limit {MAX_FILES}).")
            self.state = _FILE
            self._current = path
            self._current_parts = []
            yield ("file_open", path)
            return

        # END
        if self.manifest is None and self.state != _MANIFEST:
            raise ProtocolError("===END=== without a build.")
        yield from self._close_block()
        if not self.files:
            raise ProtocolError("A build must contain at least one file.")
        self.ended = True

    def _close_block(self) -> Iterator[tuple[str, object]]:
        if self.state == _MANIFEST:
            self.manifest = parse_manifest("\n".join(self._manifest_raw))
            yield ("manifest", self.manifest)
        elif self.state == _FILE and self._current is not None:
            content = "".join(self._current_parts)
            if len(content.encode()) > MAX_FILE_BYTES:
                raise ProtocolError(f"{self._current!r} exceeds {MAX_FILE_BYTES} bytes.")
            self.files[self._current] = content
            yield ("file_close", self._current)
            self._current = None


# ---------------------------------------------------------------------------
# Merge + final validation (revision = copy-on-write over the previous set)
# ---------------------------------------------------------------------------


def merge_files(
    manifest: Manifest,
    emitted: dict[str, str],
    previous: dict[str, str] | None,
) -> dict[str, str]:
    """Previous files, minus deletions, overlaid with what the model emitted.
    Every version is a complete, self-contained snapshot."""
    merged = dict(previous or {})
    for path in manifest.delete:
        merged.pop(path, None)
    merged.update(emitted)

    for path in manifest.template.required:
        if path not in merged:
            raise ProtocolError(f"Template {manifest.template.id!r} requires {path!r}.")
    if len(merged) > MAX_FILES:
        raise ProtocolError(f"Too many files (limit {MAX_FILES}).")
    total = sum(len(c.encode()) for c in merged.values())
    if total > MAX_TOTAL_BYTES:
        raise ProtocolError(f"App exceeds {MAX_TOTAL_BYTES} bytes in total.")
    return merged
