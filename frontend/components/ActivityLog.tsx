export interface Activity {
  message: string;
  tone: "info" | "ok" | "warn";
  ts: number;
}

const toneClass: Record<Activity["tone"], string> = {
  info: "text-muted",
  ok: "text-fg font-medium",
  warn: "text-amber-600 dark:text-amber-400",
};

export default function ActivityLog({ activity }: { activity: Activity[] }) {
  return (
    <section className="rounded-xl border border-edge bg-panel p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-subtle">Activity</h2>
      {activity.length === 0 ? (
        <p className="text-sm text-subtle">No activity yet.</p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {activity.map((a, i) => (
            <li key={i} className="flex gap-2">
              <span className="font-mono text-xs text-subtle">
                {new Date(a.ts).toLocaleTimeString([], { hour12: false })}
              </span>
              <span className={toneClass[a.tone]}>{a.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
