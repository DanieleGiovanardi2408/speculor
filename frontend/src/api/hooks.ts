import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { ApiError, api } from "./client";
import type {
  AppStats,
  Application,
  ApplicationCreate,
  ApplicationPatch,
  Company,
  CompanyPatch,
  Contact,
  ContactCreate,
  Draft,
  DraftCreate,
  DraftForContactBody,
  DraftPatch,
  DraftStatus,
  MarkSentBody,
  Pack,
  PackPatch,
  PostingDetail,
  PostingPatch,
  PostingSummary,
  PostingsQuery,
  RunSummary,
  SettingsOut,
  SettingsPatch,
  Today,
} from "./types";

export const qk = {
  today: ["today"] as const,
  postingsAll: ["postings"] as const,
  postings: (q: PostingsQuery) => ["postings", q] as const,
  posting: (id: number) => ["posting", id] as const,
  pack: (postingId: number) => ["pack", postingId] as const,
  draftsAll: ["drafts"] as const,
  drafts: (status: DraftStatus) => ["drafts", status] as const,
  applications: ["applications"] as const,
  appStats: ["applications", "stats"] as const,
  companies: ["companies"] as const,
  settings: ["settings"] as const,
  runs: (limit: number) => ["runs", limit] as const,
};

// ---------- Queries ----------

export const useToday = () =>
  useQuery({ queryKey: qk.today, queryFn: ({ signal }) => api.get<Today>("/api/today", undefined, signal) });

export const usePostings = (q: PostingsQuery) =>
  useQuery({
    queryKey: qk.postings(q),
    queryFn: ({ signal }) => api.get<PostingSummary[]>("/api/postings", { ...q }, signal),
    placeholderData: (prev) => prev,
  });

export const usePosting = (id: number) =>
  useQuery({
    queryKey: qk.posting(id),
    queryFn: ({ signal }) => api.get<PostingDetail>(`/api/postings/${id}`, undefined, signal),
    enabled: Number.isFinite(id),
  });

/** Resolves to `null` when the posting has no pack yet (404). */
export const usePack = (postingId: number) =>
  useQuery({
    queryKey: qk.pack(postingId),
    queryFn: async ({ signal }) => {
      try {
        return await api.get<Pack>(`/api/postings/${postingId}/pack`, undefined, signal);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    enabled: Number.isFinite(postingId),
  });

export const useDrafts = (status: DraftStatus) =>
  useQuery({
    queryKey: qk.drafts(status),
    queryFn: ({ signal }) => api.get<Draft[]>("/api/drafts", { status }, signal),
  });

export const useApplications = () =>
  useQuery({
    queryKey: qk.applications,
    queryFn: ({ signal }) => api.get<Application[]>("/api/applications", undefined, signal),
  });

export const useAppStats = () =>
  useQuery({
    queryKey: qk.appStats,
    queryFn: ({ signal }) => api.get<AppStats>("/api/applications/stats", undefined, signal),
  });

export const useCompanies = () =>
  useQuery({
    queryKey: qk.companies,
    queryFn: ({ signal }) => api.get<Company[]>("/api/companies", undefined, signal),
  });

export const useSettings = () =>
  useQuery({
    queryKey: qk.settings,
    queryFn: ({ signal }) => api.get<SettingsOut>("/api/settings", undefined, signal),
  });

export const useRuns = (limit = 10) =>
  useQuery({
    queryKey: qk.runs(limit),
    queryFn: ({ signal }) => api.get<RunSummary[]>("/api/runs", { limit }, signal),
  });

// ---------- Optimistic helpers ----------

type Snapshot = [QueryKey, unknown][];

function snapshot(qc: QueryClient, keys: QueryKey[]): Snapshot {
  return keys.flatMap((k) => qc.getQueriesData({ queryKey: k }));
}
function restore(qc: QueryClient, snap: Snapshot | undefined) {
  snap?.forEach(([k, v]) => qc.setQueryData(k, v));
}

function patchPostingInCaches(qc: QueryClient, id: number, fn: (p: PostingSummary) => PostingSummary) {
  qc.setQueriesData<PostingSummary[]>({ queryKey: qk.postingsAll }, (old) =>
    old?.map((p) => (p.id === id ? fn(p) : p)),
  );
  qc.setQueryData<PostingDetail>(qk.posting(id), (old) => (old ? { ...old, ...fn(old) } : old));
}

function writePosting(qc: QueryClient, p: PostingDetail) {
  qc.setQueriesData<PostingSummary[]>({ queryKey: qk.postingsAll }, (old) =>
    old?.map((x) => (x.id === p.id ? { ...x, ...stripDetail(p) } : x)),
  );
  qc.setQueryData(qk.posting(p.id), p);
}

function stripDetail(p: PostingDetail): PostingSummary {
  const { raw_text: _r, via: _v, history: _h, pack_id: _p, ...summary } = p;
  return summary;
}

// ---------- Posting mutations ----------

export function usePatchPosting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: PostingPatch }) =>
      api.patch<PostingDetail>(`/api/postings/${id}`, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: qk.postingsAll });
      const snap = snapshot(qc, [qk.postingsAll, qk.posting(id)]);
      patchPostingInCaches(qc, id, (p) => ({ ...p, ...patch }) as PostingSummary);
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
    onSuccess: (p) => writePosting(qc, p),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.today });
    },
  });
}

