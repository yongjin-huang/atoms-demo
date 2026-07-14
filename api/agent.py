"""The agent, speaking protocol v2 (see protocol.py).

One streaming call, structured output. The model always answers in prose
first. When a build is warranted it emits a manifest and whole files:

    Sure — a pomodoro timer. Plain HTML, state kept in localStorage.
    ===MANIFEST===
    {"template": "static", "delete": []}
    ===FILE index.html===
    <!DOCTYPE html>…
    ===END===

A greeting never reaches a directive, so it never produces a version.
Parsing, validation and the previous-version merge all live in protocol.py —
this module only knows how to talk to providers and how to retry once when
the model breaks the protocol.

Reasoning models (DeepSeek R1) emit `reasoning_content` on a separate field;
that streams as its own channel so thinking is never confused for the reply.
"""

from typing import Iterator

from anthropic import Anthropic
from openai import OpenAI

from models import User
from protocol import Manifest, Parser, ProtocolError, enabled_templates, merge_files
from providers import PROVIDERS, ModelSpec, api_key_for, find_model


def _system_prompt() -> str:
    names = ", ".join(t.id for t in enabled_templates())
    return f"""You are an agent that builds small web apps through conversation.

HOW YOU REPLY
Always speak to the person first, in plain prose. Two or three sentences. Say
what you understood and what you're about to build, or ask a question if the
request is genuinely unclear.

If they are greeting you, chatting, or asking a question — just reply. Do not
emit any === directive.

WHEN YOU BUILD
Only when the person has asked for an app, or asked to change the existing
one, end your prose, then emit exactly this structure:

===MANIFEST===
{{"template": "<one of: {names}>", "delete": ["path-to-remove", "..."]}}
===FILE <path>===
<the complete file content>
===END===

RULES
- Directives are whole lines, exactly as shown. Nothing else on those lines.
- Files are complete documents, never diffs or fragments.
- When revising an existing app, emit ONLY the files that change, and list
  removed files in "delete". Unchanged files carry over automatically.
- The "static" template is ONE self-contained index.html: all CSS in <style>
  inside <head>, all JS in <script>. No external files, no CDN imports, no
  network calls, no remote images. Real state, real event handlers, real
  interactivity. Deliberate type, spacing and colour — not a wireframe.
- Keep index.html under ~400 lines.
"""


class AgentError(Exception):
    """Message is written for the user; the UI shows it verbatim."""


def _history_messages(turns: list[dict], previous_files: dict[str, str] | None) -> list[dict]:
    """Rebuild the conversation for the model.

    Prior builds are summarised, not replayed. Only the *current* version's
    files are sent, and only when there is one to revise. Large files are
    truncated — the model rewrites whole files anyway, so a revision that
    needs a truncated file's detail should regenerate it.
    """
    msgs: list[dict] = []
    for t in turns:
        if t["role"] == "user":
            msgs.append({"role": "user", "content": t["content"]})
        else:
            text = t["content"]
            if t.get("version_id"):
                text += "\n\n[built the app — full source omitted here]"
            msgs.append({"role": "assistant", "content": text})

    if previous_files:
        parts = ["The current app files are:"]
        for path, content in sorted(previous_files.items()):
            if len(content) > 24_000:
                content = content[:24_000] + "\n… [truncated]"
            parts.append(f"--- {path} ---\n{content}")
        msgs.append({"role": "user", "content": "\n\n".join(parts)})
        msgs.append({
            "role": "assistant",
            "content": "Got it — that's the app as it stands. What should change?",
        })
    return msgs


