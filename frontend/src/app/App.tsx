import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { NewTaskWorkspace } from "../pages/NewTaskWorkspace";
import { NotFoundPage } from "../pages/NotFoundPage";
import { SystemPage } from "../pages/SystemPage";
import { TaskConversationPage } from "../pages/TaskConversationPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<NewTaskWorkspace />} />
        <Route path="tasks" element={<Navigate to="/" replace />} />
        <Route path="tasks/new" element={<Navigate to="/" replace />} />
        <Route path="tasks/:taskId" element={<TaskConversationPage />} />
        <Route path="tasks/:taskId/*" element={<TaskConversationPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
