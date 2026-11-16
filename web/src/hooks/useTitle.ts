import { useEffect } from "react";

/** `<title> - qodo` for the current screen. */
export function useTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} - qodo`;
  }, [title]);
}
