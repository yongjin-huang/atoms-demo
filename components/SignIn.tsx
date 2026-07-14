import { signIn } from "@/auth";
import "./workbench.css";

export default function SignIn() {
  return (
    <div className="gate">
      <div className="gate-card">
        <h1>atoms<span>.</span>demo</h1>
        <p>
          Describe an app in a sentence. An agent writes it, it runs immediately, and every
          revision is kept. Sign in to start building.
        </p>
        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/" });
          }}
        >
          <button type="submit">Continue with Google</button>
        </form>
      </div>
    </div>
  );
}
