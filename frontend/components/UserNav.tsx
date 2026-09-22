"use client";

import { useAuth } from "@/lib/auth-context";
import { User, LogOut } from "lucide-react";
import { useState } from "react";

export default function UserNav() {
  const { user, signOut } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

  if (!user) return null;

  const handleSignOut = async () => {
    setLoggingOut(true);
    try {
      await signOut();
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <div className="flex items-center gap-2.5 border-l border-slate-800/80 pl-3">
      {/* User identity pill */}
      <div className="flex items-center gap-1.5 text-xs text-slate-300 font-mono bg-slate-900/80 border border-slate-800 px-2 py-1 rounded">
        <User className="w-3.5 h-3.5 text-blue-400 shrink-0" />
        <span className="max-w-[140px] sm:max-w-[180px] truncate" title={user.email || ""}>
          {user.email}
        </span>
      </div>

      {/* Logout button */}
      <button
        onClick={handleSignOut}
        disabled={loggingOut}
        title="Sign Out"
        className="text-xs text-slate-400 hover:text-rose-300 hover:bg-rose-950/30 hover:border-rose-900/60 border border-slate-800 px-2.5 py-1 rounded bg-slate-900/60 transition-colors flex items-center gap-1.5 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <LogOut className="w-3 h-3 text-slate-400 group-hover:text-rose-300" />
        <span className="hidden sm:inline">Logout</span>
      </button>
    </div>
  );
}
