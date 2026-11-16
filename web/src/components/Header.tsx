import { NavLink } from "react-router-dom";
import KeyStatus from "./KeyStatus";

/**
 * The shell header from the design canvas: the brand dot and the `qodo` wordmark, the three
 * tabs in a floating pill (`aria-current="page"` on the current one) and the key status.
 * There is no avatar: nothing in the API says who is signed in, and none is invented.
 *
 * Shape cited from culture-rules web/src/components/Header.tsx @ b7f861f690b57c4355f81371cc02ad1dd2ae9cac
 * (cite-don't-import: this repo owns its copy). Adapted: three tabs, the key status in place
 * of the identity avatar.
 */
export const TABS = [
  { to: "/findings", label: "Findings" },
  { to: "/configuration", label: "Configuration" },
  { to: "/rules", label: "Rules" },
] as const;

export function Header() {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <span className="brand-dot" aria-hidden="true" />
        <span className="app-header__wordmark">qodo</span>
      </div>
      <nav className="tabs" aria-label="Primary">
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} className="tabs__tab">
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <KeyStatus />
    </header>
  );
}

export default Header;
