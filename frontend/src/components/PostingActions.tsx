import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useOverridePosting, usePatchPosting, useUndoOverride } from "../api/hooks";
import type { PostingSummary } from "../api/types";
import { MarkSentDialog } from "./MarkSentDialog";
import { SkipDialog } from "./SkipDialog";
import { useToast } from "./Toast";

type Target = Pick<PostingSummary, "id" | "title"> & { cv?: string | null };

interface PostingActionsApi {
  /** Opens the "Segna come inviata" dialog (prefilled; Enter confirms). */
  markSent: (p: Target) => void;
  /** Opens the "Salta" dialog with quick reasons; confirms with an undo toast. */
  skip: (p: Target) => void;
  unskip: (p: Target) => void;
  /** Restores a posting rejected by the filters, with an undo toast. */
  restore: (p: Target) => void;
}

const Ctx = createContext<PostingActionsApi | null>(null);

export function usePostingActions(): PostingActionsApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePostingActions must be used within <PostingActionsProvider>");
  return ctx;
}

/** One place for the human-in-the-loop posting flows. Nothing here submits or sends anything. */
export function PostingActionsProvider({ children }: { children: ReactNode }) {
  const [sentFor, setSentFor] = useState<Target | null>(null);
  const [skipFor, setSkipFor] = useState<Target | null>(null);
  const patch = usePatchPosting();
  const override = useOverridePosting();
  const undoOverride = useUndoOverride();
  const toast = useToast();

  const fail = useCallback((what: string) => (e: Error) => toast.error(`${what} non riuscito. ${e.message}`), [toast]);

  const unskip = useCallback(
    (p: Target) => {
      patch.mutateAsync({ id: p.id, patch: { user_status: "todo" } }).then(() => toast.success(`“${p.title}” è di nuovo tra quelli da valutare.`), fail("Ripristino"));
    },
    [patch, toast, fail],
  );

  const doSkip = useCallback(
    (p: Target, reason: string) => {
      patch.mutateAsync({ id: p.id, patch: { user_status: "skipped", skip_reason: reason } }).then(
        () => toast.success(`Saltato: “${p.title}” (${reason}).`, { label: "Annulla", onClick: () => unskip(p) }),
        fail("Salto"),
      );
    },
    [patch, toast, unskip, fail],
  );

  const restore = useCallback(
    (p: Target) => {
      override.mutateAsync(p.id).then(
        () =>
          toast.success(`Ripristinato: “${p.title}” ora è tra gli annunci attivi.`, {
            label: "Annulla",
            onClick: () =>
              undoOverride.mutateAsync(p.id).then(() => toast.success(`“${p.title}” è di nuovo tra gli scartati.`), fail("Annullamento")),
          }),
        fail("Ripristino"),
      );
    },
    [override, undoOverride, toast, fail],
  );

  const api = useMemo<PostingActionsApi>(() => ({ markSent: setSentFor, skip: setSkipFor, unskip, restore }), [unskip, restore]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <MarkSentDialog
        open={!!sentFor}
        onClose={() => setSentFor(null)}
        postingId={sentFor?.id ?? NaN}
        postingTitle={sentFor?.title ?? ""}
        defaultCv={sentFor?.cv}
      />
      <SkipDialog
        open={!!skipFor}
        onClose={() => setSkipFor(null)}
        postingTitle={skipFor?.title ?? ""}
        onConfirm={(reason) => skipFor && doSkip(skipFor, reason)}
      />
    </Ctx.Provider>
  );
}
