import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function ExternalLink({ href, children, className = "link" }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={`${className} inline-flex items-center gap-1`}>
      {children}
      <Icon name="external" size={14} />
      <span className="sr-only"> (si apre in una nuova scheda)</span>
    </a>
  );
}
