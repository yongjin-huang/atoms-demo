import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

export async function GET() {
  const { status, data } = await apiFetch("/models");
  if (status >= 400) return NextResponse.json({ models: [], default: null });
  return NextResponse.json(data);
}
