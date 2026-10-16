import type { ReactNode } from "react";

/**
 * Small, safe Markdown renderer for run reports. It builds React elements (never HTML strings),
 * so the text cannot inject markup or scripts. Supports headings, paragraphs, nested lists,
 * pipe tables, blockquotes, fenced code, rules, `code`, **bold**, *italic*, backslash escapes
 * and [links](https://…) (only http, https and mailto URLs become links).
 */

const SAFE_URL = /^(https?:\/\/|mailto:)/i;

export function safeHref(url: string): string | null {
  const u = url.trim();
  return SAFE_URL.test(u) ? u : null;
}

function findClosing(text: string, marker: string, from: number): number {
  for (let i = from; i <= text.length - marker.length; i++) {
    if (text[i] === "\\") {
      i++;
      continue;
    }
    if (text.startsWith(marker, i)) return i;
  }
  return -1;
}

export function renderInline(text: string, keyBase = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let buf = "";
  let k = 0;
  const flush = () => {
    if (buf) out.push(buf);
    buf = "";
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    const key = `${keyBase}-${k++}`;
    if (c === "\\" && i + 1 < text.length && /[\\`*_[\]()<>|#+\-.!{}]/.test(text[i + 1]!)) {
      buf += text[i + 1];
      i++;
      continue;
    }
    if (c === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i) {
        flush();
        out.push(
          <code key={key} className="rounded bg-subtle px-1 font-mono text-[0.9em]">
            {text.slice(i + 1, end)}
          </code>,
        );
        i = end;
        continue;
      }
    }
    if (text.startsWith("**", i)) {
      const end = findClosing(text, "**", i + 2);
      if (end > i + 2) {
        flush();
        out.push(<strong key={key}>{renderInline(text.slice(i + 2, end), key)}</strong>);
        i = end + 1;
        continue;
      }
    }
    if ((c === "*" || c === "_") && text[i + 1] && text[i + 1] !== " " && (c === "*" || i === 0 || /\W/.test(text[i - 1]!))) {
      const end = findClosing(text, c, i + 1);
      if (end > i + 1 && text[end - 1] !== " ") {
        flush();
        out.push(<em key={key}>{renderInline(text.slice(i + 1, end), key)}</em>);
        i = end;
        continue;
      }
    }
    if (c === "[") {
      const m = /^\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)\)/.exec(text.slice(i));
      if (m) {
        flush();
        const href = safeHref(m[2]!);
        const label = renderInline(m[1]!, key);
        out.push(
          href ? (
            <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="link">
              {label}
              <span className="sr-only"> (si apre in una nuova scheda)</span>
            </a>
          ) : (
            <span key={key}>{label}</span>
          ),
        );
        i += m[0].length - 1;
        continue;
      }
    }
    buf += c;
  }
  flush();
  return out;
}

interface ListNode {
  ordered: boolean;
  items: { text: string; children: ListNode[] }[];
}

const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const splitRow = (line: string) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split(/(?<!\\)\|/)
    .map((c) => c.trim());

function renderList(node: ListNode, key: string): ReactNode {
  const Tag = node.ordered ? "ol" : "ul";
  return (
    <Tag key={key} className={`my-2 space-y-1 pl-5 ${node.ordered ? "list-decimal" : "list-disc"}`}>
      {node.items.map((it, i) => (
        <li key={i}>
          {renderInline(it.text, `${key}-${i}`)}
          {it.children.map((c, j) => renderList(c, `${key}-${i}-${j}`))}
        </li>
      ))}
    </Tag>
  );
}

export function Markdown({ source, headingOffset = 1 }: { source: string; headingOffset?: number }) {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let b = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const key = `b${b++}`;
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.startsWith("```")) code.push(lines[i++]!);
      i++;
      blocks.push(
        <pre key={key} className="my-3 overflow-x-auto rounded-lg bg-subtle p-3 font-mono text-xs">
          {code.join("\n")}
        </pre>,
      );
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = Math.min(6, h[1]!.length + headingOffset);
      const Tag = `h${level}` as "h2";
      const size = level <= 2 ? "text-lg" : level === 3 ? "text-base" : "text-sm";
      blocks.push(
        <Tag key={key} className={`mb-2 mt-5 font-semibold first:mt-0 ${size}`}>
          {renderInline(h[2]!, key)}
        </Tag>,
      );
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr key={key} className="my-4 border-line" />);
      i++;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1]!)) {
      const head = splitRow(line);
      const aligns = splitRow(lines[i + 1]!).map((c) => (c.endsWith(":") ? "text-right" : "text-left"));
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i]!.includes("|") && lines[i]!.trim()) rows.push(splitRow(lines[i++]!));
      blocks.push(
        <div key={key} className="my-3 overflow-x-auto" role="region" aria-label="Tabella del resoconto" tabIndex={0}>
          <table className="w-full min-w-[320px] text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-ink-muted">
                {head.map((c, j) => (
                  <th key={j} scope="col" className={`py-2 pr-4 font-medium ${aligns[j] ?? "text-left"}`}>
                    {renderInline(c, `${key}-h${j}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-b border-line/70 last:border-0">
                  {head.map((_, j) => (
                    <td key={j} className={`py-2 pr-4 tabular-nums ${aligns[j] ?? "text-left"}`}>
                      {renderInline(r[j] ?? "", `${key}-${ri}-${j}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i]!.startsWith(">")) quote.push(lines[i++]!.replace(/^>\s?/, ""));
      blocks.push(
        <blockquote key={key} className="my-3 border-l-2 border-line-strong pl-3 text-ink-muted">
          {renderInline(quote.join(" "), key)}
        </blockquote>,
      );
      continue;
    }
    if (LIST_RE.test(line)) {
      const root: ListNode = { ordered: /\d/.test(LIST_RE.exec(line)![2]!), items: [] };
      const stack: { indent: number; node: ListNode }[] = [{ indent: LIST_RE.exec(line)![1]!.length, node: root }];
      while (i < lines.length) {
        const m = LIST_RE.exec(lines[i]!);
        if (!m) {
          // continuation line of the previous item
          const cont = lines[i]!;
          if (cont.trim() && /^\s+/.test(cont)) {
            const top = stack[stack.length - 1]!.node;
            const last = top.items[top.items.length - 1];
            if (last) last.text += ` ${cont.trim()}`;
            i++;
            continue;
          }
          break;
        }
        const indent = m[1]!.length;
        while (stack.length > 1 && indent < stack[stack.length - 1]!.indent) stack.pop();
        let top = stack[stack.length - 1]!;
        if (indent > top.indent && top.node.items.length > 0) {
          const child: ListNode = { ordered: /\d/.test(m[2]!), items: [] };
          top.node.items[top.node.items.length - 1]!.children.push(child);
          stack.push({ indent, node: child });
          top = stack[stack.length - 1]!;
        }
        top.node.items.push({ text: m[3]!, children: [] });
        i++;
      }
      blocks.push(renderList(root, key));
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !/^(#{1,6}\s|```|>)/.test(lines[i]!) && !LIST_RE.test(lines[i]!)) para.push(lines[i++]!.trim());
    if (para.length === 0) para.push(lines[i++]!.trim()); // never loop forever on odd input
    blocks.push(
      <p key={key} className="my-2 leading-relaxed">
        {renderInline(para.join(" "), key)}
      </p>,
    );
  }
  return <div className="text-sm text-ink">{blocks}</div>;
}
