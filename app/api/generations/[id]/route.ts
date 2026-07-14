import { NextResponse } from "next/server";
import { apiFetch, normalise } from "@/lib/api";

export async function DELETE(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const { status, data } = await apiFetch(`/generations/${id}`, { method: "DELETE" });
  return NextResponse.json(status >= 400 ? normalise(data) : data, { status });
}
