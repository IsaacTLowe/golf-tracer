"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { useApp } from "@/components/providers";
import { afterSignIn } from "@/lib/onboarding";

// OAuth and email-link redirects land here. supabase-js (PKCE, detectSessionInUrl) exchanges
// the `code` for a session on load; we just wait for it.
function Callback() {
  const { session, authReady } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const [timedOut, setTimedOut] = useState(false);
  const errorDescription = params.get("error_description");

  useEffect(() => {
    if (authReady && session) router.replace(afterSignIn(params.get("next")));
  }, [authReady, session, router, params]);

  useEffect(() => {
    const t = setTimeout(() => setTimedOut(true), 10000);
    return () => clearTimeout(t);
  }, []);

  if (errorDescription || timedOut) {
    return (
      <div className="mx-auto flex max-w-sm flex-1 flex-col items-center justify-center gap-4 px-4 text-center">
        <p className="text-sm text-danger">{errorDescription ?? "Signing in took too long."}</p>
        <a href="/sign-in" className="btn">
          Back to sign in
        </a>
      </div>
    );
  }
  return <div className="flex flex-1 items-center justify-center text-sm text-muted">Signing you in…</div>;
}

export default function AuthCallbackPage() {
  return (
    <Suspense>
      <Callback />
    </Suspense>
  );
}
