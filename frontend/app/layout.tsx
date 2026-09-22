import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import SystemHealthBadge from "@/components/SystemHealthBadge";
import { ShieldCheck, ExternalLink } from "lucide-react";
import { AuthProvider } from "@/lib/auth-context";
import AppNav from "@/components/AppNav";
import UserNav from "@/components/UserNav";

export const metadata: Metadata = {
  title: "AgentGuard — Evidence-Gated Autonomous Software Verification",
  description: "Zero-trust AI agent workflow with machine-checkable evidence verification and self-healing recovery.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#090d16] text-slate-100 min-h-screen flex flex-col font-sans selection:bg-blue-600 selection:text-white">
        <AuthProvider>
          {/* Top Developer Navigation Bar */}
          <header className="border-b border-slate-800/80 bg-[#0c111d]/90 backdrop-blur sticky top-0 z-50">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between">
              {/* Brand Logo & Tag */}
              <div className="flex items-center gap-6">
                <Link href="/" className="flex items-center gap-2.5 group">
                  <div className="w-7 h-7 rounded bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400 group-hover:border-blue-400 transition-colors">
                    <ShieldCheck className="w-4 h-4 text-blue-400" />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-sm tracking-tight text-white">AgentGuard</span>
                    <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-400 border border-slate-700/60 hidden sm:inline">
                      v1.0
                    </span>
                  </div>
                </Link>

                {/* Navigation Links */}
                <AppNav />
              </div>

              {/* Right Status & Actions */}
              <div className="flex items-center gap-3">
                <SystemHealthBadge />
                <div className="hidden lg:flex items-center gap-2 text-[11px] font-mono text-slate-400 border-l border-slate-800/80 pl-3">
                  <span className="text-slate-500">API:</span>
                  <span className="text-slate-300">127.0.0.1:8000</span>
                </div>
                <a
                  href="http://127.0.0.1:8000/docs"
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-slate-400 hover:text-slate-200 transition-colors hidden sm:flex items-center gap-1 border border-slate-800 px-2.5 py-1 rounded bg-slate-900/60 hover:bg-slate-800"
                >
                  <span>Docs</span>
                  <ExternalLink className="w-3 h-3 text-slate-500" />
                </a>

                {/* User & Logout action */}
                <UserNav />
              </div>
            </div>
          </header>

          {/* Main Content Area */}
          <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-6">
            {children}
          </main>

          {/* Engineering Platform Footer */}
          <footer className="border-t border-slate-800/60 bg-[#070a12] py-4 text-xs text-slate-500">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 flex flex-col sm:flex-row items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-medium text-slate-400">AgentGuard</span>
                <span>•</span>
                <span>Evidence-Gated Autonomous Verification & Self-Healing Engine</span>
              </div>
              <div className="flex items-center gap-4 text-slate-500 text-[11px] font-mono">
                <span>A1 HACKATHON</span>
                <span>ZERO-TRUST EXECUTION</span>
              </div>
            </div>
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}