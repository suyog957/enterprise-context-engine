import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { ApiError, apiRequest } from "../api/client";
import type { Principal } from "../api/types";

/** Seeded local-only principals (X-Dev-Principal); not an authentication mechanism. */
export const DEMO_PRINCIPALS = [
  { id: "user-alice", label: "Alice Morgan - buyer, BU-000" },
  { id: "user-buyer-010", label: "Buyer 10 - manager, BU-000" },
  { id: "user-buyer-001", label: "Buyer 01 - buyer, BU-001" },
  { id: "user-auditor", label: "Auditor - read only, all units" },
  { id: "user-admin", label: "Admin - all units" },
];

const STORAGE_KEY = "ecg.principal";

interface Session {
  principalId: string;
  setPrincipalId: (id: string) => void;
  principal: Principal | null;
  apiReady: boolean | null;
  request: <T>(path: string, init?: Parameters<typeof apiRequest>[2]) => Promise<T>;
}

const SessionContext = createContext<Session | null>(null);

function initialPrincipal(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "user-alice";
  } catch {
    return "user-alice";
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [principalId, setPrincipalIdState] = useState(initialPrincipal);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [apiReady, setApiReady] = useState<boolean | null>(null);

  const setPrincipalId = useCallback((id: string) => {
    setPrincipalIdState(id);
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // storage is a convenience only
    }
  }, []);

  const request = useCallback(
    <T,>(path: string, init?: Parameters<typeof apiRequest>[2]) =>
      apiRequest<T>(path, principalId, init),
    [principalId],
  );

  useEffect(() => {
    let cancelled = false;
    apiRequest<{ status: string }>("/health/ready", principalId)
      .then(() => !cancelled && setApiReady(true))
      .catch(() => !cancelled && setApiReady(false));
    apiRequest<Principal>("/me", principalId)
      .then((me) => !cancelled && setPrincipal(me))
      .catch((error: unknown) => {
        if (!cancelled && error instanceof ApiError) setPrincipal(null);
      });
    return () => {
      cancelled = true;
    };
  }, [principalId]);

  const value = useMemo(
    () => ({ principalId, setPrincipalId, principal, apiReady, request }),
    [principalId, setPrincipalId, principal, apiReady, request],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
