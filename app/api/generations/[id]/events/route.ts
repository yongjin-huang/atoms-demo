import { NextResponse } from "next/server";
import { apiStream } from "@/lib/api";

export const maxDuration = 300;
export const dynamic = "force-dynamic";

// Replay-then-follow SSE. `since` resumes mid-log after any disconnect.
export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const since = new URL(req.url).searchParams.get("since") ?? "0";
  const upstream = await apiStream(`/generations/${id}/events?since=${since}`);

  if (!upstream) {
    return NextResponse.json({ error: "Sign in to build." }, { status: 401 });
  }
  if (!upstream.ok || !upstream.body) {
    return NextResponse.json({ error: "Generation not found." }, { status: upstream.status });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
