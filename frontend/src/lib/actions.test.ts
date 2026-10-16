import { describe, expect, it } from "vitest";
import type { Application, Today } from "../api/types";
import { makePosting } from "../test/utils";
import { buildTodayActions } from "./actions";
import { isoDate } from "../mocks/fixtures";

const app = (over: Partial<Application>): Application => ({
  id: 1,
  posting_id: null,
  company: "Corvo",
  role: "Analyst",
  city: null,
  date_sent: isoDate(-20),
  channel: "Email",
  cv_version: "CV v1",
  stage: "Inviata",
  next_step: null,
  next_step_date: null,
  notes: null,
  responded: false,
  updated_at: isoDate(-1),
  followup_due: true,
  ...over,
});

function today(over: Partial<Today> = {}): Today {
  return {
    weekly_goal: 10,
    applications_this_week: 2,
    new_since_yesterday: [],
    closed_since_yesterday: [],
    upcoming_deadlines: [],
    followups_due: [],
    pending_approvals: 0,
    last_run: null,
    ...over,
  };
}

describe("buildTodayActions", () => {
  it("orders deadlines (Alta first), new Alta, packs, follow-ups, drafts, post-graduation", () => {
    const mediaSoon = makePosting({ id: 1, title: "Media tra 1 giorno", priority: "Media", deadline: isoDate(1) });
    const altaLater = makePosting({ id: 2, title: "Alta tra 6 giorni", priority: "Alta", deadline: isoDate(6) });
    const altaSoon = makePosting({ id: 3, title: "Alta tra 2 giorni", priority: "Alta", deadline: isoDate(2), has_pack: true });
    const tooFar = makePosting({ id: 4, title: "Alta tra 12 giorni", priority: "Alta", deadline: isoDate(12), has_pack: true });
    const newAlta = makePosting({ id: 5, title: "Nuovo Alta", priority: "Alta" });
    const newMedia = makePosting({ id: 6, title: "Nuovo Media", priority: "Media" });
    const pack = makePosting({ id: 7, title: "Pacchetto", priority: "Bassa", has_pack: true });
    const packSent = makePosting({ id: 8, title: "Già inviato", has_pack: true, user_status: "sent" });
    const items = buildTodayActions(
      today({
        upcoming_deadlines: [mediaSoon, altaLater, altaSoon, tooFar],
        new_since_yesterday: [newAlta, newMedia, altaSoon],
        followups_due: [app({ id: 20, date_sent: isoDate(-15) }), app({ id: 21, date_sent: isoDate(-30) })],
        pending_approvals: 3,
        new_post_graduation: 2,
      }),
      [pack, packSent, altaSoon, tooFar],
    );
    expect(items.map((i) => i.key)).toEqual([
      "deadline-3",
      "deadline-2",
      "deadline-1",
      "new-5",
      "pack-4",
      "pack-7",
      "followup-21",
      "followup-20",
      "drafts",
      "post-graduation",
    ]);
    expect(items[0]!.to).toBe("/annunci/3/pacchetto");
    expect(items[1]!.to).toBe("/annunci?open=2");
    expect(items.find((i) => i.key === "followup-21")!.to).toBe("/candidature?apri=21");
    expect(items.find((i) => i.key === "drafts")!.to).toBe("/approvazioni");
  });

  it("is empty when there is nothing to do", () => {
    expect(buildTodayActions(today())).toEqual([]);
  });
});
