import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

/**
 * Auth.js keeps no database of its own now.
 *
 * With `strategy: "jwt"` and no adapter, the Google `sub` claim lands in
 * token.sub and rides in a signed httpOnly cookie. We surface it as
 * session.user.id and forward it to the Python service, which owns the users
 * table. One schema, one migration tool, one language.
 */

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Google],
  session: { strategy: "jwt" },
  callbacks: {
    jwt({ token, account }) {
      // Google's `sub` — stable for this user, forever. Without this, Auth.js
      // mints a random id per sign-in and every login looks like a new person.
      if (account?.provider === "google" && account.providerAccountId) {
        token.sub = account.providerAccountId;
      }
      return token;
    },
    session({ session, token }) {
      if (token.sub) session.user.id = token.sub;
      return session;
    },
  },
});