export function useOverridePosting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<PostingDetail>(`/api/postings/${id}/override`),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: qk.postingsAll });
      const snap = snapshot(qc, [qk.postingsAll, qk.posting(id)]);
      patchPostingInCaches(qc, id, (p) => ({
        ...p,
        filter_state: "overridden",
        filter_hits: p.filter_hits.map((h) => (h.action === "reject" ? { ...h, overridden: true } : h)),
      }));
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
    onSuccess: (p) => writePosting(qc, p),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.postingsAll }),
  });
}

export function useAddPosting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (url: string) => api.post<PostingDetail>("/api/postings", { url }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.postingsAll });
      void qc.invalidateQueries({ queryKey: qk.today });
    },
  });
}

export function useMarkPostingSent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: MarkSentBody }) =>
      api.post<Application>(`/api/postings/${id}/mark-sent`, body),
    onMutate: async ({ id }) => {
      await qc.cancelQueries({ queryKey: qk.postingsAll });
      const snap = snapshot(qc, [qk.postingsAll, qk.posting(id)]);
      patchPostingInCaches(qc, id, (p) => ({ ...p, user_status: "sent" }));
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
    onSettled: (_d, _e, { id }) => {
      void qc.invalidateQueries({ queryKey: qk.postingsAll });
      void qc.invalidateQueries({ queryKey: qk.posting(id) });
      void qc.invalidateQueries({ queryKey: qk.applications });
      void qc.invalidateQueries({ queryKey: qk.today });
    },
  });
}

// ---------- Packs, contacts, drafts ----------

export function useGeneratePack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (postingId: number) => api.post<Pack>(`/api/postings/${postingId}/pack`),
    onSuccess: (pack) => {
      qc.setQueryData(qk.pack(pack.posting_id), pack);
      patchPostingInCaches(qc, pack.posting_id, (p) => ({ ...p, has_pack: true }));
    },
  });
}

export function usePatchPack(postingId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: PackPatch }) => api.patch<Pack>(`/api/packs/${id}`, patch),
    onSuccess: (pack) => qc.setQueryData(qk.pack(postingId), pack),
  });
}

export function useCreateContact(postingId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ContactCreate) => api.post<Contact>("/api/contacts", body),
    onSuccess: (c) =>
      qc.setQueryData<Pack | null>(qk.pack(postingId), (old) => (old ? { ...old, contacts: [...old.contacts, c] } : old)),
  });
}

