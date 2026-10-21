"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useApp, useUploads } from "@/components/providers";

/** Signed-in layout: header with navigation; redirects to sign-in when there's no session. */
export function AppShell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  const { session, authReady, supabase, config } = useApp();
  const { active } = useUploads();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (authReady && !session) router.replace(`/sign-in?next=${encodeURIComponent(pathname)}`);
  }, [authReady, session, router, pathname]);

  if (!authReady || !session) {
    return <div className="flex flex-1 items-center justify-center text-sm text-muted">Loading…</div>;
  }

  const nav = [
    { href: "/library", label: "Library" },
    { href: "/tips", label: "Tips" },
    { href: "/settings", label: "Settings" },
  ];

  return (
    <div className="flex flex-1 flex-col">
      <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur">
        <div className={`mx-auto flex items-center gap-2 px-4 ${wide ? "max-w-7xl" : "max-w-6xl"}`}>
          <Link href="/library" className="tap flex shrink-0 items-center gap-2 whitespace-nowrap font-bold" aria-label={config.appName}>
            <span className="inline-block h-3 w-3 rounded-full bg-accent" aria-hidden />
            <span className="hidden sm:inline">{config.appName}</span>
          </Link>
          <nav className="ml-auto flex items-center gap-0.5 whitespace-nowrap text-sm sm:gap-1">
            {nav.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={`tap flex items-center rounded-lg px-2 sm:px-3 ${pathname.startsWith(n.href) ? "bg-background font-semibold" : ""}`}
              >
                {n.label}
              </Link>
            ))}
            {active > 0 && (
              <span className="hidden rounded-full bg-accent px-2 py-0.5 text-xs font-semibold text-accent-fg sm:inline" title="Uploads in progress">
                ↑ {active}
              </span>
            )}
            <button
              type="button"
              className="tap rounded-lg px-2 sm:px-3"
              onClick={async () => {
                await supabase.auth.signOut();
                router.replace("/sign-in");
              }}
            >
              Sign out
            </button>
          </nav>
        </div>
      </header>
      <main className={`mx-auto w-full flex-1 px-4 py-4 ${wide ? "max-w-7xl" : "max-w-6xl"}`}>{children}</main>
    </div>
  );
}
