"use client";

import React, { createContext, useContext, useEffect, useState, useTransition } from "react";
import { useRouter, usePathname } from "next/navigation";
import type { Session, User } from "@supabase/supabase-js";
import { supabase } from "@/lib/supabase";
import { ShieldCheck, Loader2 } from "lucide-react";

interface AuthContextType {
  user: User | null;
  session: Session | null;
  loading: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  session: null,
  loading: true,
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();
  const [, startTransition] = useTransition();

  useEffect(() => {
    let mounted = true;

    // 1. Restore existing session on initial load or refresh
    supabase.auth
      .getSession()
      .then(({ data: { session } }) => {
        if (!mounted) return;
        setSession(session);
        setUser(session?.user ?? null);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to retrieve Supabase session:", err);
        if (mounted) setLoading(false);
      });

    // 2. Keep session updated on auth state changes
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return;
      setSession(session);
      setUser(session?.user ?? null);
      setLoading(false);
    });

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, []);

  const signOut = async () => {
    try {
      await supabase.auth.signOut();
    } catch (err) {
      console.error("Error during signOut:", err);
    } finally {
      setSession(null);
      setUser(null);
      startTransition(() => {
        router.replace("/login");
      });
    }
  };

  // Route protection effect
  useEffect(() => {
    if (loading) return;

    const isLoginPage = pathname === "/login";

    if (!session && !isLoginPage) {
      startTransition(() => {
        router.replace("/login");
      });
    } else if (session && isLoginPage) {
      startTransition(() => {
        router.replace("/");
      });
    }
  }, [loading, session, pathname, router]);

  const isLoginPage = pathname === "/login";

  // While verifying session for protected routes, show sleek dark loading screen
  if (loading && !isLoginPage) {
    return (
      <div className="min-h-screen bg-[#090d16] flex flex-col items-center justify-center p-4">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 rounded-lg bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400 animate-pulse">
            <ShieldCheck className="w-6 h-6 text-blue-400" />
          </div>
          <div className="flex items-center gap-2 text-xs font-mono text-slate-400 uppercase tracking-widest">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
            <span>Verifying Security Clearance...</span>
          </div>
        </div>
      </div>
    );
  }

  // If unauthenticated on a protected route, hold screen until redirect finishes
  if (!loading && !session && !isLoginPage) {
    return (
      <div className="min-h-screen bg-[#090d16] flex flex-col items-center justify-center p-4">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 rounded-lg bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400">
            <ShieldCheck className="w-6 h-6 text-blue-400" />
          </div>
          <div className="flex items-center gap-2 text-xs font-mono text-slate-400 uppercase tracking-widest">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
            <span>Redirecting to Login...</span>
          </div>
        </div>
      </div>
    );
  }

  // If authenticated and on login page, hold screen until redirect to dashboard finishes
  if (!loading && session && isLoginPage) {
    return (
      <div className="min-h-screen bg-[#090d16] flex flex-col items-center justify-center p-4">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 rounded-lg bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400">
            <ShieldCheck className="w-6 h-6 text-blue-400" />
          </div>
          <div className="flex items-center gap-2 text-xs font-mono text-slate-400 uppercase tracking-widest">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
            <span>Redirecting to Dashboard...</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user, session, loading, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
