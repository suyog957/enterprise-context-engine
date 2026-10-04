import { NavLink, Outlet } from "react-router-dom";
import {
  BarChart3,
  Boxes,
  ClipboardList,
  GitBranch,
  MessageSquareText,
  ShieldCheck,
  Waypoints,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { DEMO_PRINCIPALS, useSession } from "../state/session";

const NAVIGATION: Array<{ to: string; label: string; icon: LucideIcon }> = [
  { to: "/", label: "Chat", icon: MessageSquareText },
  { to: "/requisitions", label: "Requisitions", icon: ClipboardList },
  { to: "/entities", label: "Entities", icon: GitBranch },
  { to: "/graph", label: "Graph", icon: Boxes },
  { to: "/policy", label: "Policy", icon: ShieldCheck },
  { to: "/traces", label: "Traces", icon: Waypoints },
  { to: "/evaluation", label: "Evaluation", icon: BarChart3 },
];

export function Layout() {
  const { principalId, setPrincipalId, principal, apiReady } = useSession();
  return (
    <div className="shell">
      <nav className="rail" aria-label="Primary">
        <span className="brand" aria-hidden="true">
          ECG
        </span>
        {NAVIGATION.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) => `rail-link${isActive ? " is-active" : ""}`}
            title={label}
          >
            <Icon size={19} aria-hidden="true" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="workspace">
        <header className="topbar">
          <div className="wordmark">
            <span className="wordmark-dot" />
            Enterprise Context Graph Agent
          </div>
          <div className="topbar-meta">
            <label className="principal-picker">
              <span>Acting as</span>
              <select
                aria-label="Principal"
                value={principalId}
                onChange={(event) => setPrincipalId(event.target.value)}
              >
                {DEMO_PRINCIPALS.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            {principal && (
              <span className="scope mono" title="Roles and business-unit scope">
                {principal.roles.join("+")} · {principal.business_unit_ids.length} BU
              </span>
            )}
            <span className={`api-status ${apiReady ? "is-ready" : ""}`}>
              <span className="status-dot" />
              {apiReady === null ? "checking API" : apiReady ? "API ready" : "API unavailable"}
            </span>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
        <footer className="footer mono">
          Local development identity (X-Dev-Principal) - synthetic data only
        </footer>
      </div>
    </div>
  );
}
