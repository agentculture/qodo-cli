import { useKeyStatus, type KeyStatusState } from "../hooks/useKeyStatus";

const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;

/** The words and the style state for one key status. A number is shown only when it is known. */
export function describeKey(state: KeyStatusState): { text: string; kind: string } {
  switch (state.status) {
    case "loading":
      return { text: "Qodo key: checking…", kind: "loading" };
    case "ok":
      if (state.daysLeft === null) break;
      return { text: `Qodo key: ${plural(state.daysLeft, "day")} left`, kind: "ok" };
    case "expiring":
      if (state.daysLeft === null) break;
      return {
        text:
          state.daysLeft <= 0
            ? "Qodo key: expires today"
            : `Qodo key: ${plural(state.daysLeft, "day")} left`,
        kind: "expiring",
      };
    case "expired":
      if (state.daysLeft === null) return { text: "Qodo key: expired", kind: "expired" };
      return {
        text: `Qodo key: expired ${plural(Math.abs(state.daysLeft), "day")} ago`,
        kind: "expired",
      };
    case "missing":
      return { text: "Qodo key: not set", kind: "missing" };
    case "unknown":
      return { text: "Qodo key: expiry not set", kind: "unknown" };
    case "invalid":
      return { text: "Qodo key: expiry unreadable", kind: "invalid" };
  }
  return { text: "Qodo key: status unavailable", kind: "unavailable" };
}

/** "Qodo key: N days left" from the canvas header, with an honest state for everything else. */
export function KeyStatus() {
  const { text, kind } = describeKey(useKeyStatus());
  return (
    <output className="key-status" data-key-state={kind}>
      {text}
    </output>
  );
}

export default KeyStatus;
