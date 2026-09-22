"use client";

import React, { useState } from "react";
import { formatDuration, formatTimestamp } from "@/lib/api";
import type { StepDefinition, ExecutionResult, VerificationResult } from "@/lib/api";
import {
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
  Clock,
  FastForward,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Terminal,
  ShieldCheck,
} from "lucide-react";

interface StepTimelineProps {
  steps: StepDefinition[];
  currentStep?: string;
}

function StepIcon({ status }: { status: string }) {
  const norm = (status || "").toUpperCase();
  if (norm === "SUCCESS" || norm === "VERIFIED_SUCCESS") {
    return <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />;
  }
  if (norm === "FAILED") {
    return <XCircle className="w-4 h-4 text-rose-400 shrink-0" />;
  }
  if (norm === "RUNNING" || norm === "VERIFYING") {
    return <Loader2 className="w-4 h-4 text-sky-400 shrink-0 animate-spin" />;
  }
  if (norm === "RECOVERING") {
    return <RefreshCw className="w-4 h-4 text-yellow-400 shrink-0 animate-spin" />;
  }
  if (norm === "SKIPPED") {
    return <FastForward className="w-4 h-4 text-slate-500 shrink-0" />;
  }
  return <Clock className="w-4 h-4 text-slate-500 shrink-0" />;
}

interface EvidencePanelProps {
  execution_result?: ExecutionResult | null;
  verification_result?: VerificationResult | null;
}

