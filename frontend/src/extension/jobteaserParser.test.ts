// Unit tests for the "Salva in Radar" extension parser (extension/jobteaser/parser.js), with
// fictional HTML only. The parser is a classic script that sets globalThis.RadarJobteaserParser.
import { beforeAll, describe, expect, it } from "vitest";
import "../../../extension/jobteaser/parser.js";

interface Offer {
  title: string;
  company: string;
  url: string;
  location: string | null;
  date: string | null;
}
interface Parser {
  parseOffers(root: Document | Element, baseUrl: string): Offer[];
  isListingUrl(href: string): boolean;
  MAX_OFFERS: number;
}

let parser: Parser;
beforeAll(() => {
  parser = (globalThis as unknown as { RadarJobteaserParser: Parser }).RadarJobteaserParser;
});

const PAGE = "https://luiss.jobteaser.com/it/job-offers?q=data";
const U1 = "0a1b2c3d-1111-4a2a-9f1d-000000000001";

function doc(body: string): Document {
  return new DOMParser().parseFromString(`<!doctype html><html><body>${body}</body></html>`, "text/html");
}

describe("isListingUrl", () => {
  it("accepts listing and search pages only", () => {
    expect(parser.isListingUrl("https://luiss.jobteaser.com/it/job-offers")).toBe(true);
    expect(parser.isListingUrl("https://luiss.jobteaser.com/it/job-offers?q=ai&page=2")).toBe(true);
    expect(parser.isListingUrl("https://www.jobteaser.com/job-offers/")).toBe(true);
    expect(parser.isListingUrl(`https://luiss.jobteaser.com/it/job-offers/${U1}-x`)).toBe(false);
    expect(parser.isListingUrl("https://luiss.jobteaser.com/it/dashboard")).toBe(false);
    expect(parser.isListingUrl("https://jobteaser.com.evil.example/it/job-offers")).toBe(false);
    expect(parser.isListingUrl("http://luiss.jobteaser.com/it/job-offers")).toBe(false);
  });
});

describe("parseOffers", () => {
  it("reads title, company, canonical URL, location and date from rendered cards", () => {
    const d = doc(`
      <header><a href="/it/job-offers?targeted=true">Offerte mirate</a></header>
      <ul>
        <li data-testid="job-ad-card">
          <a href="/it/job-offers/${U1}-nebula-data-intern?from=search#top"><h3>Data Scientist Intern</h3></a>
          <p data-testid="job-ad-company">Nebula Analytics</p>
          <p data-testid="job-ad-location">Roma, Italia</p>
          <span>Stage</span>
          <time datetime="2026-10-12">12 ott</time>
        </li>
        <li data-testid="job-ad-card">
          <a href="https://luiss.jobteaser.com/it/job-offers/10234567-example-ventures">
            <h3>Venture Capital Analyst</h3>
          </a>
          <p>Example Ventures · Amsterdam</p>
          <p>Tirocinio</p>
          <p>3 giorni fa</p>
          <a href="https://luiss.jobteaser.com/it/job-offers/10234567-example-ventures">Candidati</a>
        </li>
      </ul>
      <nav><a href="/it/job-offers?page=2">2</a></nav>`);
    const offers = parser.parseOffers(d, PAGE);
    expect(offers).toEqual([
      {
        title: "Data Scientist Intern",
        company: "Nebula Analytics",
        url: `https://luiss.jobteaser.com/it/job-offers/${U1}-nebula-data-intern`,
        location: "Roma, Italia",
        date: "2026-10-12",
      },
      {
        title: "Venture Capital Analyst",
        company: "Example Ventures",
        url: "https://luiss.jobteaser.com/it/job-offers/10234567-example-ventures",
        location: "Amsterdam",
        date: "3 giorni fa",
      },
    ]);
  });

  it("ignores non-offer links, other hosts and cards without a title", () => {
    const d = doc(`
      <a href="/it/job-offers?page=3">Avanti</a>
      <a href="/it/companies/nebula">Nebula</a>
      <a href="https://example.com/it/job-offers/12345678">Fake</a>
      <a href="http://luiss.jobteaser.com/it/job-offers/12345679">Plain http</a>
      <div><a href="/it/job-offers/12345680"><img alt=""></a></div>`);
    expect(parser.parseOffers(d, PAGE)).toEqual([]);
  });

  it("uses only what is in the DOM it is given (no fetch, no scroll) and caps the count", () => {
    const cards = Array.from(
      { length: 520 },
      (_, i) => `<article><a href="/it/job-offers/${30000000 + i}"><h2>Offer ${i}</h2></a><p>Acme</p></article>`,
    ).join("");
    const d = doc(cards);
    const offers = parser.parseOffers(d, PAGE);
    expect(offers).toHaveLength(parser.MAX_OFFERS);
    expect(offers[0]).toMatchObject({ title: "Offer 0", company: "Acme", location: null, date: null });
  });

  it("deduplicates the same offer linked twice and cuts long strings", () => {
    const long = "T".repeat(400);
    const d = doc(`
      <div class="card"><a href="/it/job-offers/${U1}"><h3>${long}</h3></a><span class="company-name">C</span></div>
      <div class="card"><a href="/en/job-offers/${U1}-again"><h3>Again</h3></a></div>`);
    const offers = parser.parseOffers(d, PAGE);
    expect(offers).toHaveLength(1);
    expect(offers[0]?.title).toHaveLength(300);
    expect(offers[0]?.company).toBe("C");
  });
});
