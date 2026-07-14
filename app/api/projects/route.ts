import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

export async function GET() {
  const { status, data } = await apiFetch("/projects");
  // The rail asks for this before the user has signed in on first paint.
  // An empty list is a truthful answer, and keeps the client simple.
  if (status === 401) return NextResponse.json([]);
  return NextResponse.json(data, { status });
}
