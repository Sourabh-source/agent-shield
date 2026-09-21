"use client";

import React from "react";
import type { RecoveryAttempt } from "@/lib/api";
import { formatDuration } from "@/lib/api";
import {
  RefreshCw,
  AlertOctagon,
  CheckCircle2,
  XCircle,
  ArrowRight,
  ShieldCheck,
  Terminal,
  Activity,
} from "lucide-react";

interface RecoveryPanelProps {
  recoveryHistory: RecoveryAttempt[];
  currentStatus: string;
}

export default function RecoveryPanel({ recoveryHistory, currentStatus }: RecoveryPanelProps) {
  const isRecovering = currentStatus === "RECOVERING";

  if (recoveryHistory.length === 0 && !isRecovering) {
    return (
      <div className="py-12 text-center text-xs text-slate-500 font-mono">
        <ShieldCheck className="w-8 h-8 mx-auto mb-2 text-slate-600" />
        <p className="text-slate-300 font-semibold font-sans text-sm">No Recovery Interventions Required</p>
        <p className="text-slate-500 mt-1 max-w-sm mx-auto font-sans">
          All steps met evidence verification criteria on their initial execution. The self-healing engine remains on standby.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5 text-xs font-mono">
      {/* Active Recovery Status Banner */}
      {isRecovering && (
        <div className="p-4 rounded-md border border-yellow-700/80 bg-yellow-950/20 text-yellow-300 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <RefreshCw className="w-5 h-5 text-yellow-400 animate-spin shrink-0" />
            <div>
              <span className="font-semibold text-sm font-sans block text-yellow-200">
                Self-Healing Loop In Progress
              </span>
              <span className="text-[11px] text-yellow-400/80">
                Analyzing machine evidence failure, formulating idempotent recovery plan, and preparing step retry...
              </span>
            </div>
          </div>
          <span className="px-2.5 py-1 rounded text-[10px] font-mono uppercase bg-yellow-900/60 border border-yellow-700 text-yellow-200 shrink-0">
            Phase: Recovering
          </span>
        </div>
      )}

      {/* Structured Self-Healing Visual Sequence */}
      {recoveryHistory.length > 0 && (
        <div className="rounded-md border border-slate-800 bg-[#070b14] p-4 space-y-3">
          <div className="flex items-center justify-between pb-2 border-b border-slate-800 text-[11px] uppercase tracking-wider text-slate-400 font-semibold">
            <span>Self-Healing Protocol Flow</span>
            <span className="text-yellow-400 font-mono">{recoveryHistory.length} Intervention(s)</span>
          </div>

          <div className="flex items-center gap-2 overflow-x-auto py-2 text-[11px]">
            <div className="p-2 rounded bg-rose-950/40 border border-rose-800/80 text-rose-300 shrink-0 flex items-center gap-1.5">
              <AlertOctagon className="w-3.5 h-3.5" />
              <span>Evidence Failure</span>
            </div>
            <ArrowRight className="w-3.5 h-3.5 text-slate-600 shrink-0" />
            <div className="p-2 rounded bg-amber-950/40 border border-amber-800/80 text-amber-300 shrink-0 flex items-center gap-1.5">
              <Terminal className="w-3.5 h-3.5" />
              <span>Diagnose & Plan</span>
            </div>
            <ArrowRight className="w-3.5 h-3.5 text-slate-600 shrink-0" />
            <div className="p-2 rounded bg-yellow-950/40 border border-yellow-800/80 text-yellow-300 shrink-0 flex items-center gap-1.5">
              <RefreshCw className="w-3.5 h-3.5 text-yellow-400" />
              <span>Execute Recovery</span>
            </div>
            <ArrowRight className="w-3.5 h-3.5 text-slate-600 shrink-0" />
            <div className="p-2 rounded bg-blue-950/40 border border-blue-800/80 text-blue-300 shrink-0 flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5" />
              <span>Retry Step</span>
            </div>
            <ArrowRight className="w-3.5 h-3.5 text-slate-600 shrink-0" />
            <div className="p-2 rounded bg-emerald-950/40 border border-emerald-800/80 text-emerald-300 shrink-0 flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              <span>Re-Verify</span>
            </div>
          </div>
        </div>
      )}

      {/* Detailed Intervention Cards */}
      <div className="space-y-3">
        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1.5">
          <Terminal className="w-3.5 h-3.5 text-blue-400" />
          <span>Intervention Audit Records ({recoveryHistory.length})</span>
        </div>

        {recoveryHistory.map((item, index) => {
          const isSuccess = item.status === "SUCCESS" || item.exit_code === 0;

          return (
            <div
              key={item.recovery_id || index}
              className="p-3.5 rounded-md border border-slate-800 bg-[#0a0f1d] space-y-2.5"
            >
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  {isSuccess ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                  ) : (
                    <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
                  )}
                  <span className="font-semibold text-slate-200 text-xs">{item.step_name}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-amber-950/80 text-amber-300 border border-amber-800/80 uppercase">
                    {item.failure_type || "DIAGNOSED_FAILURE"}
                  </span>
                </div>

                <div className="flex items-center gap-3 text-[11px] text-slate-400">
                  <span>
                    EXIT:{" "}
                    <span className={item.exit_code === 0 ? "text-emerald-400" : "text-rose-400"}>
                      {item.exit_code}
                    </span>
                  </span>
                  {item.duration_ms > 0 && (
                    <span>DURATION: {formatDuration(item.duration_ms)}</span>
                  )}
                </div>
              </div>

              {/* Synthesized Action */}
              <div className="p-2.5 rounded bg-[#050811] border border-slate-800/80 flex items-center gap-2">
                <span className="text-yellow-500 font-semibold select-none">$</span>
                <code className="text-xs text-yellow-300 font-mono truncate">{item.action}</code>
              </div>

              <div className="flex items-center justify-between text-[11px] text-slate-500 pt-1">
                <span>Intervention #{index + 1}</span>
                <span
                  className={`font-semibold uppercase ${
                    isSuccess ? "text-emerald-400" : "text-rose-400"
                  }`}
                >
                  Status: {item.status || (isSuccess ? "SUCCESS" : "FAILED")}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}