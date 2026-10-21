"use client";

// Review queue: the clips waiting for review (status review or needs_input), stepped
// through one by one. The queue is a snapshot taken when it starts, kept in sessionStorage
// so reloading the review screen keeps your place.

import { unwrap, type ApiClient } from "@golf-tracer/api-client";
import { useCallback, useState } from "react";

const KEY = "gt.reviewQueue";

export function readQueue(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

function writeQueue(ids: string[]) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(ids));
  } catch {
    /* ignore */
  }
}

/** Fetch every clip awaiting review, store the queue, and return the first id (or null). */
export async function startReviewQueue(api: ApiClient, batchId?: string | null): Promise<string | null> {
  const ids: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await unwrap(
      api.GET("/v1/clips", {
        params: { query: { status: ["review,needs_input"], batch_id: batchId ?? undefined, cursor, limit: 100 } },
      }),
    );
    ids.push(...page.items.map((c) => c.id));
    cursor = page.next_cursor ?? undefined;
  } while (cursor && ids.length < 500);
  // Oldest first, so a batch is reviewed in upload order.
  ids.reverse();
  writeQueue(ids);
  return ids[0] ?? null;
}

export function useReviewQueue(currentId: string) {
  const [ids, setIds] = useState<string[]>(readQueue);
  const index = ids.indexOf(currentId);
  const inQueue = index >= 0;

  const remove = useCallback((id: string) => {
    const next = readQueue().filter((x) => x !== id);
    writeQueue(next);
    setIds(next);
  }, []);

  return {
    inQueue,
    position: inQueue ? index + 1 : 0,
    total: ids.length,
    prevId: inQueue && index > 0 ? ids[index - 1]! : null,
    nextId: inQueue && index < ids.length - 1 ? ids[index + 1]! : null,
    remove,
  };
}