def _stream_call(spec: ModelSpec, user: User, messages: list[dict]) -> Iterator[tuple[str, str]]:
    """Yield ("reason", text) and ("content", text) deltas."""
    key = api_key_for(spec.provider, user)
    if not key:
        raise AgentError(f"{PROVIDERS[spec.provider].label} is not configured.")

    if spec.provider == "anthropic":
        client = Anthropic(api_key=key)
        with client.messages.stream(
            model=spec.model, max_tokens=8000, system=_system_prompt(), messages=messages
        ) as stream:
            for text in stream.text_stream:
                yield ("content", text)
        return

    client = OpenAI(api_key=key, base_url=PROVIDERS[spec.provider].base_url)
    stream = client.chat.completions.create(
        model=spec.model,
        max_tokens=8000,
        stream=True,
        messages=[{"role": "system", "content": _system_prompt()}, *messages],
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # DeepSeek R1 puts its chain of thought here, separate from the answer.
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield ("reason", reasoning)
        if delta.content:
            yield ("content", delta.content)


def _non_streaming(spec: ModelSpec, user: User, messages: list[dict], extra: str) -> str:
    key = api_key_for(spec.provider, user)
    if spec.provider == "anthropic":
        res = Anthropic(api_key=key).messages.create(
            model=spec.model, max_tokens=8000, system=_system_prompt() + extra, messages=messages
        )
        return "".join(b.text for b in res.content if b.type == "text")

    client = OpenAI(api_key=key, base_url=PROVIDERS[spec.provider].base_url)
    res = client.chat.completions.create(
        model=spec.model,
        max_tokens=8000,
        messages=[{"role": "system", "content": _system_prompt() + extra}, *messages],
    )
    return res.choices[0].message.content or ""


def converse(
    model_id: str,
    user: User,
    prompt: str,
    turns: list[dict],
    previous_files: dict[str, str] | None = None,
) -> Iterator[tuple[str, object]]:
    """Stream a turn.

    Yields, in order:
      ("reason", text)          the model's thinking, if it exposes any
      ("chat", text)            the prose reply, as it streams
      ("manifest", dict)        the build's manifest, once parsed
      ("file_open", path)
      ("code", text)            content for the currently open file
      ("file_close", path)
      ("retry", "")             protocol violation; retrying strictly, once
      ("chat_done", full_prose)
      ("build_done", {"manifest": dict, "runtime": str, "files": {path: content}})
                                 — omitted entirely on a chat-only turn.
                                 files is the MERGED snapshot, ready to persist.
    """
    spec = find_model(model_id, user)
    if spec is None:
        raise AgentError("That model isn't available. Pick another.")

    messages = [*_history_messages(turns, previous_files), {"role": "user", "content": prompt}]

    parser = Parser()
    reasons_seen = False
    violation: ProtocolError | None = None

    try:
        for kind, text in _stream_call(spec, user, messages):
            if kind == "reason":
                reasons_seen = True
                yield ("reason", text)
                continue
            try:
                yield from _forward(parser.feed(text))
            except ProtocolError as e:
                violation = e
                break
        if violation is None:
            try:
                yield from _forward(parser.finish())
            except ProtocolError as e:
                violation = e
    except AgentError:
        raise
    except Exception as e:
        raise AgentError(f"{spec.label} failed: {e}") from e

    if violation is not None:
        # One strict retry, non-streaming. The violation text tells the model
        # exactly which rule it broke.
        yield ("retry", "")
        try:
            raw = _non_streaming(
                spec, user, messages,
                "\n\nYour last reply was rejected: " + str(violation)
                + " Reply again. Prose first, then the exact ===MANIFEST=== / "
                  "===FILE path=== / ===END=== structure.",
            )
        except Exception as e:
            raise AgentError(f"{spec.label} failed: {e}") from e
        parser = Parser()
        try:
            yield from _forward(parser.feed(raw))
            yield from _forward(parser.finish())
        except ProtocolError as e:
            raise AgentError(
                f"{spec.label} couldn't produce a valid build ({e}). "
                "Try again or switch models."
            ) from e

    chat = parser.chat

    if not parser.ended:
        # No directives: this was a conversation. Nothing to build or save.
        yield ("chat_done", chat or ("…" if not reasons_seen else "Thought about it."))
        return

    manifest: Manifest = parser.manifest  # ended=True guarantees this
    try:
        files = merge_files(manifest, parser.files, previous_files)
    except ProtocolError as e:
        raise AgentError(f"The build was invalid: {e}") from e

    yield ("chat_done", chat or "Built it.")
    yield ("build_done", {
        "manifest": manifest.as_dict(),
        "runtime": manifest.template.runtime,
        "files": files,
    })


def _forward(events) -> Iterator[tuple[str, object]]:
    """protocol.Parser events → the converse() event vocabulary."""
    for kind, payload in events:
        if kind == "chat":
            yield ("chat", payload)
        elif kind == "manifest":
            yield ("manifest", payload.as_dict())
        elif kind == "file":
            yield ("code", payload)
        else:  # file_open / file_close
            yield (kind, payload)


def title_from(prompt: str) -> str:
    t = " ".join(prompt.split())
    return t[:48] + "\u2026" if len(t) > 48 else t
