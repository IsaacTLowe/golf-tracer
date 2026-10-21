"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { useApp } from "@/components/providers";
import { afterSignIn } from "@/lib/onboarding";

type Mode = "sign-in" | "sign-up" | "magic";

function SignInForm() {
  const { supabase, session, authReady, config } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next");
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (authReady && session) router.replace(afterSignIn(next));
  }, [authReady, session, router, next]);

  const redirectTo = () => {
    const url = new URL("/auth/callback", window.location.origin);
    if (next) url.searchParams.set("next", next);
    return url.toString();
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "sign-in") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "sign-up") {
        const { data, error } = await supabase.auth.signUp({ email, password, options: { emailRedirectTo: redirectTo() } });
        if (error) throw error;
        if (!data.session) setNotice("Check your email to confirm your account.");
      } else {
        const { error } = await supabase.auth.signInWithOtp({ email, options: { emailRedirectTo: redirectTo() } });
        if (error) throw error;
        setNotice("Check your email for a sign-in link.");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const oauth = async (provider: "google" | "github") => {
    setError(null);
    const { error } = await supabase.auth.signInWithOAuth({ provider, options: { redirectTo: redirectTo() } });
    if (error) setError(error.message);
  };

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
      <div className="text-center">
        <h1 className="text-2xl font-bold">{config.appName}</h1>
        <p className="mt-1 text-sm text-muted">Broadcast-style tracers for your swing clips.</p>
      </div>
      <div className="flex rounded-lg border border-border p-1 text-sm" role="tablist">
        {(
          [
            ["sign-in", "Sign in"],
            ["sign-up", "Create account"],
            ["magic", "Email link"],
          ] as [Mode, string][]
        ).map(([m, label]) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={mode === m}
            className={`tap flex-1 rounded-md ${mode === m ? "bg-accent font-semibold text-accent-fg" : ""}`}
            onClick={() => setMode(m)}
          >
            {label}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        {mode !== "magic" && (
          <label className="flex flex-col gap-1 text-sm">
            Password
            <input
              className="input"
              type="password"
              autoComplete={mode === "sign-up" ? "new-password" : "current-password"}
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="text-sm text-accent">
            {notice}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Please wait…" : mode === "sign-in" ? "Sign in" : mode === "sign-up" ? "Create account" : "Send sign-in link"}
        </button>
      </form>
      <div className="flex items-center gap-3 text-xs text-muted">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>
      <div className="flex flex-col gap-2">
        <button type="button" className="btn" onClick={() => oauth("google")}>
          Continue with Google
        </button>
        <button type="button" className="btn" onClick={() => oauth("github")}>
          Continue with GitHub
        </button>
      </div>
    </div>
  );
}

export default function SignInPage() {
  return (
    <Suspense>
      <SignInForm />
    </Suspense>
  );
}
