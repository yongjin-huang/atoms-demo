import { NextResponse } from "next/server";
import { apiFetch, normalise } from "@/lib/api";

export async function GET() {
  const { status, data } = await apiFetch("/settings");
  return NextResponse.json(status >= 400 ? normalise(data) : data, { status });
}

export async function PUT(req: Request) {
  const body = await req.text();
  const { status, data } = await apiFetch("/settings", { method: "PUT", body });
  return NextResponse.json(status >= 400 ? normalise(data) : data, { status });
}
