import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { ConnectionProvider } from './hooks/ConnectionContext';
import { ToastProvider } from './hooks/Toasts';
import ArchitecturePage from './pages/ArchitecturePage';
import CoursesPage from './pages/CoursesPage';
import FairQueuePage from './pages/FairQueuePage';
import OverviewPage from './pages/OverviewPage';
import RaceLabPage from './pages/RaceLabPage';
import RecoveryPage from './pages/RecoveryPage';
import RegisterPage from './pages/RegisterPage';

export default function App() {
  return (
    <ConnectionProvider>
      <ToastProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/courses" element={<CoursesPage />} />
            <Route path="/fairqueue" element={<FairQueuePage />} />
            <Route path="/race-lab" element={<RaceLabPage />} />
            <Route path="/recovery" element={<RecoveryPage />} />
            <Route path="/architecture" element={<ArchitecturePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
      </ToastProvider>
    </ConnectionProvider>
  );
}
