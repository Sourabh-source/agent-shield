"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { api, formatTimestamp, getWorkflowDuration } from "@/lib/api";
import type { WorkflowState } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import {
  Terminal,
  PlusCircle,
  RefreshCw,
  GitBranch,
  ArrowUpRight,
  Search,
  AlertCircle,
} from "lucide-react";

type FilterType = "ALL" | "SUCCESS" | "FAILURE" | "RUNNING";

export default function WorkflowsHistoryPage() {
  const [workflows, setWorkflows] = useState<WorkflowState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FilterType>("ALL");

  const loadData = () => {
    setLoading(true);
    setError(null);
    api.listWorkflows()
      .then((data) => {
        setWorkflows(data || []);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load workflow history");
      })
      .finally(() => {
        setLoading(false);
      });
  };

  useEffect(() => {
    let mounted = true;
    api.listWorkflows()
      .then((data) => {
        if (mounted) setWorkflows(data || []);
      })
      .catch((err) => {
        if (mounted) setError(err instanceof Error ? err.message : "Failed to load workflow history");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  const filtered = useMemo(() => {
    return [...workflows].reverse().filter((w) => {
      const matchSearch =
        w.repository.toLowerCase().includes(search.toLowerCase()) ||
        w.workflow_id.toLowerCase().includes(search.toLowerCase()) ||
        w.task.toLowerCase().includes(search.toLowerCase());

      if (!matchSearch) return false;

      if (filter === "SUCCESS") return w.overall_status === "COMPLETED";
      if (filter === "FAILURE")
        return ["VERIFIED_FAILURE", "FAILED", "BUDGET_EXCEEDED"].includes(w.overall_status);
      if (filter === "RUNNING")
        return ["RUNNING", "PLANNING", "VERIFYING", "RECOVERING"].includes(w.overall_status);

      return true;
    });
  }, [workflows, search, filter]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <Terminal className="w-5 h-5 text-blue-400" />
            <span>Workflow History</span>
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Audit log of all executed, verified, and self-healed repository verification runs.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={loadData}
            disabled={loading}
            className="p-2 rounded-md border border-slate-700/80 bg-slate-900/60 hover:bg-slate-800 text-slate-300 transition-colors"
            title="Reload Workflows"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-blue-400" : ""}`} />
          </button>
          <Link
            href="/new"
            className="px-3.5 py-1.5 text-xs font-semibold uppercase tracking-wider text-white bg-blue-600 hover:bg-blue-500 rounded-md border border-blue-500/60 transition-colors flex items-center gap-1.5 shadow-sm"
          >
            <PlusCircle className="w-3.5 h-3.5" />
            <span>New Run</span>
          </Link>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-[#0e1422] p-3 rounded-lg border border-slate-800">
        <div className="relative w-full sm:w-80">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2.5" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by repo, task, or ID..."
            className="w-full pl-8 pr-3 py-1.5 bg-[#070b14] border border-slate-700/80 rounded text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 font-mono"
          />
        </div>

        {/* Filter Tabs */}
        <div className="flex items-center gap-1 w-full sm:w-auto overflow-x-auto text-[11px] font-mono">
          {[
            { id: "ALL", label: `All (${workflows.length})` },
            {
              id: "SUCCESS",
              label: `Success (${workflows.filter((w) => w.overall_status === "COMPLETED").length})`,
            },
            {
              id: "FAILURE",
              label: `Failure (${
                workflows.filter((w) =>
                  ["VERIFIED_FAILURE", "FAILED", "BUDGET_EXCEEDED"].includes(w.overall_status)
                ).length
              })`,
            },
            {
              id: "RUNNING",
              label: `Running (${
                workflows.filter((w) =>
                  ["RUNNING", "PLANNING", "VERIFYING", "RECOVERING"].includes(w.overall_status)
                ).length
              })`,
            },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setFilter(tab.id as FilterType)}
              className={`px-3 py-1.5 rounded-md transition-colors whitespace-nowrap ${
                filter === tab.id
                  ? "bg-slate-800 text-white font-semibold border border-slate-700"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-950/40 border border-rose-900/60 rounded-lg text-xs text-rose-300 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={loadData}
            className="px-2.5 py-1 rounded text-xs font-semibold bg-rose-900/60 hover:bg-rose-800 text-rose-200 border border-rose-700 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* History Table */}
      <div className="border border-slate-800 rounded-lg bg-[#0e1422] overflow-hidden">
        {loading && workflows.length === 0 ? (
          <div className="p-6 space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-12 bg-slate-900/60 animate-pulse rounded border border-slate-800/50" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="py-16 text-center text-xs text-slate-500">
            <p className="text-slate-300 font-semibold mb-1">
              {workflows.length === 0 ? "No executions yet" : "No matching workflow runs found."}
            </p>
            <p className="text-slate-400">
              {workflows.length === 0
                ? "Run your first workflow to see results here."
                : "Try adjusting your search query or filter tab."}
            </p>
            {workflows.length > 0 && (
              <button
                onClick={() => {
                  setSearch("");
                  setFilter("ALL");
                }}
                className="mt-3 text-blue-400 hover:underline inline-block"
              >
                Reset filters
              </button>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-[#070b14] border-b border-slate-800 text-slate-400 text-[11px] uppercase tracking-wider">
                <tr>
                  <th className="py-2.5 px-4">Workflow ID</th>
                  <th className="py-2.5 px-4">Repository</th>
                  <th className="py-2.5 px-4">Status</th>
                  <th className="py-2.5 px-4">Steps</th>
                  <th className="py-2.5 px-4">Duration</th>
                  <th className="py-2.5 px-4">Retries</th>
                  <th className="py-2.5 px-4">Recoveries</th>
                  <th className="py-2.5 px-4">Timestamp</th>
                  <th className="py-2.5 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {filtered.map((wf) => {
                  const completed = (wf.steps || []).filter((s) =>
                    ["SUCCESS", "VERIFIED_SUCCESS", "NOT_APPLICABLE", "PARTIALLY_SATISFIED"].includes(s.status)
                  ).length;
                  const durationStr = getWorkflowDuration(wf);

                  return (
                    <tr key={wf.workflow_id} className="hover:bg-slate-900/50 transition-colors">
                      <td className="py-3 px-4 text-slate-400 font-mono">
                        {wf.workflow_id.slice(0, 8)}
                      </td>
                      <td className="py-3 px-4">
                        <Link
                          href={`/workflow/${wf.workflow_id}`}
                          className="font-medium text-slate-100 hover:text-blue-400 transition-colors flex items-center gap-1.5"
                        >
                          <GitBranch className="w-3 h-3 text-slate-500 shrink-0" />
                          <span className="truncate max-w-xs font-sans">
                            {(wf.repository || "").replace("https://github.com/", "") || wf.workflow_id}
                          </span>
                        </Link>
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap">
                        <StatusBadge status={wf.overall_status} size="xs" />
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap text-slate-400">
                        <span className="text-slate-200 font-semibold">{completed}</span>
                        <span className="text-slate-600">/{wf.steps ? wf.steps.length : 0}</span>
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap text-slate-300">
                        {durationStr}
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap text-slate-400">
                        {wf.retries > 0 ? (
                          <span className="text-amber-400 font-semibold">{wf.retries}x</span>
                        ) : (
                          <span className="text-slate-600">0</span>
                        )}
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap">
                        {wf.recovery_history && wf.recovery_history.length > 0 ? (
                          <span className="text-yellow-400 font-semibold">
                            {wf.recovery_history.length} healed
                          </span>
                        ) : (
                          <span className="text-slate-600">0</span>
                        )}
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap text-slate-500 text-[11px]">
                        {wf.created_at ? formatTimestamp(wf.created_at) : "-"}
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