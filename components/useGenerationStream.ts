"use client";

import { useRef, useState } from "react";

/**
 * The client side of server-owned generations. POST /api/generate returns a
 * generationId; this hook subscribes to its event log with replay-then-follow
 * semantics. Disconnects (navigation, refresh, bad Wi-Fi) are survivable:
 * reconnect with since=<last seq> and nothing is missed. Stop is a server
 * cancel, not a closed socket.
 *
 * The hook owns transport only. Every event is handed to onEvent — state
 * stays with the caller.
 */
export function useGenerationStream(onEvent: (evt: string, data: any) => void) {
  const [working, setWorking] = useState(false);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const genRef = useRef<string | null>(null);
  const seqRef = useRef(0);
  const stoppedRef = useRef(false);

  async function start(body: { prompt: string; projectId: string | null; modelId: string }) {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error ?? "That didn't work.");
    await follow(data.generationId, 0);
  }

  /** Reattach to a generation already running server-side. */
  async function resume(generationId: string) {
    await follow(generationId, 0);
  }

  async function follow(id: string, since: number) {
    genRef.current = id;
    seqRef.current = since;
    stoppedRef.current = false;
    setWorking(true);
    try {
      let failures = 0;
      for (;;) {
        try {
          if (await readOnce(id)) return; // terminal event seen
          failures = 0; // stream closed mid-flight (proxy timeout) — resume
        } catch (e) {
          if (stoppedRef.current) return;
          if (++failures > 5) throw e;
        }
        if (stoppedRef.current) return;
        await new Promise((r) => setTimeout(r, 1000));
      }
    } finally {
      setWorking(false);
      genRef.current = null;
    }
  }

  /** One subscription pass. Returns true if a terminal event arrived. */
  async function readOnce(id: string): Promise<boolean> {
    const res = await fetch(`/api/generations/${id}/events?since=${seqRef.current}`);
    if (!res.ok || !res.body) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error ?? "The build service is unreachable.");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        if (frame.startsWith(":")) continue; // keepalive
        const evt = /^event: (.+)$/m.exec(frame)?.[1];
        const seq = /^id: (\d+)$/m.exec(frame)?.[1];
        const raw = /^data: (.*)$/m.exec(frame)?.[1];
        if (!evt || raw === undefined) continue;
        if (seq !== undefined) seqRef.current = Number(seq) + 1;
        onEventRef.current(evt, JSON.parse(raw || "{}"));
        if (evt === "done" || evt === "error" || evt === "stopped") terminal = true;
      }
      if (terminal) {
        reader.cancel().catch(() => {});
        break;
      }
    }
    return terminal;
  }

  /** Cancel on the server. The confirming "stopped" event ends the stream. */
  async function stop() {
    stoppedRef.current = true;
    const id = genRef.current;
    if (id) await fetch(`/api/generations/${id}`, { method: "DELETE" }).catch(() => {});
  }

  return { start, resume, stop, working };
}