function EvidencePanel({ execution_result, verification_result }: EvidencePanelProps) {
  const [copied, setCopied] = useState(false);

  const copyCommand = () => {
    if (execution_result?.command) {
      navigator.clipboard.writeText(execution_result.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!execution_result && !verification_result) {
    return (
      <div className="p-4 rounded border border-slate-800 bg-[#070b14] text-xs text-slate-500 font-mono">
        No execution evidence recorded yet for this step.
      </div>
    );
  }

  return (
    <div className="space-y-3 pt-2 text-xs font-mono">
      {/* 1. Execution Evidence Header */}
      {execution_result && (
        <div className="rounded-md border border-slate-800 bg-[#080d1a] overflow-hidden">
          {/* Metadata bar */}
          <div className="bg-[#0b1222] px-3.5 py-2 border-b border-slate-800 flex items-center justify-between gap-3 flex-wrap text-[11px]">
            <div className="flex items-center gap-3">
              <span className="text-slate-400 uppercase tracking-wider font-semibold flex items-center gap-1.5">
                <Terminal className="w-3.5 h-3.5 text-blue-400" />
                <span>Execution Evidence</span>
              </span>
              <span
                className={`px-2 py-0.5 rounded font-mono font-semibold ${
                  execution_result.exit_code === 0
                    ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800/80"
                    : "bg-rose-950/60 text-rose-400 border border-rose-800/80"
                }`}
              >
                EXIT CODE: {execution_result.exit_code}
              </span>
            </div>

            <div className="flex items-center gap-4 text-slate-400">
              {execution_result.execution_id && (
                <span>
                  ID: <span className="text-slate-200">{execution_result.execution_id}</span>
                </span>
              )}
              {execution_result.timestamp && (
                <span>
                  UTC: <span className="text-slate-200">{formatTimestamp(execution_result.timestamp)}</span>
                </span>
              )}
              {execution_result.duration_ms > 0 && (
                <span>
                  DURATION: <span className="text-slate-200">{formatDuration(execution_result.duration_ms)}</span>
                </span>
              )}
            </div>
          </div>

          {/* Command display */}
          {execution_result.command && (
            <div className="p-3 border-b border-slate-800/80 bg-[#050811] flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0 overflow-x-auto text-slate-200 font-mono">
                <span className="text-blue-500 select-none">$</span>
                <code className="text-xs text-sky-300 truncate">{execution_result.command}</code>
              </div>
              <button
                onClick={copyCommand}
                className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors shrink-0"
                title="Copy Command"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
          )}

          {/* STDOUT Console */}
          {execution_result.stdout && (
            <div className="p-3 border-b border-slate-800/80">
              <div className="text-[10px] uppercase tracking-wider text-slate-400 font-semibold mb-1 flex items-center gap-1">
                <span>Standard Output</span>
                <span className="text-slate-600 font-normal">({execution_result.stdout.length} bytes)</span>
              </div>
              <pre className="p-2.5 rounded bg-[#04060d] text-emerald-300/90 text-xs overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed border border-slate-900 font-mono">
                {execution_result.stdout}
              </pre>
            </div>
          )}

          {/* STDERR Console */}
          {execution_result.stderr && (
            <div className="p-3 bg-rose-950/10">
              <div className="text-[10px] uppercase tracking-wider text-rose-400 font-semibold mb-1 flex items-center gap-1">
                <span>Standard Error / Trace</span>
                <span className="text-rose-600 font-normal">({execution_result.stderr.length} bytes)</span>
              </div>
              <pre className="p-2.5 rounded bg-[#0b0406] text-rose-300 text-xs overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed border border-rose-950/80 font-mono">
                {execution_result.stderr}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* 2. Machine Evidence Verification Decision Box */}
      {verification_result && (
        <div
          className={`rounded-md p-3.5 border ${
            verification_result.verified
              ? "bg-emerald-950/20 border-emerald-800/60 text-emerald-200"
              : "bg-rose-950/20 border-rose-800/60 text-rose-200"
          }`}
        >
          <div className="flex items-center justify-between gap-3 mb-2">
            <div className="flex items-center gap-2">
              <ShieldCheck
                className={`w-4 h-4 ${verification_result.verified ? "text-emerald-400" : "text-rose-400"}`}
              />
              <span className="text-xs font-semibold uppercase tracking-wider">
                Machine Evidence Gate Decision:{" "}
                <span className={verification_result.verified ? "text-emerald-400" : "text-rose-400"}>
                  {verification_result.verified ? "PASS" : "FAILED"}
                </span>
              </span>
            </div>

            {verification_result.failure_type && (
              <span className="text-[10px] px-2 py-0.5 rounded bg-rose-950/60 text-rose-300 border border-rose-800/60 uppercase">
                {verification_result.failure_type}
              </span>
            )}
          </div>

          {verification_result.reason && (
            <p className="text-xs text-slate-300 leading-relaxed font-sans mb-2">
              <span className="font-semibold text-slate-400 font-mono">Reason: </span>
              {verification_result.reason}
            </p>
          )}

          {/* Recovery Required Banner */}
          {verification_result.recovery_required && (
            <div className="mt-2.5 p-2.5 rounded bg-yellow-950/30 border border-yellow-800/60 text-yellow-300 text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <RefreshCw className="w-3.5 h-3.5 text-yellow-400 shrink-0 animate-spin" />
                <span className="font-medium">Self-Healing Required:</span>
                <code className="text-yellow-200 bg-yellow-950/80 px-1.5 py-0.5 rounded border border-yellow-800/80">
                  {verification_result.recovery_action || "Automatic diagnostic plan"}
                </code>
              </div>
              <span className="text-[10px] text-yellow-500 uppercase">
                Retry Allowed: {verification_result.retry_allowed ? "YES" : "NO"}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function StepTimeline({ steps, currentStep }: StepTimelineProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!steps || steps.length === 0) {
    return (
      <div className="text-center py-12 text-slate-500 font-mono text-xs">
        <Clock className="w-6 h-6 mx-auto mb-2 text-slate-600 animate-pulse" />
        <p>Synthesizing execution plan...</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {steps.map((step, index) => {
        const isExpanded = expandedId === step.id;
        const isActive =
          (step.name === currentStep || step.id === currentStep) &&
          ["RUNNING", "VERIFYING", "RECOVERING"].includes(step.status);
        const isVerified = step.status === "VERIFIED_SUCCESS" || step.status === "SUCCESS";
        const isFailed = step.status === "FAILED";

        return (
          <div
            key={step.id}
            className={`rounded-md border transition-colors ${
              isActive
                ? "bg-[#0d1424] border-blue-600/80 ring-1 ring-blue-500/40"
                : isFailed
                ? "bg-[#140b0f] border-rose-900/60"
                : isVerified
                ? "bg-[#0b121e] border-slate-800 hover:border-slate-700"
                : "bg-[#0a0f1b] border-slate-800/80"
            }`}
          >
            {/* Step Row Header */}
            <button
              type="button"
              onClick={() => setExpandedId(isExpanded ? null : step.id)}
              className="w-full text-left px-3.5 py-2.5 flex items-center justify-between gap-3 text-xs focus:outline-none"
            >
              <div className="flex items-center gap-3 min-w-0">
                <StepIcon status={step.status} />

                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono text-slate-500 text-[11px]">0{index + 1}</span>
                  <span className="font-medium text-slate-200 truncate">{step.name}</span>
                </div>

                {/* Tool Badge */}
                {step.tool && (
                  <span className="hidden sm:inline text-[10px] font-mono px-1.5 py-0.2 rounded bg-slate-800/80 text-slate-400 border border-slate-700/60">
                    tool:{step.tool}
                  </span>
                )}

                {/* Retries counter */}
                {step.retries > 0 && (
                  <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-yellow-950/60 text-yellow-300 border border-yellow-800/60">
                    retry {step.retries}x
                  </span>
                )}

                {/* Active pulse */}
                {isActive && (
                  <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-blue-950 text-blue-300 border border-blue-800 animate-pulse">
                    ACTIVE
                  </span>
                )}
              </div>

              <div className="flex items-center gap-3 shrink-0 font-mono text-[11px]">
                {/* Duration */}
                {step.execution_result?.duration_ms ? (
                  <span className="text-slate-400">
                    {formatDuration(step.execution_result.duration_ms)}
                  </span>
                ) : null}

                {/* Step Status Verdict */}
                <span
                  className={`font-semibold uppercase ${
                    isVerified
                      ? "text-emerald-400"
                      : isFailed
                      ? "text-rose-400"
                      : isActive
                      ? "text-sky-400"
                      : "text-slate-500"
                  }`}
                >
                  {step.status === "VERIFIED_SUCCESS" ? "PASS" : step.status}
                </span>

                {isExpanded ? (
                  <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5 text-slate-500" />
                )}
              </div>
            </button>

            {/* Expandable Debugging Evidence Box */}
            {isExpanded && (
              <div className="px-3.5 pb-3.5 border-t border-slate-800/60 bg-[#060913]">
                {step.description && (
                  <p className="text-xs text-slate-400 mt-2 mb-2 font-sans">{step.description}</p>
                )}
                <EvidencePanel
                  execution_result={step.execution_result}
                  verification_result={step.verification_result}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}