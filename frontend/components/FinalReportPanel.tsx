"use client";

import React, { useState } from "react";
import type { FinalReportData, RecommendedFix } from "@/lib/api";
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
  ChevronDown,
  ChevronRight,
  Wrench,
} from "lucide-react";

interface FinalReportPanelProps {
  report: FinalReportData;
}

export default function FinalReportPanel({ report }: FinalReportPanelProps) {
  const [copied, setCopied] = useState(false);
  const fixes = report.recommended_fixes || [];
  const fixesSummary = report.fixes_summary || {
    issues_found: fixes.length,
    recovered_automatically: fixes.filter((f) => f.status === "RECOVERED").length,
    action_required: fixes.filter((f) => f.status === "ACTION REQUIRED").length,
    retries: fixes.reduce((sum, f) => sum + (f.retries || 0), 0),
  };

  const [expandedFixes, setExpandedFixes] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    fixes.forEach((f, idx) => {
      initial[f.id || `fix-${idx}`] = true;
    });
    return initial;
  });

  const toggleFix = (id: string) => {
    setExpandedFixes((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  const allExpanded = fixes.length > 0 && fixes.every((f, idx) => expandedFixes[f.id || `fix-${idx}`]);

  const toggleAllFixes = () => {
    const nextState = !allExpanded;
    const updated: Record<string, boolean> = {};
    fixes.forEach((f, idx) => {
      updated[f.id || `fix-${idx}`] = nextState;
    });
    setExpandedFixes(updated);
  };

  const isSuccess =
    report.final_status === "COMPLETED" ||
    report.final_status === "VERIFIED_SUCCESS" ||
    report.final_status === "PASS";
  const isFailed = ["VERIFIED_FAILURE", "FAILED", "BUDGET_EXCEEDED"].includes(report.final_status);
  const isUnavailable = report.final_status === "VERIFICATION_UNAVAILABLE";

  const verifiedSteps = report.verified_steps ?? report.steps_completed;
  const notApplicableSteps = report.not_applicable_steps ?? 0;
  const totalSteps = report.total_steps;
  const successRate =
    totalSteps > 0
      ? Math.round((verifiedSteps / totalSteps) * 100)
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
            {verifiedSteps} <span className="text-slate-600 text-sm">/ {totalSteps}</span>
          </div>
          {notApplicableSteps > 0 ? (
            <span className="text-[10px] text-slate-400 mt-1 block">
              {verifiedSteps} verified / {notApplicableSteps} not applicable
            </span>
          ) : (
            <span className="text-[10px] text-slate-500 mt-1 block">
              {verifiedSteps} of {totalSteps} verified
            </span>
          )}
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

      {/* 3. Recommended Fixes Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Wrench className="w-4 h-4 text-blue-400" />
            <span className="text-xs uppercase tracking-wider text-slate-300 font-semibold">
              Recommended Fixes
            </span>
            {fixes.length > 0 && (
              <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-slate-800 text-slate-300 border border-slate-700">
                {fixes.length}
              </span>
            )}
          </div>

          {fixes.length > 0 && (
            <button
              type="button"
              onClick={toggleAllFixes}
              className="text-[11px] text-slate-400 hover:text-slate-200 font-sans transition-colors cursor-pointer"
            >
              {allExpanded ? "Collapse All" : "Expand All"}
            </button>
          )}
        </div>

        {fixes.length === 0 ? (
          <div className="p-4 rounded-md border border-emerald-900/60 bg-emerald-950/20 text-emerald-300 flex items-center gap-3">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
            <div>
              <span className="font-semibold text-xs block text-emerald-300">
                ✓ No outstanding issues detected
              </span>
              <span className="text-[11px] text-emerald-500 font-sans block mt-0.5">
                All planned steps passed verification without unrecovered failures or pending interventions.
              </span>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Summary at top */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 p-3 rounded-md border border-slate-800 bg-[#070b14]">
              <div className="p-2 rounded bg-slate-900/50 border border-slate-800/80">
                <span className="text-[10px] text-slate-400 uppercase tracking-wider block">Issues Found</span>
                <span className="text-lg font-bold text-slate-200 font-mono mt-0.5 block">
                  {fixesSummary.issues_found}
                </span>
              </div>
              <div className="p-2 rounded bg-emerald-950/20 border border-emerald-900/40">
                <span className="text-[10px] text-emerald-400 uppercase tracking-wider block">
                  Recovered Automatically
                </span>
                <span className="text-lg font-bold text-emerald-400 font-mono mt-0.5 block">
                  {fixesSummary.recovered_automatically}
                </span>
              </div>
              <div className="p-2 rounded bg-rose-950/20 border border-rose-900/40">
                <span className="text-[10px] text-rose-400 uppercase tracking-wider block">
                  Action Required
                </span>
                <span className="text-lg font-bold text-rose-400 font-mono mt-0.5 block">
                  {fixesSummary.action_required}
                </span>
              </div>
              <div className="p-2 rounded bg-amber-950/20 border border-amber-900/40">
                <span className="text-[10px] text-amber-400 uppercase tracking-wider block">Retries</span>
                <span className="text-lg font-bold text-amber-300 font-mono mt-0.5 block">
                  {fixesSummary.retries}
                </span>
              </div>
            </div>

            {/* Expandable Fix Cards */}
            <div className="space-y-2.5">
              {fixes.map((fix, idx) => {
                const fixKey = fix.id || `fix-${idx}`;
                const isExpanded = expandedFixes[fixKey] ?? true;
                const isRecovered = fix.status === "RECOVERED";
                const isActionRequired = fix.status === "ACTION REQUIRED";
                const isUnverified = fix.status === "UNVERIFIED";

                return (
                  <div
                    key={fixKey}
                    className={`rounded-md border transition-all ${
                      isRecovered
                        ? "bg-[#060e14] border-emerald-900/60"
                        : isUnverified
                        ? "bg-[#0f0e0a] border-amber-900/60"
                        : "bg-[#0d070b] border-rose-900/60"
                    }`}
                  >
                    {/* Card Header (clickable) */}
                    <button
                      type="button"
                      onClick={() => toggleFix(fixKey)}
                      className="w-full text-left p-3.5 flex items-start sm:items-center justify-between gap-3 hover:bg-slate-900/30 transition-colors cursor-pointer"
                    >
                      <div className="flex flex-col sm:flex-row sm:items-center gap-2.5 min-w-0">
                        <div className="flex items-center gap-2 shrink-0">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase tracking-wider ${
                              isRecovered
                                ? "bg-emerald-950 text-emerald-400 border border-emerald-700/80"
                                : isUnverified
                                ? "bg-amber-950 text-amber-300 border border-amber-700/80"
                                : "bg-rose-950 text-rose-400 border border-rose-700/80"
                            }`}
                          >
                            {fix.status}
                          </span>
                          <span className="text-xs font-mono font-bold text-slate-400">{fix.id}</span>
                        </div>

                        <div className="min-w-0">
                          <span className="text-xs font-semibold text-slate-200 block truncate">
                            {fix.title}
                          </span>
                          <span className="text-[11px] text-slate-400 font-sans block mt-0.5">
                            Step: <span className="text-slate-300 font-mono">{fix.step}</span>
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2.5 shrink-0 self-start sm:self-center">
                        {(fix.retries ?? 0) > 0 && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-950/60 text-amber-300 border border-amber-800/60">
                            Retries: {fix.retries}
                          </span>
                        )}
                        {isExpanded ? (
                          <ChevronDown className="w-4 h-4 text-slate-400" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-slate-400" />
                        )}
                      </div>
                    </button>

                    {/* Card Body */}
                    {isExpanded && (
                      <div className="px-4 pb-4 pt-1 border-t border-slate-800/60 space-y-3 font-sans text-xs">
                        <div>
                          <span className="text-[10px] font-mono font-semibold uppercase tracking-wider text-slate-400 block mb-1">
                            What happened
                          </span>
                          <p className="text-slate-200 leading-relaxed bg-[#050811] p-2.5 rounded border border-slate-800 font-mono text-[11px]">
                            {fix.what_happened}
                          </p>
                        </div>

                        <div>
                          <span className="text-[10px] font-mono font-semibold uppercase tracking-wider text-slate-400 block mb-1">
                            Diagnosis
                          </span>
                          <p className="text-slate-300 leading-relaxed bg-[#050811] p-2.5 rounded border border-slate-800">
                            {fix.diagnosis}
                          </p>
                        </div>

                        <div>
                          <span className="text-[10px] font-mono font-semibold uppercase tracking-wider text-blue-400 block mb-1">
                            Recommended fix
                          </span>
                          <div className="p-2.5 rounded bg-blue-950/30 border border-blue-900/60 text-blue-200 font-mono text-[11px] leading-relaxed select-text">
                            {fix.recommended_fix}
                          </div>
                        </div>

                        {fix.recovery_attempted && (
                          <div>
                            <span className="text-[10px] font-mono font-semibold uppercase tracking-wider text-yellow-400 block mb-1">
                              Recovery attempted
                            </span>
                            <div className="p-2.5 rounded bg-yellow-950/20 border border-yellow-900/40 text-yellow-200 font-mono text-[11px]">
                              <code>{fix.recovery_attempted}</code>
                              {fix.recovery_result && (
                                <div className="mt-1 text-[11px] text-slate-300 font-sans">
                                  Result:{" "}
                                  <span
                                    className={
                                      isRecovered
                                        ? "text-emerald-400 font-semibold"
                                        : "text-rose-400 font-semibold"
                                    }
                                  >
                                    {fix.recovery_result}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* 4. Task Objective & Summary */}
      <div className="space-y-3">
        {report.summary && (
          <div className="p-3.5 rounded-md border border-blue-900/50 bg-[#091024]">
            <span className="text-[11px] text-blue-400 uppercase tracking-wider font-semibold block mb-1">
              Audit Summary
            </span>
            <p className="text-slate-200 font-sans leading-relaxed text-xs">{report.summary}</p>
          </div>
        )}

        <div className="p-3.5 rounded-md border border-slate-800 bg-[#0a0f1d]">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold block mb-1">
            Verification Task
          </span>
          <p className="text-slate-200 font-sans leading-relaxed text-xs">{report.task}</p>
        </div>
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
              const notApplicable =
                verdict === "NOT_APPLICABLE" || verdict === "SKIPPED" || verdict === "NA";
              const incomplete =
                verdict === "INCOMPLETE" || verdict === "PARTIALLY_SATISFIED";

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
                        : notApplicable
                        ? "bg-slate-900 text-slate-400 border border-slate-700"
                        : incomplete
                        ? "bg-amber-950/60 text-amber-300 border border-amber-800/80"
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