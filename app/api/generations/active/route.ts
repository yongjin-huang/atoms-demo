import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

export async function GET() {
  const { status, data } = await apiFetch("/generations/active");
  if (status === 401) return NextResponse.json([]); // first paint, signed out
  return NextResponse.json(data, { status });
}
