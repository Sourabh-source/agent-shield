"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import Link from "next/link";
import { api, formatTimestamp, getWorkflowDuration } from "@/lib/api";
import type { WorkflowState } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import {
  Layers,
  PlusCircle,
  RefreshCw,
  GitBranch,
  CheckCircle2,
  XCircle,
  Activity,
  ArrowUpRight,
  Search,
  AlertCircle,
} from "lucide-react";

export default function DashboardPage() {
  const [workflows, setWorkflows] = useState<WorkflowState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  // Step 8: Standardized data-fetching pattern
  const fetchWorkflows = useCallback(async (isUserInitiated = false) => {
    if (isUserInitiated) {
      setLoading(true);
      setError(null);
    }
    try {
      const data = await api.listWorkflows();
      setWorkflows(data || []);
      setError(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unable to load workflow history";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    let isFetching = false;

    // Initial load: fetch workflows via promise callbacks
    api.listWorkflows()
      .then((data) => {
        if (mounted) {
          setWorkflows(data || []);
          setError(null);
        }
      })
      .catch((err) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Unable to load workflow history");
        }
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    // Step 3: Controlled periodic background refresh (avoids request flooding)
    const interval = setInterval(async () => {
      if (!mounted || isFetching) return;
      isFetching = true;
      try {
        const data = await api.listWorkflows();
        if (mounted) {
          setWorkflows(data || []);
          setError(null);
        }
      } catch {
        // Keep existing cached data if a transient background poll fails
      } finally {
        isFetching = false;
      }
    }, 8000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  // Compute real metrics from loaded workflows
  const stats = useMemo(() => {
    const total = workflows.length;
    const success = workflows.filter((w) => w.overall_status === "COMPLETED").length;
    const failures = workflows.filter((w) =>
      ["VERIFIED_FAILURE", "FAILED", "BUDGET_EXCEEDED"].includes(w.overall_status)
    ).length;
    const recoveries = workflows.reduce(
      (sum, w) => sum + (w.recovery_history ? w.recovery_history.length : 0),
      0
    );
    const active = workflows.filter((w) =>
      ["RUNNING", "PLANNING", "VERIFYING", "RECOVERING", "PENDING"].includes(w.overall_status)
    ).length;

    return { total, success, failures, recoveries, active };
  }, [workflows]);

  // Filtered workflows list
  const filteredWorkflows = useMemo(() => {
    return [...workflows]
      .reverse()
      .filter((w) => {
        const matchesSearch =
          w.repository.toLowerCase().includes(search.toLowerCase()) ||
          w.workflow_id.toLowerCase().includes(search.toLowerCase()) ||
          w.task.toLowerCase().includes(search.toLowerCase());

        if (!matchesSearch) return false;

        if (statusFilter === "SUCCESS") return w.overall_status === "COMPLETED";
        if (statusFilter === "FAILURE")
          return ["VERIFIED_FAILURE", "FAILED", "BUDGET_EXCEEDED"].includes(w.overall_status);
        if (statusFilter === "ACTIVE")
          return ["RUNNING", "PLANNING", "VERIFYING", "RECOVERING"].includes(w.overall_status);

        return true;
      });
  }, [workflows, search, statusFilter]);

  return (
    <div className="space-y-6">
      {/* Platform Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-white">Engineering Dashboard</h1>
            <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-blue-950/60 border border-blue-800/60 text-blue-400">
              Live Console
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            AgentGuard • Evidence-Gated Autonomous Software Verification & Self-Healing
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchWorkflows(true)}
            disabled={loading}
            className="p-2 rounded-md border border-slate-700/80 bg-slate-900/60 hover:bg-slate-800 text-slate-300 transition-colors"
            title="Refresh Data"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-blue-400" : ""}`} />
          </button>
          <Link
            href="/new"
            className="px-4 py-2 text-xs font-semibold uppercase tracking-wider text-white bg-blue-600 hover:bg-blue-500 rounded-md border border-blue-500/60 transition-colors flex items-center gap-1.5 shadow-sm"
          >
            <PlusCircle className="w-3.5 h-3.5" />
            <span>New Workflow Run</span>
          </Link>
        </div>
      </div>

      {/* KPI Metrics Bar (Real data from backend) */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <div className="p-3.5 rounded-lg border border-slate-800 bg-[#0e1422]">
          <span className="text-[11px] font-mono text-slate-400 block uppercase tracking-wider">Total Workflows</span>
          <div className="text-2xl font-bold text-white font-mono mt-1">{stats.total}</div>
          <span className="text-[10px] text-slate-500 mt-0.5 block">Recorded executions</span>
        </div>

        <div className="p-3.5 rounded-lg border border-emerald-950/60 bg-emerald-950/20">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-emerald-400 block uppercase tracking-wider">Verified Success</span>
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold text-emerald-300 font-mono mt-1">{stats.success}</div>
          <span className="text-[10px] text-emerald-500/80 mt-0.5 block">Evidence verified</span>
        </div>

        <div className="p-3.5 rounded-lg border border-rose-950/60 bg-rose-950/20">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-rose-400 block uppercase tracking-wider">Verified Failures</span>
            <XCircle className="w-3.5 h-3.5 text-rose-400" />
          </div>
          <div className="text-2xl font-bold text-rose-300 font-mono mt-1">{stats.failures}</div>
          <span className="text-[10px] text-rose-500/80 mt-0.5 block">Zero fake passes</span>
        </div>

        <div className="p-3.5 rounded-lg border border-yellow-950/60 bg-yellow-950/20">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-yellow-400 block uppercase tracking-wider">Self-Healing</span>
            <RefreshCw className="w-3.5 h-3.5 text-yellow-400" />
          </div>
          <div className="text-2xl font-bold text-yellow-300 font-mono mt-1">{stats.recoveries}</div>
          <span className="text-[10px] text-yellow-500/80 mt-0.5 block">Automated recoveries</span>
        </div>

        <div className="p-3.5 rounded-lg border border-sky-950/60 bg-sky-950/20 col-span-2 sm:col-span-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-sky-400 block uppercase tracking-wider">In Flight</span>
            <Activity className="w-3.5 h-3.5 text-sky-400" />
          </div>
          <div className="text-2xl font-bold text-sky-300 font-mono mt-1">{stats.active}</div>
          <span className="text-[10px] text-sky-500/80 mt-0.5 block">Active pipelines</span>
        </div>
      </div>

      {/* Main Table Card */}
      <div className="border border-slate-800 rounded-lg bg-[#0e1422] overflow-hidden">
        {/* Table Toolbar */}
        <div className="p-3.5 border-b border-slate-800/80 flex flex-col sm:flex-row items-center justify-between gap-3 bg-[#0a0f1d]">
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5 uppercase font-mono tracking-wider">
              <Layers className="w-3.5 h-3.5 text-blue-400" />
              <span>Recent Executions</span>
            </span>
            <span className="text-xs text-slate-500 font-mono">({filteredWorkflows.length})</span>
          </div>

          <div className="flex items-center gap-2 w-full sm:w-auto">
            {/* Search Input */}
            <div className="relative flex-1 sm:w-60">
              <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2.5" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter repo or task..."
                className="w-full pl-8 pr-3 py-1.5 bg-[#070b14] border border-slate-700/80 rounded text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 font-mono"
              />
            </div>

            {/* Status Filter */}
            <div className="flex items-center rounded border border-slate-700/80 p-0.5 bg-[#070b14] text-[11px] font-mono">
              {["ALL", "SUCCESS", "FAILURE", "ACTIVE"].map((f) => (
                <button
                  key={f}
                  onClick={() => setStatusFilter(f)}
                  className={`px-2 py-1 rounded transition-colors ${
                    statusFilter === f
                      ? "bg-slate-800 text-white font-semibold"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Error message banner (if workflows exist but background refresh failed) */}
        {error && workflows.length > 0 && (
          <div className="p-3 bg-rose-950/40 border-b border-rose-900/60 text-xs text-rose-300 flex items-center justify-between">
            <span>{error}</span>
            <button
              onClick={() => fetchWorkflows(true)}
              className="px-2 py-0.5 rounded text-[11px] font-semibold bg-rose-900/60 hover:bg-rose-800 text-rose-200 border border-rose-700"
            >
              Retry
            </button>
          </div>
        )}

        {/* Step 3: Explicit Error State when initial load fails */}
        {!loading && error && workflows.length === 0 && (
          <div className="py-16 px-4 text-center">
            <div className="w-10 h-10 rounded-full bg-rose-950/60 border border-rose-800/80 flex items-center justify-center mx-auto mb-3 text-rose-400">
              <AlertCircle className="w-5 h-5" />
            </div>
            <h3 className="text-sm font-semibold text-rose-200">Unable to load workflow history</h3>
            <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">{error}</p>
            <button
              onClick={() => fetchWorkflows(true)}
              className="mt-4 px-3.5 py-1.5 rounded text-xs font-semibold text-white bg-slate-800 hover:bg-slate-700 border border-slate-600 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {/* Loading skeleton */}
        {loading && workflows.length === 0 && !error && (
          <div className="p-6 space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-10 bg-slate-900/60 animate-pulse rounded border border-slate-800/50" />
            ))}
          </div>
        )}

        {/* Step 5: Empty state */}
        {!loading && !error && filteredWorkflows.length === 0 && (
          <div className="py-16 px-4 text-center">
            <div className="w-10 h-10 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center mx-auto mb-3 text-slate-500">
              <Layers className="w-5 h-5" />
            </div>
            <h3 className="text-sm font-semibold text-slate-200">No executions yet</h3>
            <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
              Run your first workflow to see results here.
            </p>
            <Link
              href="/new"
              className="mt-4 inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-500 rounded-md transition-colors"
            >
              <PlusCircle className="w-3.5 h-3.5" />
              <span>Launch First Run</span>
            </Link>
          </div>
        )}

        {/* Step 6: Recent Executions Table */}
        {!loading && filteredWorkflows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[#070b14] border-b border-slate-800 text-slate-400 font-mono text-[11px] uppercase tracking-wider">
                <tr>
                  <th className="py-2.5 px-4">Repository</th>
                  <th className="py-2.5 px-4">Status</th>
                  <th className="py-2.5 px-4">Steps</th>
                  <th className="py-2.5 px-4">Duration</th>
                  <th className="py-2.5 px-4">Retries</th>
                  <th className="py-2.5 px-4">Recoveries</th>
                  <th className="py-2.5 px-4">Time</th>
                  <th className="py-2.5 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono text-slate-300">
                {filteredWorkflows.map((wf) => {
                  const completedSteps = (wf.steps || []).filter((s) =>
                    ["SUCCESS", "VERIFIED_SUCCESS", "NOT_APPLICABLE", "PARTIALLY_SATISFIED"].includes(s.status)
                  ).length;
                  const repoClean = (wf.repository || "").replace("https://github.com/", "");
                  const durationStr = getWorkflowDuration(wf);
                  const retriesCount = wf.retries ?? 0;
                  const recoveriesCount = wf.recovery_history ? wf.recovery_history.length : 0;
                  const timeStr = wf.created_at ? formatTimestamp(wf.created_at) : "-";

                  return (
                    <tr
                      key={wf.workflow_id}
                      className="hover:bg-slate-900/50 transition-colors group"
                    >
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <GitBranch className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                          <div className="min-w-0">
                            <Link
                              href={`/workflow/${wf.workflow_id}`}
                              className="font-medium text-slate-100 hover:text-blue-400 transition-colors truncate block"
                            >
                              {repoClean || wf.workflow_id}
                            </Link>
                            <span className="text-[10px] text-slate-500 truncate block max-w-xs font-sans">
                              {wf.task}
                            </span>
                          </div>
                        </div>
                      </td>

                      <td className="py-3 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <StatusBadge status={wf.overall_status} size="xs" />
                          {wf.dry_run && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-950/60 text-purple-300 border border-purple-800/60">
                              DRY RUN
                            </span>
                          )}
                        </div>
                      </td>

                      <td className="py-3 px-4 whitespace-nowrap text-slate-400">
                        <span className="text-slate-200 font-semibold">{completedSteps}</span>
                        <span className="text-slate-600">/{wf.steps ? wf.steps.length : 0}</span>
                      </td>

                      <td className="py-3 px-4 whitespace-nowrap text-slate-300">
                        {durationStr}
                      </td>

                      <td className="py-3 px-4 whitespace-nowrap">
                        {retriesCount > 0 ? (
                          <span className="text-amber-400 font-semibold">{retriesCount}x</span>
                        ) : (
                          <span className="text-slate-600">0</span>
                        )}
                      </td>

                      <td className="py-3 px-4 whitespace-nowrap">
                        {recoveriesCount > 0 ? (
                          <span className="text-yellow-400 font-semibold flex items-center gap-1">
                            <RefreshCw className="w-3 h-3" />
                            {recoveriesCount}
                          </span>
                        ) : (
                          <span className="text-slate-600">0</span>
                        )}
                      </td>

                      <td className="py-3 px-4 whitespace-nowrap text-slate-500 text-[11px]">
                        {timeStr}
                      </td>

                      <td className="py-3 px-4 whitespace-nowrap text-right">
                        <Link
                          href={`/workflow/${wf.workflow_id}`}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-[11px] font-sans font-medium text-slate-300 bg-slate-800/60 hover:bg-slate-800 hover:text-white border border-slate-700 transition-colors"
                        >
                          <span>Inspect</span>
                          <ArrowUpRight className="w-3 h-3 text-slate-400" />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}