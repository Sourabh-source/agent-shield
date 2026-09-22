"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, isTerminal } from "@/lib/api";
import type { WorkflowState, FinalReportData } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import StepTimeline from "@/components/StepTimeline";
import EventsLog from "@/components/EventsLog";
import RecoveryPanel from "@/components/RecoveryPanel";
import FinalReportPanel from "@/components/FinalReportPanel";
import {
  ArrowLeft,
  GitBranch,
  Copy,
  Check,
  RefreshCw,
  Ban,
  Play,
  Clock,
  RotateCcw,
} from "lucide-react";

type Tab = "timeline" | "recovery" | "events" | "report";

export default function WorkflowDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;

  const [workflow, setWorkflow] = useState<WorkflowState | null>(null);
  const [report, setReport] = useState<FinalReportData | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("timeline");
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [copiedId, setCopiedId] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isTerminalRef = useRef(false);

  const fetchReport = useCallback(async (workflowId: string) => {
    try {
      const r = await api.getReport(workflowId);
      if (r && Object.keys(r).length > 0) {
        setReport(r);
      }
    } catch {
      // report will be available on completion
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    if (!id) return;
    try {
      const wf = await api.getStatus(id);
      setWorkflow(wf);
      setError(null);
      if (wf.final_report) {
        setReport(wf.final_report);
      }
      if (isTerminal(wf.overall_status)) {
        isTerminalRef.current = true;
        if (!wf.final_report) {
          await fetchReport(id);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch workflow state");
    }
  }, [id, fetchReport]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const poll = async () => {
      if (cancelled) return;
      await fetchStatus();
      if (!isTerminalRef.current && !cancelled) {
        pollingRef.current = setTimeout(poll, 1500);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (pollingRef.current) clearTimeout(pollingRef.current);
    };
  }, [id, fetchStatus]);

  useEffect(() => {
    if (activeTab === "report" && id && !report) {
      let isMounted = true;
      api
        .getReport(id)
        .then((r) => {
          if (isMounted && r && Object.keys(r).length > 0) {
            setReport(r);
          }
        })
        .catch(() => {});
      return () => {
        isMounted = false;
      };
    }
  }, [activeTab, id, report]);

  const handleCancel = async () => {
    if (!id) return;
    setCancelling(true);
    try {
      await api.cancelWorkflow(id);
      await fetchStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancellation failed");
    } finally {
      setCancelling(false);
    }
  };

  const handleResume = async () => {
    if (!id) return;
    setResuming(true);
    isTerminalRef.current = false;
    try {
      await api.resumeWorkflow(id);
      let count = 0;
      const poll = async () => {
        count++;
        await fetchStatus();
        if (!isTerminalRef.current && count < 200) {
          pollingRef.current = setTimeout(poll, 1500);
        }
      };
      setTimeout(poll, 500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Resume failed");
    } finally {
      setResuming(false);
    }
  };

  const copyId = () => {
    navigator.clipboard.writeText(id);
    setCopiedId(true);
    setTimeout(() => setCopiedId(false), 2000);
  };

  // Loading skeleton state
  if (!workflow && !error) {
    return (
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="h-6 w-32 bg-slate-900 animate-pulse rounded" />
        <div className="p-6 rounded-lg border border-slate-800 bg-[#0e1422] space-y-4">
          <div className="h-8 w-1/3 bg-slate-900 animate-pulse rounded" />
          <div className="h-4 w-1/2 bg-slate-900/60 animate-pulse rounded" />
          <div className="grid grid-cols-4 gap-3 pt-4 border-t border-slate-800">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-12 bg-slate-900/50 animate-pulse rounded" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (error && !workflow) {
    return (
      <div className="max-w-3xl mx-auto p-6 rounded-lg border border-rose-900 bg-[#120a0d] text-center space-y-3">
        <div className="text-rose-400 font-semibold text-sm">Failed to connect to workflow runner</div>
        <p className="text-xs text-slate-400">{error}</p>
        <div className="pt-2 flex items-center justify-center gap-3">
          <button
            onClick={fetchStatus}
            className="px-4 py-2 rounded bg-slate-800 hover:bg-slate-700 text-xs text-white border border-slate-700"
          >
            Retry Connection
          </button>
          <Link href="/" className="text-xs text-blue-400 hover:underline">
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  if (!workflow) return null;

  const terminal = isTerminal(workflow.overall_status);
  const canCancel = !terminal && !["CANCEL_REQUESTED", "CANCELLED"].includes(workflow.overall_status);
  const canResume = workflow.overall_status === "CANCELLED";
  const totalSteps = workflow.steps.length;
  const verifiedSteps = workflow.steps.filter((s) =>
    ["SUCCESS", "VERIFIED_SUCCESS"].includes(s.status)
  ).length;
  const notApplicableSteps = workflow.steps.filter((s) =>
    ["NOT_APPLICABLE", "SKIPPED"].includes(s.status)
  ).length;
  const failedSteps = workflow.steps.filter((s) => s.status === "FAILED").length;

  const completedTerminalSteps = verifiedSteps + notApplicableSteps + failedSteps;
  const progressPct =
    totalSteps > 0
      ? Math.round((completedTerminalSteps / totalSteps) * 100)
      : 0;

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "timeline", label: "Pipeline Timeline", count: workflow.steps.length },
    { id: "recovery", label: "Self-Healing", count: workflow.recovery_history?.length || 0 },
    { id: "events", label: "Activity Audit Log", count: workflow.events?.length || 0 },
    { id: "report", label: "Final Report" },
  ];

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Top Breadcrumb & ID */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs font-mono">
        <div className="flex items-center gap-2 text-slate-500">
          <Link href="/" className="hover:text-slate-300 transition-colors flex items-center gap-1">
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Dashboard</span>
          </Link>
          <span>/</span>
          <Link href="/workflows" className="hover:text-slate-300 transition-colors">
            Workflows
          </Link>
          <span>/</span>
          <span className="text-slate-300">{id.slice(0, 10)}</span>
        </div>

        <button
          onClick={copyId}
          className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 self-start sm:self-auto py-1 px-2 rounded bg-slate-900/60 border border-slate-800"
          title="Copy Workflow ID"
        >
          {copiedId ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
          <span>{id}</span>
        </button>
      </div>

      {/* Main Execution Header Card */}
      <div className="rounded-lg border border-slate-800 bg-[#0e1422] p-5 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="space-y-1.5 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <StatusBadge status={workflow.overall_status} size="md" pulse={!terminal} />
              {workflow.dry_run && (
                <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-purple-950/60 border border-purple-800/80 text-purple-300">
                  DRY RUN
                </span>
              )}
            </div>

            <div className="flex items-center gap-2 text-white text-base font-semibold tracking-tight">
              <GitBranch className="w-4 h-4 text-slate-400 shrink-0" />
              <span className="truncate font-mono">{workflow.repository}</span>
            </div>

            <p className="text-xs text-slate-400 font-sans leading-relaxed max-w-2xl">
              {workflow.task}
            </p>
          </div>

          {/* Controls */}
          <div className="flex items-center gap-2 shrink-0">
            {canCancel && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="px-3.5 py-1.5 rounded text-xs font-semibold uppercase tracking-wider text-rose-300 bg-rose-950/60 hover:bg-rose-900 border border-rose-800 transition-colors flex items-center gap-1.5"
              >
                <Ban className="w-3.5 h-3.5" />
                <span>{cancelling ? "Halting..." : "Cancel Run"}</span>
              </button>
            )}

            {canResume && (
              <button
                onClick={handleResume}
                disabled={resuming}
                className="px-3.5 py-1.5 rounded text-xs font-semibold uppercase tracking-wider text-blue-300 bg-blue-950/60 hover:bg-blue-900 border border-blue-800 transition-colors flex items-center gap-1.5"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>{resuming ? "Resuming..." : "Resume Run"}</span>
              </button>
            )}

            <button
              onClick={() => router.push("/new")}
              className="px-3.5 py-1.5 rounded text-xs font-semibold uppercase tracking-wider text-slate-300 bg-slate-900 hover:bg-slate-800 border border-slate-700 transition-colors flex items-center gap-1.5"
            >
              <Play className="w-3 h-3" />
              <span>New Run</span>
            </button>
          </div>
        </div>

        {/* Runtime Metrics Bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-4 border-t border-slate-800/80 text-xs font-mono">
          <div className="p-2.5 rounded bg-[#070b14] border border-slate-800">
            <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Steps Status</span>
            <div className="text-sm font-semibold text-slate-200 mt-0.5">
              {notApplicableSteps > 0
                ? `${verifiedSteps} verified / ${notApplicableSteps} not applicable`
                : `${verifiedSteps} / ${totalSteps} verified`}
            </div>
          </div>

          <div className="p-2.5 rounded bg-[#070b14] border border-slate-800">
            <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Step Retries</span>
            <div className="text-sm font-semibold text-slate-200 mt-0.5">
              {workflow.retries} / {workflow.max_retries} max
            </div>
          </div>

          <div className="p-2.5 rounded bg-[#070b14] border border-slate-800">
            <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Recoveries</span>
            <div className="text-sm font-semibold text-yellow-300 mt-0.5">
              {workflow.recovery_history ? workflow.recovery_history.length : 0} triggered
            </div>
          </div>

          <div className="p-2.5 rounded bg-[#070b14] border border-slate-800">
            <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Active Step</span>
            <div className="text-sm font-semibold text-sky-400 mt-0.5 truncate">
              {workflow.current_step || (terminal ? "Finished" : "Initializing")}
            </div>
          </div>
        </div>

        {/* Progress bar */}
        {!terminal && workflow.steps.length > 0 && (
          <div className="space-y-1 pt-1">
            <div className="flex items-center justify-between text-[11px] font-mono text-slate-500">
              <span>Pipeline Execution Progress</span>
              <span>{progressPct}%</span>
            </div>
            <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Structured Pipeline Tabs */}
      <div className="space-y-4">
        {/* Tab Selection */}
        <div className="flex items-center gap-1 border-b border-slate-800 text-xs font-mono">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2.5 border-b-2 font-medium transition-colors flex items-center gap-2 -mb-px ${
                activeTab === tab.id
                  ? "border-blue-500 text-blue-400 bg-blue-950/20"
                  : "border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-900/40"
              }`}
            >
              <span>{tab.label}</span>
              {tab.count !== undefined && tab.count > 0 && (
                <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-400">
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Tab Contents Card */}
        <div className="rounded-lg border border-slate-800 bg-[#0e1422] p-5">
          {activeTab === "timeline" && (
            <StepTimeline steps={workflow.steps} currentStep={workflow.current_step} />
          )}

          {activeTab === "recovery" && (
            <RecoveryPanel
              recoveryHistory={workflow.recovery_history || []}
              currentStatus={workflow.overall_status}
            />
          )}

          {activeTab === "events" && <EventsLog events={workflow.events || []} />}

          {activeTab === "report" && (() => {
            const currentReport =
              report ||
              workflow.final_report ||
              (workflow.metadata?.final_report as FinalReportData | undefined) ||
              null;

            if (currentReport) {
              return <FinalReportPanel report={currentReport} />;
            }
            if (terminal) {
              return (
                <div className="py-12 text-center text-xs text-slate-500 font-mono">
                  <RefreshCw className="w-5 h-5 mx-auto mb-2 text-blue-400 animate-spin" />
                  <p>Synthesizing final audit report...</p>
                </div>
              );
            }
            return (
              <div className="py-12 text-center text-xs text-slate-500 font-mono">
                <Clock className="w-5 h-5 mx-auto mb-2 text-slate-600" />
                <p>Final report will be compiled automatically upon terminal workflow state.</p>
              </div>
            );
          })()}
        </div>
      </div>
    </div>
  );
}