export function useDeleteContact(postingId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/api/contacts/${id}`),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: qk.pack(postingId) });
      const snap = snapshot(qc, [qk.pack(postingId)]);
      qc.setQueryData<Pack | null>(qk.pack(postingId), (old) =>
        old ? { ...old, contacts: old.contacts.filter((c) => c.id !== id) } : old,
      );
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
  });
}

function upsertDraftInCaches(qc: QueryClient, d: Draft) {
  // Lists are keyed by status: remove from lists of other statuses, update/insert in its own.
  for (const [key, list] of qc.getQueriesData<Draft[]>({ queryKey: qk.draftsAll })) {
    if (!list) continue;
    const status = key[1];
    const without = list.filter((x) => x.id !== d.id);
    const exists = without.length !== list.length;
    const next = status === d.status ? (exists ? list.map((x) => (x.id === d.id ? d : x)) : [...list, d]) : without;
    qc.setQueryData(key, next);
  }
  if (d.posting_id != null) {
    qc.setQueryData<Pack | null>(qk.pack(d.posting_id), (old) => {
      if (!old) return old;
      const exists = old.drafts.some((x) => x.id === d.id);
      return { ...old, drafts: exists ? old.drafts.map((x) => (x.id === d.id ? d : x)) : [...old.drafts, d] };
    });
  }
}

export function useCreateDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DraftCreate) => api.post<Draft>("/api/drafts", body),
    onSuccess: (d) => {
      upsertDraftInCaches(qc, d);
      void qc.invalidateQueries({ queryKey: qk.draftsAll });
    },
  });
}

export function useGenerateDraftForContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DraftForContactBody) => api.post<Draft>("/api/drafts/for-contact", body),
    onSuccess: (d) => {
      upsertDraftInCaches(qc, d);
      void qc.invalidateQueries({ queryKey: qk.draftsAll });
      void qc.invalidateQueries({ queryKey: qk.today });
    },
  });
}

export function usePatchDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: DraftPatch }) => api.patch<Draft>(`/api/drafts/${id}`, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: qk.draftsAll });
      const snap = snapshot(qc, [qk.draftsAll, ["pack"]]);
      const apply = (d: Draft): Draft => (d.id === id ? ({ ...d, ...patch } as Draft) : d);
      const current = qc
        .getQueriesData<Draft[]>({ queryKey: qk.draftsAll })
        .flatMap(([, l]) => l ?? [])
        .find((d) => d.id === id);
      if (current) upsertDraftInCaches(qc, apply(current));
      qc.setQueriesData<Pack | null>({ queryKey: ["pack"] }, (old) =>
        old ? { ...old, drafts: old.drafts.map(apply) } : old,
      );
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
    onSuccess: (d) => upsertDraftInCaches(qc, d),
    onSettled: (_d, _e, { patch }) => {
      if (patch.status) {
        void qc.invalidateQueries({ queryKey: qk.draftsAll });
        void qc.invalidateQueries({ queryKey: qk.today });
      }
    },
  });
}

export function useMarkDraftSent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<Draft>(`/api/drafts/${id}/mark-sent`),
    onSuccess: (d) => {
      upsertDraftInCaches(qc, d);
      void qc.invalidateQueries({ queryKey: qk.draftsAll });
      void qc.invalidateQueries({ queryKey: qk.today });
    },
  });
}

// ---------- Applications ----------

export function useCreateApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ApplicationCreate) => api.post<Application>("/api/applications", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.applications });
      void qc.invalidateQueries({ queryKey: qk.today });
    },
  });
}

export function usePatchApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: ApplicationPatch }) =>
      api.patch<Application>(`/api/applications/${id}`, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: qk.applications, exact: true });
      const snap = snapshot(qc, [qk.applications]);
      qc.setQueryData<Application[]>(qk.applications, (old) =>
        old?.map((a) => (a.id === id ? { ...a, ...patch } : a)),
      );
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
    onSuccess: (a) =>
      qc.setQueryData<Application[]>(qk.applications, (old) => old?.map((x) => (x.id === a.id ? a : x))),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.appStats });
      void qc.invalidateQueries({ queryKey: qk.today });
    },
  });
}

// ---------- Companies & settings ----------

export function usePatchCompany() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: CompanyPatch }) => api.patch<Company>(`/api/companies/${id}`, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: qk.companies });
      const snap = snapshot(qc, [qk.companies]);
      qc.setQueryData<Company[]>(qk.companies, (old) => old?.map((c) => (c.id === id ? { ...c, ...patch } : c)));
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
    onSuccess: (c) => qc.setQueryData<Company[]>(qk.companies, (old) => old?.map((x) => (x.id === c.id ? c : x))),
  });
}

export function usePutSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.put<SettingsOut>("/api/settings", patch),
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: qk.settings });
      const snap = snapshot(qc, [qk.settings, qk.today]);
      qc.setQueryData<SettingsOut>(qk.settings, (old) =>
        old
          ? {
              weekly_goal: patch.weekly_goal ?? old.weekly_goal,
              sources_enabled: { ...old.sources_enabled, ...(patch.sources_enabled ?? {}) },
            }
          : old,
      );
      if (patch.weekly_goal != null) {
        qc.setQueryData<Today>(qk.today, (old) => (old ? { ...old, weekly_goal: patch.weekly_goal! } : old));
      }
      return { snap };
    },
    onError: (_e, _v, ctx) => restore(qc, ctx?.snap),
    onSuccess: (s) => {
      qc.setQueryData(qk.settings, s);
      qc.setQueryData<Today>(qk.today, (old) => (old ? { ...old, weekly_goal: s.weekly_goal } : old));
    },
  });
}
