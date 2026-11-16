/** The words the screens use for Qodo's values. A value with no word here is shown as given. */

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** `remediation_recommended` -> `Remediation recommended`. */
export function humanize(value: string): string {
  const words = value.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : value;
}

/** Qodo's `attribution_status` values (the tool's description lists them), in words. */
const STATUS_WORDS: Record<string, string> = {
  pending: "Open",
  partial_implementation: "Partly implemented",
  not_implemented: "Not implemented",
  focus_areas_edited: "Focus areas edited",
  full_implementation: "Implemented",
  // seen in the recorded reply, though the tool's description does not list it
  implemented: "Implemented",
  dismissed: "Dismissed",
  detected_after_merge: "Detected after merge",
  outdated: "Outdated",
};

export function statusWord(status: string | null | undefined): string {
  if (!status) return "Unknown";
  return STATUS_WORDS[status] ?? humanize(status);
}

const TIME = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

/** An ISO time as `16 Nov 2026, 09:30 UTC`; anything unreadable is shown as given. */
export function formatTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : `${TIME.format(date)} UTC`;
}

export const shortSha = (sha: string | null | undefined) =>
  typeof sha === "string" && sha ? sha.slice(0, 7) : null;
