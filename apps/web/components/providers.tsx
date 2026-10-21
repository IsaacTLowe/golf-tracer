"use client";

import { createApiClient, type ApiClient } from "@golf-tracer/api-client";
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useClipUpload, type ClipUpload } from "@/hooks/useClipUpload";
import type { AppConfig } from "@/lib/config";

interface AppContextValue {
  config: AppConfig;
  supabase: SupabaseClient;
  api: ApiClient;
  session: Session | null;
  /** False until the stored session has been read. */
  authReady: boolean;
  getAccessToken: () => Promise<string | null>;
}

const AppContext = createContext<AppContextValue | null>(null);
const UploadContext = createContext<ClipUpload | null>(null);
const ToastContext = createContext<((message: string, kind?: Toast["kind"]) => void) | null>(null);

interface Toast {
  id: number;
  message: string;
  kind: "info" | "error" | "success";
}

export function Providers({ config, children }: { config: AppConfig; children: ReactNode }) {
  const supabase = useMemo(
    () =>
      createClient(config.supabaseUrl, config.supabaseAnonKey, {
        auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      }),
    [config.supabaseUrl, config.supabaseAnonKey],
  );
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(false);

  useEffect(() => {
    let alive = true;
    supabase.auth.getSession().then(({ data }) => {
      if (!alive) return;
      setSession(data.session);
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s);
      setAuthReady(true);
    });
    return () => {
      alive = false;
      data.subscription.unsubscribe();
    };
  }, [supabase]);

  const getAccessToken = useCallback(async () => {
    // getSession refreshes an expired token before returning it.
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  }, [supabase]);

  const api = useMemo(() => createApiClient({ baseUrl: config.apiUrl, getAccessToken }), [config.apiUrl, getAccessToken]);

  const value = useMemo(
    () => ({ config, supabase, api, session, authReady, getAccessToken }),
    [config, supabase, api, session, authReady, getAccessToken],
  );

  return (
    <AppContext.Provider value={value}>
      <ToastHost>
        <UploadHost>{children}</UploadHost>
      </ToastHost>
    </AppContext.Provider>
  );
}

function UploadHost({ children }: { children: ReactNode }) {
  const { api, config, getAccessToken } = useApp();
  const upload = useClipUpload({ api, supabaseAnonKey: config.supabaseAnonKey, getAccessToken });
  return <UploadContext.Provider value={upload}>{children}</UploadContext.Provider>;
}

function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const push = useCallback((message: string, kind: Toast["kind"] = "info") => {
    const id = next.current++;
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            className={`pointer-events-auto max-w-md rounded-lg px-4 py-3 text-sm shadow-lg ${
              t.kind === "error" ? "bg-danger text-white" : t.kind === "success" ? "bg-accent text-accent-fg" : "bg-foreground text-background"
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useApp(): AppContextValue {
  const v = useContext(AppContext);
  if (!v) throw new Error("useApp must be used inside <Providers>");
  return v;
}

export function useUploads(): ClipUpload {
  const v = useContext(UploadContext);
  if (!v) throw new Error("useUploads must be used inside <Providers>");
  return v;
}

export function useToast() {
  const v = useContext(ToastContext);
  if (!v) throw new Error("useToast must be used inside <Providers>");
  return v;
}
