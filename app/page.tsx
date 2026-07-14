import { auth, signOut } from "@/auth";
import Workbench from "@/components/Workbench";
import SignIn from "@/components/SignIn";

export default async function Home() {
  const session = await auth();
  if (!session?.user) return <SignIn />;

  async function doSignOut() {
    "use server";
    await signOut({ redirectTo: "/" });
  }

  return <Workbench user={session.user} signOutAction={doSignOut} />;
}
