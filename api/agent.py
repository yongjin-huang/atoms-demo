"""The agent.

One streaming call, two channels. The model always answers in prose first. It
emits an app *only* when building is actually warranted, after a sentinel line.

    Sure — a pomodoro timer with a task list. I'll use a 25/5 cycle
    and keep the tasks in memory.
    ===APP===
    <!DOCTYPE html>…

Everything before the sentinel is conversation. Everything after is the app.
A greeting never reaches the sentinel, so it never produces a version — which
is why "hello" used to fail and now doesn't.

Reasoning models (DeepSeek R1) also emit `reasoning_content` on a separate
field. That's streamed as its own channel, so the thinking is visible without
being confused for the reply.
"""

import re
from typing import Iterator

from anthropic import Anthropic
from openai import OpenAI

from models import User
from providers import PROVIDERS, ModelSpec, api_key_for, find_model

SENTINEL = "===APP==="

SYSTEM = f"""You are an agent that builds small web apps through conversation.

HOW YOU REPLY
Always speak to the person first, in plain prose. Two or three sentences. Say
what you understood and what you're about to build, or ask a question if the
request is genuinely unclear.

If they are greeting you, chatting, or asking a question — just reply. Do not
build anything. Do not emit the sentinel.

WHEN YOU BUILD
Only when the person has asked for an app, or asked to change the existing one,
end your prose reply, then output a line containing exactly:

{SENTINEL}

…followed by ONE complete HTML document and nothing else.

THE APP
- Start with <!DOCTYPE html>. Inline all CSS in <style> and all JS in <script>.
- No external files, no imports, no CDN <script src>, no network calls, no remote images.
- It must actually work: real state, real event handlers, real interactivity.
- Make it look considered. Deliberate type, spacing and colour. Not a wireframe.
- Put the entire <style> block in <head>, before any content.
- Keep it under ~400 lines.
"""

_FENCE = re.compile(r"```(?:html)?\s*(.*?)```", re.S | re.I)
_START = re.compile(r"<!DOCTYPE html|<html", re.I)
_VALID = re.compile(r"^\s*(<!DOCTYPE html|<html)", re.I)


class AgentError(Exception):
    """Message is written for the user; the UI shows it verbatim."""


def _extract_html(raw: str) -> str:
    s = raw.strip()
    if m := _FENCE.search(s):
        s = m.group(1).strip()
    if m := _START.search(s):
        s = s[m.start():]
    return s.strip()


def _looks_like_html(s: str) -> bool:
    return bool(_VALID.match(s)) and "</html>" in s.lower()


def _history_messages(turns: list[dict], previous_html: str | None) -> list[dict]:
    """Rebuild the conversation for the model.

    Prior apps are summarised, not replayed — sending three full HTML documents
    back would burn the context window for no benefit. Only the *current* app is
    sent in full, and only when there is one to revise.
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

    if previous_html:
        msgs.append({
            "role": "user",
            "content": f"The current app source is:\n\n{previous_html}",
        })
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
            model=spec.model, max_tokens=8000, system=SYSTEM, messages=messages
        ) as stream:
            for text in stream.text_stream:
                yield ("content", text)
        return

    client = OpenAI(api_key=key, base_url=PROVIDERS[spec.provider].base_url)
    stream = client.chat.completions.create(
        model=spec.model,
        max_tokens=8000,
        stream=True,
        messages=[{"role": "system", "content": SYSTEM}, *messages],
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
            model=spec.model, max_tokens=8000, system=SYSTEM + extra, messages=messages
        )
        return "".join(b.text for b in res.content if b.type == "text")

    client = OpenAI(api_key=key, base_url=PROVIDERS[spec.provider].base_url)
    res = client.chat.completions.create(
        model=spec.model,
        max_tokens=8000,
        messages=[{"role": "system", "content": SYSTEM + extra}, *messages],
    )
    return res.choices[0].message.content or ""


def converse(
    model_id: str,
    user: User,
    prompt: str,
    turns: list[dict],
    previous_html: str | None = None,
) -> Iterator[tuple[str, str]]:
    """Stream a turn.

    Yields, in order:
      ("reason", text)   the model's thinking, if it exposes any
      ("chat", text)     the prose reply, token by token
      ("code", text)     the app, token by token — only if the model built one
      ("retry", "")      the app came back malformed; retrying strictly
      ("chat_done", full_prose)
      ("code_done", full_html)   omitted entirely on a chat-only turn
    """
    spec = find_model(model_id, user)
    if spec is None:
        raise AgentError("That model isn't available. Pick another.")

    messages = [*_history_messages(turns, previous_html), {"role": "user", "content": prompt}]

    chat_parts: list[str] = []
    code_parts: list[str] = []
    in_code = False
    hold = ""  # the sentinel can arrive split across chunks

    try:
        for kind, text in _stream_call(spec, user, messages):
            if kind == "reason":
                yield ("reason", text)
                continue

            if in_code:
                code_parts.append(text)
                yield ("code", text)
                continue

            hold += text
            if SENTINEL in hold:
                before, after = hold.split(SENTINEL, 1)
                if before:
                    chat_parts.append(before)
                    yield ("chat", before)
                in_code = True
                hold = ""
                after = after.lstrip("\n")
                if after:
                    code_parts.append(after)
                    yield ("code", after)
                continue

            # Emit everything that cannot still turn out to be the sentinel.
            keep = len(SENTINEL) - 1
            if len(hold) > keep:
                emit, hold = hold[:-keep], hold[-keep:]
                chat_parts.append(emit)
                yield ("chat", emit)

    except AgentError:
        raise
    except Exception as e:
        raise AgentError(f"{spec.label} failed: {e}") from e

    if hold and not in_code:
        chat_parts.append(hold)
        yield ("chat", hold)

    chat = "".join(chat_parts).strip()

    # No sentinel: this was a conversation. Nothing to build, nothing to save.
    if not in_code:
        yield ("chat_done", chat or "…")
        return

    html = _extract_html("".join(code_parts))

    if not _looks_like_html(html):
        yield ("retry", "")
        try:
            raw = _non_streaming(
                spec,
                user,
                messages,
                "\n\nYour last reply was rejected. Reply with the prose, then the "
                f"{SENTINEL} line, then the raw HTML document ONLY.",
            )
            _, _, tail = raw.partition(SENTINEL)
            html = _extract_html(tail or raw)
        except Exception as e:
            raise AgentError(f"{spec.label} failed: {e}") from e

    if not _looks_like_html(html):
        raise AgentError(
            f"{spec.label} didn't return a usable HTML document. Try again or switch models."
        )

    yield ("chat_done", chat or "Built it.")
    yield ("code_done", html)


def title_from(prompt: str) -> str:
    t = " ".join(prompt.split())
    return t[:48] + "\u2026" if len(t) > 48 else t
