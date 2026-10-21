"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AppShell } from "@/components/AppShell";
import { RecordingTips } from "@/components/RecordingTips";
import { markOnboarded } from "@/lib/onboarding";

function Tips() {
  const params = useSearchParams();
  const router = useRouter();
  const onboarding = params.get("onboarding") === "1";
  const next = params.get("next") ?? "/library";
  return (
    <AppShell>
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <div>
          <h1 className="text-2xl font-bold">{onboarding ? "Before your first upload" : "Recording tips"}</h1>
          <p className="mt-1 text-sm text-muted">
            Only the camera angle is required: film from behind the golfer. The rest makes the tracer more accurate.
          </p>
        </div>
        <figure className="card overflow-hidden">
          <svg viewBox="0 0 320 180" className="h-auto w-full bg-gradient-to-b from-sky-300 to-sky-100" role="img" aria-label="Down-the-line camera position">
            <rect y="110" width="320" height="70" fill="#3f8f47" />
            <path d="M160 108 L150 40" stroke="#ff3b30" strokeWidth="3" fill="none" strokeLinecap="round" />
            <circle cx="160" cy="150" r="4" fill="white" />
            <rect x="110" y="70" width="18" height="80" rx="8" fill="#333" />
            <circle cx="119" cy="60" r="10" fill="#7a5a48" />
            <text x="160" y="172" textAnchor="middle" fontSize="10" fill="white">ball low in frame · plenty of sky</text>
          </svg>
          <figcaption className="p-3 text-xs text-muted">The view you want: behind the golfer, looking at the target.</figcaption>
        </figure>
        <RecordingTips />
        {onboarding && (
          <button
            type="button"
            className="btn-primary self-end"
            onClick={() => {
              markOnboarded();
              router.replace(next);
            }}
          >
            Got it
          </button>
        )}
      </div>
    </AppShell>
  );
}

export default function TipsPage() {
  return (
    <Suspense>
      <Tips />
    </Suspense>
  );
}
