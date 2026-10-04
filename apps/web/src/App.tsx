import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { ChatPage } from "./pages/ChatPage";
import { EntityPage } from "./pages/EntityPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { GraphPage } from "./pages/GraphPage";
import { PolicyPage } from "./pages/PolicyPage";
import { RequisitionPage } from "./pages/RequisitionPage";
import { TracePage } from "./pages/TracePage";
import { SessionProvider } from "./state/session";

export default function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<ChatPage />} />
            <Route path="requisitions" element={<Navigate to="/requisitions/PR-1007" replace />} />
            <Route path="requisitions/:requisitionId" element={<RequisitionPage />} />
            <Route path="entities" element={<EntityPage />} />
            <Route path="entities/:entityId" element={<EntityPage />} />
            <Route path="graph" element={<GraphPage />} />
            <Route path="policy" element={<PolicyPage />} />
            <Route path="traces" element={<TracePage />} />
            <Route path="traces/:traceId" element={<TracePage />} />
            <Route path="evaluation" element={<EvaluationPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  );
}
