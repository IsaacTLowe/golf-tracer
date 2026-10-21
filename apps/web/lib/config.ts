export interface AppConfig {
  /** Supabase URL as reachable from the browser. */
  supabaseUrl: string;
  supabaseAnonKey: string;
  /** FastAPI base URL as reachable from the browser. */
  apiUrl: string;
  appName: string;
}
