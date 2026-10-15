import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { ApplicationsPage } from "./pages/ApplicationsPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { CompaniesPage } from "./pages/CompaniesPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PackPage } from "./pages/PackPage";
import { PostingsPage } from "./pages/PostingsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TodayPage } from "./pages/TodayPage";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <TodayPage /> },
      { path: "annunci", element: <PostingsPage /> },
      { path: "annunci/:id/pacchetto", element: <PackPage /> },
      { path: "approvazioni", element: <ApprovalsPage /> },
      { path: "candidature", element: <ApplicationsPage /> },
      { path: "aziende", element: <CompaniesPage /> },
      { path: "impostazioni", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
      mutations: { retry: 0 },
    },
  });
}

const queryClient = makeQueryClient();
const router = createBrowserRouter(routes, {
  future: {
    v7_relativeSplatPath: true,
    v7_fetcherPersist: true,
    v7_normalizeFormMethod: true,
    v7_partialHydration: true,
    v7_skipActionErrorRevalidation: true,
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </ToastProvider>
    </QueryClientProvider>
  );
}
