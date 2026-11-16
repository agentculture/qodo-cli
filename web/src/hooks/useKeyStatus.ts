import { useEffect, useState } from "react";
import { getHealth } from "../api/health";
import type { KeyState } from "../api/types";

/**
 * What the header says about the Qodo key, read once from `GET /health`. Every state is
 * its own value so no view mistakes one for another: `unavailable` is a failed request or
 * a body that is not a key status (it says nothing about the key); `loading` is only the
 * moment before the answer.
 */
export type KeyStatusState =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: KeyState; daysLeft: number | null };

const STATES: readonly string[] = ["missing", "unknown", "invalid", "expired", "expiring", "ok"];

export function useKeyStatus(): KeyStatusState {
  const [state, setState] = useState<KeyStatusState>({ status: "loading" });
  useEffect(() => {
    const ctl = new AbortController();
    getHealth(ctl.signal)
      .then((health) => {
        const key = health?.key;
        if (!key || !STATES.includes(key.state)) throw new Error("not a key status");
        setState({
          status: key.state,
          daysLeft: typeof key.days_left === "number" ? key.days_left : null,
        });
      })
      .catch(() => {
        if (!ctl.signal.aborted) setState({ status: "unavailable" });
      });
    return () => ctl.abort();
  }, []);
  return state;
}
