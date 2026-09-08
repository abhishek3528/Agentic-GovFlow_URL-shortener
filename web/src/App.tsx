import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  createLink,
  displayHost,
  docsUrl,
  fetchStats,
  normalizeDestination,
  shortUrl,
  type Link,
} from "./api";
import { useLinks } from "./useLinks";
import "./App.css";

/**
 * A button that confirms it worked.
 *
 * A copy that gives no feedback reads as a broken button, and re-clicking it is
 * the most common reaction. The confirmation is on the button itself rather
 * than in a toast, so it appears exactly where the user is looking.
 */
function CopyButton({
  value,
  variant,
  label = "Copy",
}: {
  value: string;
  variant: "primary" | "quiet";
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <button
      type="button"
      className={`btn ${variant}${copied ? " copied" : ""}`}
      onClick={() => {
        navigator.clipboard.writeText(value).then(
          () => {
            setCopied(true);
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => setCopied(false), 1800);
          },
          () => setCopied(false),
        );
      }}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

export default function App() {
  const { links, remember } = useLinks();
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [latest, setLatest] = useState<Link | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});

  const resultRef = useRef<HTMLElement | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement | null>(null);

  /**
   * Grow the field to fit the link.
   *
   * A long address in a single-line input shows only its tail, which makes it
   * hard to check you pasted the right thing. Growing the box means the whole
   * link stays visible - and the box visibly getting taller is itself the
   * feedback that this is a long one. Height is capped so a pathological URL
   * scrolls instead of taking over the page.
   */
  useLayoutEffect(() => {
    const field = fieldRef.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  }, [input]);

  /** Open counts come from the stats endpoint; there is no bulk read, so each
   *  known link is asked for its own. The list is small by nature. */
  const refreshCount = useCallback((code: string) => {
    fetchStats(code)
      .then((stats) => setCounts((current) => ({ ...current, [code]: stats.click_count })))
      .catch(() => undefined);
  }, []);

  const codes = useMemo(() => links.map((link) => link.code).join(","), [links]);
  useEffect(() => {
    if (!codes) return;
    const load = () => codes.split(",").forEach(refreshCount);
    load();
    // Someone following a short link in another tab should see the count move
    // here without reloading the page.
    const timer = window.setInterval(load, 8000);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [codes, refreshCount]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const { url } = normalizeDestination(input);
    if (!url) return;
    setBusy(true);
    setError(null);
    createLink(url)
      .then((link) => {
        remember(link);
        setLatest(link);
        setInput("");
        refreshCount(link.code);
        window.requestAnimationFrame(() =>
          resultRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
        );
      })
      .catch((exc: ApiError) => setError(exc.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="page">
      <header>
        <h1>URL Shortener</h1>
        <p className="tagline">Make long links short and easy to share.</p>
      </header>

      <form className="shorten" onSubmit={submit}>
        <label className="visually-hidden" htmlFor="link">
          The link you want to shorten
        </label>
        <textarea
          id="link"
          ref={fieldRef}
          value={input}
          rows={1}
          autoFocus
          autoComplete="off"
          spellCheck={false}
          placeholder="Paste your link here"
          onChange={(event) => {
            // A web address never contains line breaks, so strip anything a
            // paste brings along rather than letting it wrap oddly or fail.
            setInput(event.target.value.replace(/[\r\n]+/g, ""));
            if (error) setError(null);
          }}
          onKeyDown={(event) => {
            // In a textarea Enter would insert a newline. Here it should do what
            // it does in every other search box: submit.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "error" : undefined}
        />
        <button type="submit" className="btn primary big" disabled={busy || !input.trim()}>
          {busy ? "Shortening…" : "Shorten"}
        </button>
      </form>

      {error && (
        <p className="error" id="error" role="alert">
          {error}
        </p>
      )}

      {latest && (
        <section className="result" ref={resultRef} aria-live="polite">
          <h2>Your short link</h2>
          <button
            type="button"
            className="short"
            title="Click to copy"
            onClick={() => navigator.clipboard.writeText(shortUrl(latest.code)).catch(() => {})}
          >
            {shortUrl(latest.code)}
          </button>
          <div className="actions">
            <CopyButton value={shortUrl(latest.code)} variant="primary" />
            <a className="btn quiet" href={shortUrl(latest.code)} target="_blank" rel="noreferrer">
              Open
            </a>
          </div>
        </section>
      )}

      {links.length > 0 && (
        <section className="history">
          <h2>Your links</h2>
          <ul>
            {links.map((link) => (
              <li key={link.code}>
                <a
                  className="site"
                  href={shortUrl(link.code)}
                  target="_blank"
                  rel="noreferrer"
                  title={link.destination}
                >
                  {displayHost(link.destination)}
                </a>
                <span className="opens">
                  {counts[link.code] ?? 0} {counts[link.code] === 1 ? "open" : "opens"}
                </span>
                <CopyButton value={shortUrl(link.code)} variant="quiet" />
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer>
        <a href={docsUrl} target="_blank" rel="noreferrer">
          API
        </a>
      </footer>
    </div>
  );
}
