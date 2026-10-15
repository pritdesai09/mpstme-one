import { useMemo, useState } from "react";
import { marked } from "marked";
import { Icon } from "./ui.jsx";

marked.setOptions({ gfm: true, breaks: false });

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Markdown -> HTML, with [1] [2] turned into clickable citation markers
function renderAnswer(text, citedNumbers) {
  const html = marked
    .parse(escapeHtml(text))
    .replace(/<table>/g, '<div class="table-wrap"><table>')
    .replace(/<\/table>/g, "</table></div>");
  return html.replace(/\[(\d+)\]/g, (match, n) =>
    citedNumbers.has(Number(n))
      ? `<button type="button" class="cite" data-n="${n}" aria-label="Show source ${n}">${n}</button>`
      : ""
  );
}

export function tabLabel(c) {
  let name = c.source;
  if (/^SRB/i.test(name)) name = "SRB";
  else name = name.replace(/\.pdf$/i, "").replace(/[-_]+/g, " ").replace(/\s+-\s+MPSTME$/i, "");
  if (name.length > 26) name = name.slice(0, 25).trimEnd() + "…";
  return c.page ? `${name} p.${c.page}` : name;
}

const LEVEL_TEXT = { high: "High confidence", medium: "Medium confidence", low: "Low confidence" };

export default function Answer({ content, citations = [], confidence }) {
  const [openN, setOpenN] = useState(null);
  const [copied, setCopied] = useState(false);

  const citedNumbers = useMemo(() => new Set(citations.map((c) => c.n)), [citations]);
  const html = useMemo(() => renderAnswer(content, citedNumbers), [content, citedNumbers]);
  const open = citations.find((c) => c.n === openN);
  const level = confidence?.level;

  function onBodyClick(e) {
    const btn = e.target.closest("button.cite");
    if (btn) toggle(Number(btn.dataset.n));
  }

  function toggle(n) {
    setOpenN((cur) => (cur === n ? null : n));
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(content.replace(/\[(\d+)\]/g, ""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked */ }
  }

  return (
    <div className="answer">
      <div className="answer__body prose" onClick={onBodyClick} dangerouslySetInnerHTML={{ __html: html }} />

      {citations.length > 0 && (
        <div className="tabs" role="list" aria-label="Sources">
          {citations.map((c) => (
            <button
              key={c.n}
              type="button"
              role="listitem"
              className={`tab ${openN === c.n ? "tab--open" : ""}`}
              aria-expanded={openN === c.n}
              onClick={() => toggle(c.n)}
              title={c.section || c.source}
            >
              <span className="tab__n">{c.n}</span>
              {tabLabel(c)}
            </button>
          ))}
        </div>
      )}

      {open && (
        <blockquote className="excerpt">
          <p className="excerpt__where">
            {open.source}
            {open.page ? `, page ${open.page}` : ""}
          </p>
          {open.section && <p className="excerpt__section">{open.section}</p>}
          <p className="excerpt__text">“{open.snippet}…”</p>
          {open.url && (
            <a className="excerpt__link" href={open.url} target="_blank" rel="noreferrer">
              <Icon name="link" size={14} /> {open.page ? "Open document" : "Open page"}
            </a>
          )}
        </blockquote>
      )}

      <div className="answer__meta">
        {level && level !== "none" && (
          <span className={`confidence confidence--${level}`}>
            <span className="confidence__dot" />
            {confidence.label?.startsWith("Not found") ? "Not found in sources" : LEVEL_TEXT[level]}
          </span>
        )}
        <button type="button" className="icon-btn" onClick={copy} aria-label="Copy answer">
          <Icon name={copied ? "check" : "copy"} size={15} />
        </button>
      </div>
    </div>
  );
}