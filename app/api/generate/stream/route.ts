import { NextResponse } from "next/server";
import { apiStream } from "@/lib/api";

export const maxDuration = 300;
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = await req.text();
  const upstream = await apiStream("/generate/stream", body);

  if (!upstream) {
    return NextResponse.json({ error: "Sign in to build." }, { status: 401 });
  }
  if (!upstream.ok || !upstream.body) {
    const data = await upstream.json().catch(() => ({}));
    const message =
      typeof data === "object" && data && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : "Generation failed.";
    return NextResponse.json({ error: message }, { status: upstream.status });
  }

  // Hand the body through untouched. Nothing is buffered here — the whole
  // point is that the first token reaches the browser as soon as it exists.
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
