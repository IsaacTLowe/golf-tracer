const KEY = "gt.onboarded";

export function isOnboarded(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return true;
  }
}

export function markOnboarded() {
  try {
    localStorage.setItem(KEY, "1");
  } catch {
    /* ignore */
  }
}

/** Where to go after signing in: the recording-tips onboarding once, then the requested page. */
export function afterSignIn(next: string | null): string {
  const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/library";
  return isOnboarded() ? target : `/tips?onboarding=1&next=${encodeURIComponent(target)}`;
}
