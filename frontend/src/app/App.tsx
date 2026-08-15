import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { ApprovalPage } from "../pages/ApprovalPage";
import { EvidencePage } from "../pages/EvidencePage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { ReportPage } from "../pages/ReportPage";
import { SystemPage } from "../pages/SystemPage";
import { TaskCreatePage } from "../pages/TaskCreatePage";
import { TaskLayout } from "../pages/TaskLayout";
import { TaskListPage } from "../pages/TaskListPage";
import { TaskOverviewPage } from "../pages/TaskOverviewPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/tasks" replace />} />
        <Route path="tasks" element={<TaskListPage />} />
        <Route path="tasks/new" element={<TaskCreatePage />} />
        <Route path="tasks/:taskId" element={<TaskLayout />}>
          <Route index element={<TaskOverviewPage />} />
          <Route path="evidence" element={<EvidencePage />} />
          <Route path="report" element={<ReportPage />} />
          <Route path="approvals/:approvalId" element={<ApprovalPage />} />
        </Route>
        <Route path="system" element={<SystemPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
