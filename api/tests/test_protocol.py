"""Protocol v2 parser tests. Pure Python — no SDKs, no database, no network."""

import pytest

from protocol import (
    MAX_FILES,
    Parser,
    ProtocolError,
    merge_files,
    parse_manifest,
    validate_path,
)

BUILD = (
    "Sure — a counter app.\n"
    "===MANIFEST===\n"
    '{"template": "static", "delete": []}\n'
    "===FILE index.html===\n"
    "<!DOCTYPE html>\n"
    "<html><body>hi</body></html>\n"
    "===END===\n"
)


def run(chunks):
    """Feed chunks, return (parser, events)."""
    p = Parser()
    events = []
    for c in chunks:
        events.extend(p.feed(c))
    events.extend(p.finish())
    return p, events


# --- happy paths -----------------------------------------------------------


def test_chat_only_turn():
    p, events = run(["Hello! What should we build today?\n"])
    assert not p.ended and p.manifest is None and p.files == {}
    assert p.chat == "Hello! What should we build today?"
    assert all(kind == "chat" for kind, _ in events)


def test_full_build_single_chunk():
    p, events = run([BUILD])
    assert p.ended
    assert p.manifest.template.id == "static"
    assert p.files["index.html"].startswith("<!DOCTYPE html>")
    kinds = [k for k, _ in events]
    assert kinds == ["chat", "manifest", "file_open", "file", "file", "file_close"]


def test_directive_split_across_chunks():
    # The worst case: every boundary lands mid-directive.
    chunks = [BUILD[i : i + 3] for i in range(0, len(BUILD), 3)]
    p, _ = run(chunks)
    assert p.ended and "index.html" in p.files


def test_end_without_trailing_newline():
    p, _ = run([BUILD.rstrip("\n")])  # stream stops right at ===END===
    assert p.ended


def test_multiple_files():
    text = (
        "Two files.\n"
        "===MANIFEST===\n"
        '{"template": "static"}\n'
        "===FILE index.html===\n"
        "<html></html>\n"
        "===FILE notes.txt===\n"
        "hello\n"
        "===END===\n"
    )
    p, _ = run([text])
    assert set(p.files) == {"index.html", "notes.txt"}


def test_content_after_end_is_ignored():
    p, _ = run([BUILD + "trailing junk the model added\n"])
    assert p.ended
    assert "trailing" not in p.chat and "trailing" not in p.files["index.html"]


def test_file_content_preserves_directive_lookalikes():
    # "=== nearly a directive" inside a file must be kept as content.
    text = (
        "ok\n===MANIFEST===\n"
        '{"template": "static"}\n'
        "===FILE index.html===\n"
        "== not a directive ==\n"
        "===END=== <- inline, not a whole line? no: regex anchors\n"
        "===END===\n"
    )
    p, _ = run([text])
    assert p.ended
    assert "== not a directive ==" in p.files["index.html"]
    assert "inline" in p.files["index.html"]


# --- protocol violations -----------------------------------------------------


def test_missing_end_raises():
    p = Parser()
    list(p.feed("hi\n===MANIFEST===\n{\"template\": \"static\"}\n===FILE index.html===\n<html>\n"))
    with pytest.raises(ProtocolError, match="END"):
        list(p.finish())


def test_file_before_manifest_raises():
    p = Parser()
    with pytest.raises(ProtocolError, match="MANIFEST"):
        list(p.feed("hi\n===FILE index.html===\n"))


def test_bad_manifest_json_raises():
    p = Parser()
    with pytest.raises(ProtocolError, match="JSON"):
        list(p.feed("hi\n===MANIFEST===\nnot json\n===FILE a.txt===\n"))


def test_disabled_template_rejected():
    with pytest.raises(ProtocolError, match="Unknown template"):
        parse_manifest('{"template": "vite-react"}')


def test_duplicate_file_raises():
    p = Parser()
    text = (
        "x\n===MANIFEST===\n{\"template\": \"static\"}\n"
        "===FILE index.html===\na\n"
        "===FILE index.html===\nb\n"
    )
    with pytest.raises(ProtocolError, match="twice"):
        list(p.feed(text))


def test_build_with_no_files_raises():
    p = Parser()
    with pytest.raises(ProtocolError, match="at least one file"):
        list(p.feed("x\n===MANIFEST===\n{\"template\": \"static\"}\n===END===\n"))


# --- paths -------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs", "a//b", "a\\b", "", ".hidden", "a" * 201])
def test_bad_paths_rejected(bad):
    with pytest.raises(ProtocolError):
        validate_path(bad)


@pytest.mark.parametrize("good", ["index.html", "src/App.jsx", "a-b_c.d/e.f"])
def test_good_paths_accepted(good):
    assert validate_path(good) == good


# --- merge -------------------------------------------------------------------


def test_merge_is_copy_on_write():
    m = parse_manifest('{"template": "static", "delete": ["old.css"]}')
    previous = {"index.html": "<old>", "old.css": "gone", "keep.js": "kept"}
    merged = merge_files(m, {"index.html": "<new>"}, previous)
    assert merged == {"index.html": "<new>", "keep.js": "kept"}


def test_merge_enforces_required_files():
    m = parse_manifest('{"template": "static", "delete": ["index.html"]}')
    with pytest.raises(ProtocolError, match="requires"):
        merge_files(m, {"other.txt": "x"}, {"index.html": "<html>"})


def test_merge_enforces_file_count():
    m = parse_manifest('{"template": "static"}')
    too_many = {f"f{i}.txt": "x" for i in range(MAX_FILES)} | {"index.html": "x"}
    with pytest.raises(ProtocolError, match="Too many"):
        merge_files(m, too_many, None)
