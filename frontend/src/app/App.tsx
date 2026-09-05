import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { NewTaskWorkspace } from "../pages/NewTaskWorkspace";
import { NotFoundPage } from "../pages/NotFoundPage";
import { SystemPage } from "../pages/SystemPage";
import { TaskConversationPage } from "../pages/TaskConversationPage";

function LegacyTaskRouteRedirect() {
  const { taskId = "" } = useParams();
  return <Navigate to={`/tasks/${encodeURIComponent(taskId)}`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<NewTaskWorkspace />} />
        <Route path="tasks" element={<Navigate to="/" replace />} />
        <Route path="tasks/new" element={<Navigate to="/" replace />} />
        <Route path="tasks/:taskId" element={<TaskConversationPage />} />
        <Route
          path="tasks/:taskId/evidence"
          element={<LegacyTaskRouteRedirect />}
        />
        <Route
          path="tasks/:taskId/report"
          element={<LegacyTaskRouteRedirect />}
        />
        <Route
          path="tasks/:taskId/approvals/:approvalId"
          element={<LegacyTaskRouteRedirect />}
        />
        <Route path="tasks/:taskId/*" element={<LegacyTaskRouteRedirect />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
