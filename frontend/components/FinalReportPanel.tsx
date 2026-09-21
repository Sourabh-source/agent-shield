"use client";

import React, { useState } from "react";
import type { FinalReportData } from "@/lib/api";
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Ban,
  ShieldCheck,
  RefreshCw,
  Copy,
  Check,
  GitBranch,
} from "lucide-react";

interface FinalReportPanelProps {
  report: FinalReportData;
}

export default function FinalReportPanel({ report }: FinalReportPanelProps) {
  const [copied, setCopied] = useState(false);

  const isSuccess = report.final_status === "COMPLETED";
  const isFailed = ["VERIFIED_FAILURE", "FAILED", "BUDGET_EXCEEDED"].includes(report.final_status);
  const isUnavailable = report.final_status === "VERIFICATION_UNAVAILABLE";

  const successRate =
    report.total_steps > 0
      ? Math.round((report.steps_completed / report.total_steps) * 100)
      : 0;

  const copyJson = () => {
    navigator.clipboard.writeText(JSON.stringify(report, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-5 text-xs font-mono">
      {/* 1. Final Verdict Banner */}
      <div
        className={`p-5 rounded-lg border ${
          isSuccess
            ? "bg-emerald-950/20 border-emerald-800/80 text-emerald-200"
            : isFailed
            ? "bg-rose-950/20 border-rose-800/80 text-rose-200"
            : isUnavailable
            ? "bg-amber-950/20 border-amber-800/80 text-amber-200"
            : "bg-slate-900/60 border-slate-800 text-slate-300"
        }`}
      >
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3.5">
            {isSuccess ? (
              <CheckCircle2 className="w-8 h-8 text-emerald-400 shrink-0" />
            ) : isFailed ? (
              <XCircle className="w-8 h-8 text-rose-400 shrink-0" />
            ) : isUnavailable ? (
              <AlertTriangle className="w-8 h-8 text-amber-400 shrink-0" />
            ) : (
              <Ban className="w-8 h-8 text-slate-400 shrink-0" />
            )}
            <div>
              <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
                Authoritative Machine-Verified Verdict
              </div>
              <div
                className={`text-xl font-bold tracking-tight uppercase mt-0.5 ${
                  isSuccess
                    ? "text-emerald-400"
                    : isFailed
                    ? "text-rose-400"
                    : isUnavailable
                    ? "text-amber-400"
                    : "text-slate-300"
                }`}
              >
                {report.final_status === "COMPLETED" ? "VERIFIED SUCCESS" : report.final_status}
              </div>
              <div className="text-slate-400 text-xs mt-0.5 flex items-center gap-1.5 font-sans">
                <GitBranch className="w-3 h-3 text-slate-500" />
                <span>{report.repository}</span>
              </div>
            </div>
          </div>

          <button
            onClick={copyJson}
            className="px-3 py-1.5 rounded bg-slate-800/80 hover:bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition-colors flex items-center gap-1.5 self-start sm:self-center"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>Copy Report JSON</span>
          </button>
        </div>
      </div>

      {/* 2. Key Metrics Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="p-3.5 rounded-md border border-slate-800 bg-[#070b14]">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider block">Steps Verified</span>
          <div className="text-xl font-bold text-white font-mono mt-1">
            {report.steps_completed} <span className="text-slate-600 text-sm">/ {report.total_steps}</span>
          </div>
          <div className="mt-2 h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${isSuccess ? "bg-emerald-500" : "bg-rose-500"}`}
              style={{ width: `${successRate}%` }}
            />
          </div>
        </div>

        <div className="p-3.5 rounded-md border border-slate-800 bg-[#070b14]">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider block">Total Runtime</span>
          <div className="text-xl font-bold text-slate-200 font-mono mt-1">
            {report.duration_seconds.toFixed(2)}s
          </div>
          <span className="text-[10px] text-slate-500 mt-1 block">Wall-clock execution</span>
        </div>

        <div className="p-3.5 rounded-md border border-slate-800 bg-[#070b14]">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider block">Recoveries Triggered</span>
          <div className="text-xl font-bold text-yellow-300 font-mono mt-1">
            {report.recoveries}
          </div>
          <span className="text-[10px] text-slate-500 mt-1 block">Self-healing events</span>
        </div>

        <div className="p-3.5 rounded-md border border-slate-800 bg-[#070b14]">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider block">Step Retries</span>
          <div className="text-xl font-bold text-amber-300 font-mono mt-1">
            {report.retries}
          </div>
          <span className="text-[10px] text-slate-500 mt-1 block">Bounded retry executions</span>
        </div>
      </div>

      {/* 3. Task Objective */}
      <div className="p-3.5 rounded-md border border-slate-800 bg-[#0a0f1d]">
        <span className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold block mb-1">
          Verification Task
        </span>
        <p className="text-slate-200 font-sans leading-relaxed text-xs">{report.task}</p>
      </div>

      {/* 4. Verification Summary Breakdown Table */}
      {Object.keys(report.verification_summary || {}).length > 0 && (
        <div className="rounded-md border border-slate-800 bg-[#070b14] overflow-hidden">
          <div className="px-4 py-2.5 bg-[#0b1222] border-b border-slate-800 text-[11px] uppercase tracking-wider text-slate-400 font-semibold flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-blue-400" />
              <span>Step-Level Verification Audit</span>
            </span>
            <span>{Object.keys(report.verification_summary).length} Steps Evaluated</span>
          </div>

          <div className="divide-y divide-slate-850">
            {Object.entries(report.verification_summary).map(([step, verdict]) => {
              const passed =
                verdict === "VERIFIED" || verdict === "VERIFIED_SUCCESS" || verdict === "PASS";
              const skipped = verdict === "SKIPPED";

              return (
                <div
                  key={step}
                  className="px-4 py-2.5 flex items-center justify-between gap-3 hover:bg-slate-900/40 transition-colors"
                >
                  <span className="text-slate-300 font-medium text-xs truncate">{step}</span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold uppercase ${
                      passed
                        ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800/80"
                        : skipped
                        ? "bg-slate-900 text-slate-500 border border-slate-800"
                        : "bg-rose-950/60 text-rose-400 border border-rose-800/80"
                    }`}
                  >
                    {verdict}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 5. Recovery Interventions Audit */}
      {report.recovery_history && report.recovery_history.length > 0 && (
        <div className="rounded-md border border-yellow-800/60 bg-yellow-950/10 p-4 space-y-3">
          <div className="flex items-center gap-2 text-yellow-400 font-semibold text-[11px] uppercase tracking-wider">
            <RefreshCw className="w-3.5 h-3.5 text-yellow-400" />
            <span>Self-Healing Log ({report.recovery_history.length} Actions)</span>
          </div>

          <div className="space-y-2">
            {report.recovery_history.map((r, i) => (
              <div
                key={i}
                className="p-2.5 rounded bg-[#060a14] border border-yellow-900/60 flex items-center justify-between gap-3"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-200 font-medium">{String(r.step_name || r.action || "recovery")}</span>
                    {Boolean(r.failure_type) && (
                      <span className="text-[10px] px-1.5 py-0.2 rounded bg-yellow-950 text-yellow-400 border border-yellow-800">
                        {String(r.failure_type)}
                      </span>
                    )}
                  </div>
                  <code className="text-xs text-yellow-300/90 font-mono mt-0.5 block truncate">
                    {String(r.action || "")}
                  </code>
                </div>
                <span className="text-emerald-400 font-semibold text-[10px] uppercase shrink-0">
                  {String(r.status || "EXECUTED")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}