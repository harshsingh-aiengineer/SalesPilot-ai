import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { getToken } from "./lib/api";
import { AgentPage } from "./pages/AgentPage";
import { AutomationPage } from "./pages/AutomationPage";
import { EmailCenterPage } from "./pages/EmailCenterPage";
import { InboxPage } from "./pages/InboxPage";
import { LeadsPage } from "./pages/LeadsPage";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SettingsPage } from "./pages/SettingsPage";

function Guard({ children }: { children: ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <Guard>
            <AppShell />
          </Guard>
        }
      >
        <Route path="/" element={<OverviewPage />} />
        <Route path="/leads" element={<LeadsPage />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/agent" element={<AgentPage />} />
        <Route path="/emails" element={<EmailCenterPage />} />
        <Route path="/automation" element={<AutomationPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
