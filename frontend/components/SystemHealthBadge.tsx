"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function SystemHealthBadge() {
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let mounted = true;
    const check = async () => {
      try {
        await api.health();
        if (mounted) setOnline(true);
      } catch {
        if (mounted) setOnline(false);
      }
    };
    check();
    const interval = setInterval(check, 10000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  if (online === null) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-900 border border-zinc-800 text-zinc-400">
        <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-pulse" />
        <span>CONNECTING</span>
      </span>
    );
  }

  if (!online) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono bg-rose-950/60 border border-rose-800/80 text-rose-300">
        <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
        <span>BACKEND OFFLINE</span>
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-950/40 border border-emerald-800/60 text-emerald-400">
      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
      <span>SYSTEM OPERATIONAL</span>
    </span>
  );
}