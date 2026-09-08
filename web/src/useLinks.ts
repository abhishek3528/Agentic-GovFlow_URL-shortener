/**
 * The set of links this browser created, kept in localStorage.
 *
 * The API has no list endpoint by design, so there is nothing to fetch. This is
 * per-browser convenience state, not a source of truth: clearing it loses the
 * list but not the links, which still resolve because they live in the API's
 * database. Every read and write is guarded because storage access itself can
 * throw in a private window or with site data blocked.
 */

import { useCallback, useEffect, useState } from "react";
import type { Link } from "./api";

const KEY = "govflow.links.v1";

function read(): Link[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (entry): entry is Link =>
        typeof entry === "object" &&
        entry !== null &&
        typeof (entry as Link).code === "string" &&
        typeof (entry as Link).destination === "string",
    );
  } catch {
    return [];
  }
}

export function useLinks() {
  const [links, setLinks] = useState<Link[]>(read);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(links));
    } catch {
      // Storage unavailable: the session still works, the list just will not
      // survive a reload. Not worth interrupting the user over.
    }
  }, [links]);

  const remember = useCallback((link: Link) => {
    setLinks((current) => {
      // Creation is idempotent server-side, so re-shortening a destination
      // returns a code already in the list. Move it to the front rather than
      // duplicating it.
      const rest = current.filter((entry) => entry.code !== link.code);
      return [link, ...rest];
    });
  }, []);

  const forget = useCallback((code: string) => {
    setLinks((current) => current.filter((entry) => entry.code !== code));
  }, []);

  /**
   * Put a hidden link back. Hiding is reversible and offered as an undo rather
   * than guarded by a confirmation dialog: the action is cheap to reverse, and
   * an undo does not interrupt someone who meant to do it.
   */
  const restore = useCallback((link: Link) => {
    setLinks((current) =>
      current.some((entry) => entry.code === link.code) ? current : [link, ...current],
    );
  }, []);

  const clear = useCallback(() => setLinks([]), []);

  return { links, remember, forget, restore, clear };
}
