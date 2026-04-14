"use client";

import Sidebar from "./Sidebar";
import ThemeProvider from "./ThemeProvider";

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
        <Sidebar />
        <main className="flex-1 ml-64 transition-all duration-300">
          {children}
        </main>
      </div>
    </ThemeProvider>
  );
}
