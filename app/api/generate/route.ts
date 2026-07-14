import { NextResponse } from "next/server";
import { apiFetch, normalise } from "@/lib/api";

// Returns { generationId } immediately — the model call runs server-side.
export async function POST(req: Request) {
  const body = await req.text();
  const { status, data } = await apiFetch("/generate", { method: "POST", body });
  return NextResponse.json(status >= 400 ? normalise(data) : data, { status });
}
