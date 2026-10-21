export const TIPS: { title: string; body: string }[] = [
  {
    title: "Film from behind, looking down the target line",
    body: "Camera behind the golfer, roughly hand height, pointing at the target. Keep the ball at address visible in the lower part of the frame. This is the one thing the tracer needs.",
  },
  { title: "Keep the phone still", body: "Use a tripod or prop the phone up. Handheld clips work, but trace less reliably." },
  { title: "Use a high frame rate", body: "60 fps at minimum; 120 or 240 fps is better." },
  { title: "Leave sky above the golfer, and don't zoom", body: "The ball climbs fast; give it room in the frame." },
  {
    title: "Start early, stop late",
    body: "Start recording a second or two before the swing and stop once the ball lands or disappears. One shot per clip.",
  },
];

export function RecordingTips() {
  return (
    <ol className="flex flex-col gap-3">
      {TIPS.map((t, i) => (
        <li key={t.title} className="card flex gap-3 p-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent font-bold text-accent-fg">{i + 1}</span>
          <div>
            <h3 className="font-semibold">{t.title}</h3>
            <p className="text-sm text-muted">{t.body}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
