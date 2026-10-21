import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { Providers } from "@/components/providers";
import type { AppConfig } from "@/lib/config";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Golf Tracer", template: "%s · Golf Tracer" },
  description: "Broadcast-style ball tracers for your golf swing clips.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0b1f14",
};

function env(name: string): string | undefined {
  // Indirect lookup so Next doesn't inline values at build time: one image, many environments.
  return process.env[name] || undefined;
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  await connection();
  const config: AppConfig = {
    supabaseUrl: env("SUPABASE_URL") ?? "http://127.0.0.1:54321",
    supabaseAnonKey: env("SUPABASE_ANON_KEY") ?? "",
    apiUrl: env("NEXT_PUBLIC_API_URL") ?? "http://localhost:8000",
    appName: env("APP_NAME") ?? "Golf Tracer",
  };
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <Providers config={config}>{children}</Providers>
      </body>
    </html>
  );
}
