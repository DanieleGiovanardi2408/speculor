import { Link } from "react-router-dom";
import type { PostingSummary } from "../api/types";
import { formatShortDate, relativeDays } from "../lib/format";
import { PriorityBadge } from "./Badge";

export function PostingMiniList({ items, showDeadline = false, empty }: { items: PostingSummary[]; showDeadline?: boolean; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-ink-muted">{empty}</p>;
  return (
    <ul className="divide-y divide-line">
      {items.map((p) => (
        <li key={p.id} className="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
          <div className="min-w-0">
            <Link to={`/annunci?open=${p.id}`} className="block truncate text-sm font-medium hover:underline">
              {p.title}
            </Link>
            <p className="truncate text-xs text-ink-muted">
              {p.company_name}
              {p.location && ` · ${p.location}`}
              {showDeadline && p.deadline && (
                <>
                  {" "}
                  · scade <time dateTime={p.deadline}>{formatShortDate(p.deadline)}</time> ({relativeDays(p.deadline)})
                </>
              )}
            </p>
          </div>
          <PriorityBadge priority={p.priority} />
        </li>
      ))}
    </ul>
  );
}
