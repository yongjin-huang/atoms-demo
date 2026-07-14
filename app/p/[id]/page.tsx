import { auth, signOut } from "@/auth";
import { notFound } from "next/navigation";
import { apiFetch } from "@/lib/api";
import Workbench from "@/components/Workbench";
import SignIn from "@/components/SignIn";

export const dynamic = "force-dynamic";

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await auth();
  if (!session?.user?.id) return <SignIn />;

  const { id } = await params;
  const { status, data } = await apiFetch(`/projects/${id}`);
  if (status === 404) notFound();
  if (status >= 400) throw new Error("Could not load this build.");

  const d = data as { versions: never[]; messages: never[] };

  async function doSignOut() {
    "use server";
    await signOut({ redirectTo: "/" });
  }

  return (
    <Workbench
      user={session.user}
      projectId={id}
      initialVersions={d.versions ?? []}
      initialMessages={d.messages ?? []}
      signOutAction={doSignOut}
    />
  );
}
