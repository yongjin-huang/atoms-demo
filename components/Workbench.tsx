"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { renderablePartial } from "@/lib/preview";
import "./workbench.css";

type Version = { id: string; n: number; prompt: string; html: string; modelId: string };
type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string | null;
  modelId?: string | null;
  versionId?: string | null;
};
type Project = { id: string; title: string };
type Model = { id: string; label: string; provider: string };
type User = { name?: string | null; image?: string | null };

export default function Workbench({
  user,
  projectId,
  initialVersions = [],
  initialMessages = [],
  signOutAction,
}: {
  user: User;
  projectId?: string;
  initialVersions?: Version[];
  initialMessages?: Message[];
  signOutAction: () => Promise<void>;
}) {
  const router = useRouter();

  const [history, setHistory] = useState<Project[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [modelId, setModelId] = useState("");
  const [versions, setVersions] = useState<Version[]>(initialVersions);
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [activeN, setActiveN] = useState(initialVersions.at(-1)?.n ?? 0);
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"preview" | "source">("preview");
  const [openThoughts, setOpenThoughts] = useState<Record<string, boolean>>({});

  // live turn
  const [working, setWorking] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [liveReason, setLiveReason] = useState("");
  const [liveChat, setLiveChat] = useState("");
  const [liveCode, setLiveCode] = useState("");
  const [reformatting, setReformatting] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);

  const turnsRef = useRef<HTMLDivElement>(null);
  const codeRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    fetch("/api/projects")
      .then((r) => r.json())
      .then((d) => setHistory(Array.isArray(d) ? d : []))
      .catch(() => setHistory([]));
  }, [projectId]);

  useEffect(() => {
    fetch("/api/models")
      .then((r) => r.json())
      .then((d) => {
        setModels(Array.isArray(d?.models) ? d.models : []);
        setModelId(d?.default ?? "");
      })
      .catch(() => setModels([]));
  }, []);

  useEffect(() => {
    turnsRef.current?.scrollTo({ top: turnsRef.current.scrollHeight });
  }, [messages.length, pending, liveChat, liveReason]);

  useEffect(() => {
    if (working) codeRef.current?.scrollTo({ top: codeRef.current.scrollHeight });
  }, [liveCode, working]);

  // Refresh the preview from the partial document, but not on every token —
  // an iframe reload per token would flicker and burn the main thread.
  useEffect(() => {
    if (!liveCode) return;
    const t = setTimeout(() => setPreview(renderablePartial(liveCode)), 400);
    return () => clearTimeout(t);
  }, [liveCode]);

  const active = versions.find((v) => v.n === activeN);
  const labelFor = (id?: string | null) =>
    (id && models.find((m) => m.id === id)?.label) || id || "";
  const versionFor = (vid?: string | null) => versions.find((v) => v.id === vid);

  async function send() {
    const text = prompt.trim();
    if (!text || working) return;

    setWorking(true);
    setError(null);
    setPending(text);
    setPrompt("");
    setLiveReason("");
    setLiveChat("");
    setLiveCode("");
    setPreview(null);
    setReformatting(false);

    try {
      const res = await fetch("/api/generate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text, projectId, modelId }),
      });

      if (!res.ok || !res.body) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.error ?? "That didn't work.");
      }

      // SSE by hand: EventSource can't POST, and we need a request body.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let done: { projectId: string; message: Message; version: Version | null } | null = null;
      let sawCode = false;

      while (true) {
        const { value, done: finished } = await reader.read();
        if (finished) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          const evt = /^event: (.+)$/m.exec(frame)?.[1];
          const raw = /^data: (.*)$/m.exec(frame)?.[1];
          if (!evt || raw === undefined) continue;
          const data = JSON.parse(raw || "{}");

          if (evt === "reason") setLiveReason((s) => s + data.text);
          else if (evt === "chat") setLiveChat((s) => s + data.text);
          else if (evt === "code") {
            if (!sawCode) {
              sawCode = true;
              setTab("source"); // it started writing — show the code
            }
            setLiveCode((s) => s + data.text);
          } else if (evt === "retry") setReformatting(true);
          else if (evt === "error") throw new Error(data.error);
          else if (evt === "done") done = data;
        }
      }

      if (!done) throw new Error("The connection dropped before the reply finished.");

      if (!projectId) {
        router.push(`/p/${done.projectId}`);
        return;
      }

      setMessages((ms) => [
        ...ms,
        { id: `${done.message.id}-u`, role: "user", content: text },
        done.message,
      ]);
      if (done.version) {
        setVersions((vs) => [...vs, done.version!]);
        setActiveN(done.version.n);
        setTab("preview");
      }
      setPending(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something broke.");
      setPrompt(text); // don't make them retype it
      setPending(null);
    } finally {
      setWorking(false);
      setReformatting(false);
      setPreview(null);
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
  }

  const empty = messages.length === 0 && !pending;

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">atoms<span>.</span>demo</div>
        <div className="brand-sub">talk → build → run</div>

        <div className="rail-label">Your builds</div>
        {history.length === 0 ? (
          <p className="rail-empty">Nothing yet. Your conversations will collect here.</p>
        ) : (
          history.map((p) => (
            <a key={p.id} href={`/p/${p.id}`} className={`rail-item ${p.id === projectId ? "on" : ""}`}>
              {p.title}
            </a>
          ))
        )}
        <a href="/" className="rail-item rail-new">+ New conversation</a>

        <div className="who">
          {user.image && <img src={user.image} alt="" />}
          <span className="who-name">{user.name ?? "Signed in"}</span>
          <form action={signOutAction}>
            <button type="submit" className="who-out">Sign out</button>
          </form>
        </div>
      </aside>

      <div className="work">
        {/* ---------------- conversation ---------------- */}
        <section className="convo">
          <div className="convo-head">
            <h1 className="h1">{projectId ? "Conversation" : "New conversation"}</h1>
            <span className={`status ${active && !working ? "live" : ""}`}>
              {working ? "thinking…" : active ? `v${active.n} · running` : "idle"}
            </span>
          </div>

          <div className="turns" ref={turnsRef}>
            {empty ? (
              <p className="turns-empty">
                Say hello, or describe an app.
                <br />
                It builds when you ask it to — not before.
              </p>
            ) : (
              <>
                {messages.map((m) => {
                  if (m.role === "user") {
                    return <div className="said" key={m.id}>{m.content}</div>;
                  }
                  const v = versionFor(m.versionId);
                  const open = !!openThoughts[m.id];
                  return (
                    <div className="reply" key={m.id}>
                      {m.reasoning && (
                        <div className="thought">
                          <button
                            className="thought-head"
                            onClick={() => setOpenThoughts((s) => ({ ...s, [m.id]: !open }))}
                          >
                            <span className="chev">{open ? "▾" : "▸"}</span> Thought for a moment
                          </button>
                          {open && <pre className="thought-body">{m.reasoning}</pre>}
                        </div>
                      )}
                      <div className="said-back">{m.content}</div>
                      {v && (
                        <button
                          className={`built ${v.n === activeN ? "on" : ""}`}
                          onClick={() => {
                            setActiveN(v.n);
                            setTab("preview");
                          }}
                        >
                          <span className="built-v">Built v{v.n}</span>
                          <span className="built-meta">{labelFor(m.modelId)}</span>
                        </button>
                      )}
                    </div>
                  );
                })}

                {pending && (
                  <>
                    <div className="said">{pending}</div>
                    <div className="reply">
                      {liveReason && (
                        <div className="thought open">
                          <div className="thought-head static">
                            <span className="pulse" /> Thinking
                          </div>
                          <pre className="thought-body live">{liveReason}</pre>
                        </div>
                      )}
                      {liveChat ? (
                        <div className="said-back">
                          {liveChat}
                          <span className="caret-inline" />
                        </div>
                      ) : (
                        !liveReason && (
                          <div className="said-back muted">
                            <span className="pulse" /> …
                          </div>
                        )
                      )}
                      {liveCode && (
                        <div className="built pending">
                          <span className="built-v">
                            <span className="pulse" />
                            {reformatting ? "Reformatting…" : "Writing the app…"}
                          </span>
                          <span className="built-meta">
                            {liveCode.length.toLocaleString()} chars
                          </span>
                        </div>
                      )}
                    </div>
                  </>
                )}
              </>
            )}
          </div>

          {error && <div className="error">{error}</div>}

          <div className="prompt">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={onKey}
              disabled={working}
              placeholder={
                empty
                  ? "Say hi, or: a pomodoro timer with a task list…"
                  : "Add a dark mode toggle. Or just ask a question…"
              }
            />
            <div className="prompt-bar">
              <select
                className="picker"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                disabled={models.length === 0 || working}
                aria-label="Model"
              >
                {models.length === 0 ? (
                  <option>No provider configured</option>
                ) : (
                  models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.provider} · {m.label}
                    </option>
                  ))
                )}
              </select>
              <span className="hint">⌘↵</span>
              <button className="build" onClick={send} disabled={working || !prompt.trim() || !modelId}>
                {working ? "…" : "Send"}
              </button>
            </div>
          </div>
        </section>

        {/* ---------------- stage ---------------- */}
        <section className="stage">
          <div className="stage-head">
            <span className="stage-title">
              {working && liveCode
                ? preview
                  ? "live preview · still writing"
                  : "waiting for the stylesheet…"
                : active
                  ? `v${active.n} · ${labelFor(active.modelId)}`
                  : "nothing running"}
            </span>
            <div className="tabs">
              <button className={`tab ${tab === "preview" ? "on" : ""}`} onClick={() => setTab("preview")}>
                Preview
              </button>
              <button className={`tab ${tab === "source" ? "on" : ""}`} onClick={() => setTab("source")}>
                Source
              </button>
            </div>
          </div>

          {tab === "source" ? (
            <pre className={`source ${working ? "streaming" : ""}`} ref={codeRef}>
              {working ? liveCode : active?.html ?? ""}
              {working && <span className="caret" />}
            </pre>
          ) : (
            <div className="bed">
              <span className="crop-bl" />
              <span className="crop-br" />
              {working && liveCode ? (
                preview ? (
                  <iframe
                    className="frame"
                    srcDoc={preview}
                    sandbox="allow-scripts allow-forms allow-modals"
                    title="Live preview"
                  />
                ) : (
                  <div className="bed-empty">
                    <h2>Laying out the stylesheet</h2>
                    <p>
                      Holding the preview until the styles are complete — rendering
                      half a stylesheet just shows you a broken page.
                    </p>
                  </div>
                )
              ) : active ? (
                <iframe
                  key={active.id}
                  className="frame"
                  srcDoc={active.html}
                  sandbox="allow-scripts allow-forms allow-modals"
                  title={`Version ${active.n}`}
                />
              ) : (
                <div className="bed-empty">
                  <h2>Nothing running</h2>
                  <p>Ask for an app and it starts here. Ask a question and it just answers.</p>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
