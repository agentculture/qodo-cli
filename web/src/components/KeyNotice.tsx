import { useKeyStatus } from "../hooks/useKeyStatus";

/**
 * "Not configured": said once, above a screen that reads Qodo, when `GET /health` reports no
 * Qodo API key on the bridge. Every other key state is the header's to show.
 */
export function KeyNotice() {
  const key = useKeyStatus();
  if (key.status !== "missing") return null;
  return (
    <p className="notice notice--warn">
      Not configured: the bridge has no Qodo API key, so nothing can be read from Qodo. Set{" "}
      <code>QODO_API_KEY</code> where <code>qodo-cli serve</code> runs.
    </p>
  );
}

export default KeyNotice;
