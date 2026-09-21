"use client";

import Link from "next/link";
import WorkflowForm from "@/components/WorkflowForm";
import { ArrowLeft, ShieldCheck, Terminal } from "lucide-react";

export default function NewWorkflowPage() {
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Breadcrumbs & Header */}
      <div>
        <div className="flex items-center gap-2 text-xs font-mono text-slate-500 mb-2">
          <Link href="/" className="hover:text-slate-300 transition-colors flex items-center gap-1">
            <ArrowLeft className="w-3 h-3" />
            <span>Dashboard</span>
          </Link>
          <span>/</span>
          <span className="text-slate-300">New Workflow</span>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-white tracking-tight">Initialize Workflow Execution</h1>
            <p className="text-xs text-slate-400 mt-0.5">
              Configure target repository and verification parameters for evidence-gated execution.
            </p>
          </div>
        </div>
      </div>

      {/* Main Execution Card */}
      <div className="bg-[#0e1422] border border-slate-800 rounded-lg p-6 shadow-md">
        <div className="flex items-center gap-2 pb-4 mb-5 border-b border-slate-800/80 text-xs font-mono text-slate-400">
          <Terminal className="w-4 h-4 text-blue-400" />
          <span>Workflow Dispatcher • Zero-Trust Sandbox</span>
        </div>

        <WorkflowForm onCancel={() => window.history.back()} />
      </div>

      {/* Info notice */}
      <div className="p-4 rounded-lg bg-[#0c1220] border border-slate-800/80 text-xs text-slate-400 space-y-2">
        <div className="font-semibold text-slate-300 flex items-center gap-1.5">
          <ShieldCheck className="w-3.5 h-3.5 text-blue-400" />
          <span>Zero-Trust Verification Protocol</span>
        </div>
        <p className="text-slate-400 leading-relaxed text-[11px]">
          AgentGuard automatically clones the repository into an isolated sandbox, detects language & build stacks, plans sequential steps, and verifies execution evidence against strict rules. Exit code 0 is never accepted alone without machine-checkable verification.
        </p>
      </div>
    </div>
  );
}