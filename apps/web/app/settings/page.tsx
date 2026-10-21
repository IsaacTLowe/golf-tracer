"use client";

import { unwrap, type Profile } from "@golf-tracer/api-client";
import { DEFAULT_STYLE, type TracerStyle } from "@golf-tracer/tracer-spec";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { useApp, useToast } from "@/components/providers";
import { StylePanel } from "@/components/StylePanel";

export default function SettingsPage() {
  const { api, session } = useApp();
  const toast = useToast();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [name, setName] = useState("");
  const [style, setStyle] = useState<TracerStyle>(DEFAULT_STYLE);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!session) return;
    unwrap(api.GET("/v1/me"))
      .then((p) => {
        setProfile(p);
        setName(p.display_name ?? "");
        if (p.default_tracer_style) setStyle(p.default_tracer_style as TracerStyle);
      })
      .catch((e: Error) => toast(e.message, "error"));
  }, [api, session, toast]);

  const save = async () => {
    setBusy(true);
    try {
      const p = await unwrap(api.PATCH("/v1/me", { body: { display_name: name || null, default_tracer_style: style } }));
      setProfile(p);
      toast("Settings saved", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="mx-auto flex max-w-lg flex-col gap-4">
        <h1 className="text-2xl font-bold">Settings</h1>
        <section className="card flex flex-col gap-3 p-4">
          <div className="text-sm text-muted">Signed in as {session?.user.email}</div>
          <label className="flex flex-col gap-1 text-sm">
            Display name
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
          </label>
        </section>
        <section className="card flex flex-col gap-3 p-4">
          <h2 className="font-semibold">Default tracer style</h2>
          <p className="text-sm text-muted">Used for new tracers. You can still change the style per clip.</p>
          <div className="flex h-24 items-center justify-center rounded-lg bg-gradient-to-b from-sky-400 to-emerald-700">
            <div
              className="h-16 w-40 rounded-t-full border-x-0 border-b-0"
              style={{
                borderTop: `${Math.max(2, style.width * 400)}px solid ${style.color}`,
                filter: style.glow ? `drop-shadow(0 0 ${style.glow * 8}px ${style.color})` : undefined,
              }}
              aria-hidden
            />
          </div>
          <StylePanel style={style} onChange={(p) => setStyle((s) => ({ ...s, ...p }))} />
        </section>
        <button type="button" className="btn-primary" onClick={save} disabled={busy || !profile}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </AppShell>
  );
}
