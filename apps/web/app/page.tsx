"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useApp } from "@/components/providers";

export default function Home() {
  const { session, authReady } = useApp();
  const router = useRouter();
  useEffect(() => {
    if (authReady) router.replace(session ? "/library" : "/sign-in");
  }, [authReady, session, router]);
  return <div className="flex flex-1 items-center justify-center text-sm text-muted">Loading…</div>;
}
