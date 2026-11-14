import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";

import { AppLayout } from "./layout/AppLayout";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { useSession } from "./session";
import {
  AuditPage,
  ConfigPage,
  DevicesPage,
  InspectorPage,
  OverviewPage,
  ReleasesPage,
  UsersPage,
  VoicePage,
} from "./pages";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true },
  },
});

/** 登录守卫：无有效会话一律回登录页，登录后回到原目标。 */
function RequireSession({ children }: { children: React.ReactNode }) {
  const { hasSession } = useSession();
  const location = useLocation();
  if (!hasSession) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireSession>
                <AppLayout />
              </RequireSession>
            }
          >
              <Route path="/" element={<OverviewPage />} />
              <Route path="/devices" element={<DevicesPage />} />
              <Route path="/inspector" element={<InspectorPage />} />
              <Route path="/config" element={<ConfigPage />} />
              <Route path="/voice" element={<VoicePage />} />
              <Route path="/users" element={<UsersPage />} />
              <Route path="/releases" element={<ReleasesPage />} />
              <Route path="/audit" element={<AuditPage />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
