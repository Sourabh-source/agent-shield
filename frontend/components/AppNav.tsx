"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Terminal, Layers, PlusCircle } from "lucide-react";

export default function AppNav() {
  const { user } = useAuth();
  const pathname = usePathname();

  if (!user) return null;

  return (
    <nav className="hidden md:flex items-center gap-1 border-l border-slate-800/80 pl-5 text-xs font-medium">
      <Link
        href="/"
        className={`px-3 py-1.5 rounded-md transition-colors flex items-center gap-1.5 ${
          pathname === "/"
            ? "text-white bg-slate-800 font-semibold"
            : "text-slate-300 hover:text-white hover:bg-slate-800/50"
        }`}
      >
        <Layers className="w-3.5 h-3.5 text-slate-400" />
        <span>Dashboard</span>
      </Link>
      <Link
        href="/workflows"
        className={`px-3 py-1.5 rounded-md transition-colors flex items-center gap-1.5 ${
          pathname === "/workflows"
            ? "text-white bg-slate-800 font-semibold"
            : "text-slate-300 hover:text-white hover:bg-slate-800/50"
        }`}
      >
        <Terminal className="w-3.5 h-3.5 text-slate-400" />
        <span>Workflows</span>
      </Link>
      <Link
        href="/new"
        className="px-3 py-1.5 rounded-md text-blue-400 hover:text-blue-300 hover:bg-blue-950/40 border border-blue-900/40 transition-colors flex items-center gap-1.5 ml-1"
      >
        <PlusCircle className="w-3.5 h-3.5 text-blue-400" />
        <span>New Run</span>
      </Link>
    </nav>
  );
}
