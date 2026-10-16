import type { ReactNode } from "react";
import type { DraftStatus, FilterState, PostingStatus, Priority, UserStatus } from "../api/types";
import { DRAFT_STATUS_LABEL, POSTING_STATUS_LABEL, USER_STATUS_LABEL } from "../lib/labels";
import { Icon, type IconName } from "./Icon";

export type Tone = "neutral" | "accent" | "danger" | "warning" | "success" | "info";

const toneClass: Record<Tone, string> = {
  neutral: "bg-neutral-soft text-neutral-ink border-neutral-ink/15",
  accent: "bg-accent-soft text-accent-ink border-accent/20",
  danger: "bg-danger-soft text-danger-ink border-danger/20",
  warning: "bg-warning-soft text-warning-ink border-warning-ink/20",
  success: "bg-success-soft text-success-ink border-success-ink/20",
  info: "bg-info-soft text-info-ink border-info-ink/20",
};

export function Badge({
  tone = "neutral",
  icon,
  children,
  className = "",
}: {
  tone?: Tone;
  icon?: IconName;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-xs font-medium leading-4 ${toneClass[tone]} ${className}`}
    >
      {icon && <Icon name={icon} size={12} />}
      {children}
    </span>
  );
}

const priorityMeta: Record<Priority, { tone: Tone; icon: IconName }> = {
  Alta: { tone: "danger", icon: "arrowUp" },
  Media: { tone: "warning", icon: "minus" },
  Bassa: { tone: "neutral", icon: "arrowDown" },
};

export function PriorityBadge({ priority }: { priority: Priority | null }) {
  if (!priority)
    return (
      <Badge tone="neutral" icon="circle">
        Non valutato
      </Badge>
    );
  const m = priorityMeta[priority];
  return (
    <Badge tone={m.tone} icon={m.icon}>
      <span className="sr-only">Priorità </span>
      {priority}
    </Badge>
  );
}

export function UserStatusBadge({ status, reason }: { status: UserStatus; reason?: string | null }) {
  if (status === "todo") return null;
  return (
    <Badge tone={status === "sent" ? "success" : "neutral"} icon={status === "sent" ? "check" : "skip"} className={reason ? "max-w-full whitespace-normal" : ""}>
      {USER_STATUS_LABEL[status]}
      {status === "skipped" && reason && <span className="font-normal">: {reason}</span>}
    </Badge>
  );
}

export function PostingStatusBadge({ status }: { status: PostingStatus }) {
  if (status === "open") return null;
  return (
    <Badge tone={status === "closed" ? "neutral" : "info"} icon={status === "closed" ? "lock" : "info"}>
      {POSTING_STATUS_LABEL[status]}
    </Badge>
  );
}

export function FilterStateBadge({ state }: { state: FilterState }) {
  switch (state) {
    case "flagged":
      return (
        <Badge tone="warning" icon="alert">
          Da verificare
        </Badge>
      );
    case "rejected":
      return (
        <Badge tone="danger" icon="x">
          Scartato
        </Badge>
      );
    case "overridden":
      return (
        <Badge tone="info" icon="undo">
          Ripristinato
        </Badge>
      );
    default:
      return null;
  }
}

const draftTone: Record<DraftStatus, { tone: Tone; icon: IconName }> = {
  draft: { tone: "neutral", icon: "file" },
  approved: { tone: "accent", icon: "check" },
  sent: { tone: "success", icon: "send" },
  skipped: { tone: "neutral", icon: "skip" },
  failed: { tone: "danger", icon: "alert" },
};

export function DraftStatusBadge({ status }: { status: DraftStatus }) {
  const m = draftTone[status];
  return (
    <Badge tone={m.tone} icon={m.icon}>
      {DRAFT_STATUS_LABEL[status]}
    </Badge>
  );
}

/** Local embedding similarity to the profile (0..1): a compact bar plus the value. */
export function SimilarityBadge({ value }: { value: number | null | undefined }) {
  if (value == null) return null;
  const v = Math.min(1, Math.max(0, value));
  const label = v.toFixed(2).replace(".", ",");
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-line px-1.5 py-0.5 text-xs font-medium leading-4 text-ink-muted"
      title="Affinità locale con il tuo profilo (embedding), calcolata prima della valutazione LLM"
    >
      <span aria-hidden="true" className="h-1.5 w-8 overflow-hidden rounded-full bg-subtle">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.round(v * 100)}%` }} />
      </span>
      <span aria-hidden="true">Affinità {label}</span>
      <span className="sr-only">Affinità con il profilo: {Math.round(v * 100)} su 100</span>
    </span>
  );
}
