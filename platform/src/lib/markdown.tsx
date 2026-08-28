import * as React from "react";

/**
 * Minimal, dependency-free markdown renderer for changelog bodies and other
 * team-authored rich text. Supports: #/##/### headings, **bold**, *italic*,
 * `code`, fenced code blocks, [links](https://…), unordered/ordered lists,
 * > blockquotes, --- rules, and paragraphs. All text is escaped first —
 * markdown here can never inject HTML.
 */

function renderInline(text: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // Tokenize: code spans, bold, italic, links.
  const pattern =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyBase}-${i++}`;
    if (token.startsWith("`")) {
      nodes.push(
        <code key={key} className="rounded bg-line/50 px-1 py-0.5 font-mono text-[0.85em]">
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith("**")) {
      nodes.push(
        <strong key={key} className="font-semibold text-ink">
          {renderInline(token.slice(2, -2), key)}
        </strong>
      );
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key}>{renderInline(token.slice(1, -1), key)}</em>);
    } else {
      const label = token.slice(1, token.indexOf("]"));
      const href = match[5]!;
      nodes.push(
        <a
          key={key}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent-soft underline decoration-accent/40 underline-offset-2 hover:text-accent"
        >
          {label}
        </a>
      );
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function Markdown({ source, className }: { source: string; className?: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let codeBlock: string[] | null = null;
  let key = 0;

  const flushParagraph = () => {
    if (paragraph.length) {
      const text = paragraph.join(" ");
      blocks.push(
        <p key={key++} className="leading-relaxed text-ink-muted">
          {renderInline(text, `p${key}`)}
        </p>
      );
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((item, idx) => (
        <li key={idx}>{renderInline(item, `li${key}-${idx}`)}</li>
      ));
      blocks.push(
        list.ordered ? (
          <ol key={key++} className="list-decimal space-y-1 pl-5 text-ink-muted">
            {items}
          </ol>
        ) : (
          <ul key={key++} className="list-disc space-y-1 pl-5 text-ink-muted">
            {items}
          </ul>
        )
      );
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw ?? "";
    if (codeBlock !== null) {
      if (line.trim().startsWith("```")) {
        blocks.push(
          <pre
            key={key++}
            className="overflow-x-auto rounded-lg border border-line bg-void/60 p-3 font-mono text-xs text-ink-muted"
          >
            {codeBlock.join("\n")}
          </pre>
        );
        codeBlock = null;
      } else {
        codeBlock.push(line);
      }
      continue;
    }
    if (line.trim().startsWith("```")) {
      flushParagraph();
      flushList();
      codeBlock = [];
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1]!.length;
      const content = renderInline(heading[2]!, `h${key}`);
      blocks.push(
        level === 1 ? (
          <h2 key={key++} className="mt-2 font-display text-lg font-semibold text-ink">{content}</h2>
        ) : level === 2 ? (
          <h3 key={key++} className="mt-2 font-display text-base font-semibold text-ink">{content}</h3>
        ) : (
          <h4 key={key++} className="mt-1 text-sm font-semibold text-ink">{content}</h4>
        )
      );
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(line.trim())) {
      flushParagraph();
      flushList();
      blocks.push(<hr key={key++} className="border-line" />);
      continue;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      flushList();
      blocks.push(
        <blockquote
          key={key++}
          className="border-l-2 border-accent/50 pl-3 text-sm italic text-ink-muted"
        >
          {renderInline(quote[1] ?? "", `q${key}`)}
        </blockquote>
      );
      continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.*)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (unordered || ordered) {
      flushParagraph();
      const item = (unordered ?? ordered)![1]!;
      const isOrdered = Boolean(ordered);
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = { ordered: isOrdered, items: [] };
      }
      list.items.push(item);
      continue;
    }
    if (line.trim() === "") {
      flushParagraph();
      flushList();
      continue;
    }
    paragraph.push(line.trim());
  }
  if (codeBlock !== null) {
    blocks.push(
      <pre key={key++} className="overflow-x-auto rounded-lg border border-line bg-void/60 p-3 font-mono text-xs text-ink-muted">
        {codeBlock.join("\n")}
      </pre>
    );
  }
  flushParagraph();
  flushList();

  return <div className={className ?? "space-y-3 text-sm"}>{blocks}</div>;
}
