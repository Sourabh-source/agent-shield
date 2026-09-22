"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { WorkflowCreateRequest } from "@/lib/api";
import { useRouter } from "next/navigation";
import { Play, RotateCw, GitBranch, Terminal, HelpCircle } from "lucide-react";

const PRESET_REPOS = [
  { label: "Hello-World (Python/Generic)", url: "https://github.com/octocat/Hello-World" },
  { label: "Example Node Service", url: "https://github.com/example/broken-dependency-repo" },
];

interface WorkflowFormProps {
  onStarted?: (workflowId: string) => void;
  onCancel?: () => void;
  compact?: boolean;
}

export default function WorkflowForm({ onStarted, onCancel, compact = false }: WorkflowFormProps) {
  const router = useRouter();
  const [repoUrl, setRepoUrl] = useState("https://github.com/octocat/Hello-World");
  const [task, setTask] = useState("Analyze repository, install dependencies, build, run tests, and verify service readiness.");
  const [dryRun, setDryRun] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const payload: WorkflowCreateRequest = {
      repo_url: repoUrl.trim(),
      task: task.trim(),
      dry_run: dryRun,
    };

    try {
      const resp = await api.startWorkflow(payload);
      if (onStarted) onStarted(resp.workflow_id);
      router.push(`/workflow/${resp.workflow_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start workflow");
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Repository Section */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
            <GitBranch className="w-3.5 h-3.5 text-slate-400" />
            <span>Target Repository</span>
          </label>
          <span className="text-[11px] text-slate-500 font-mono">Public Git URL</span>
        </div>
        <input
          type="url"
          required
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          placeholder="https://github.com/org/repo"
          className="w-full bg-[#070b14] border border-slate-700/80 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 font-mono focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors"
        />
        <div className="flex items-center gap-2 mt-1.5 text-[11px] text-slate-500">
          <span>Presets:</span>
          {PRESET_REPOS.map((p) => (
            <button
              key={p.url}
              type="button"
              onClick={() => setRepoUrl(p.url)}
              className="text-blue-400 hover:text-blue-300 underline underline-offset-2 transition-colors font-mono"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Task Description */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
            <Terminal className="w-3.5 h-3.5 text-slate-400" />
            <span>Verification Objective</span>
          </label>
          <span className="text-[11px] text-slate-500 font-mono">Workflow Specification</span>
        </div>
        <textarea
          required
          rows={compact ? 2 : 3}
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Describe the multi-step verification task for AgentGuard..."
          className="w-full bg-[#070b14] border border-slate-700/80 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors resize-none leading-relaxed"
        />
        <p className="text-[11px] text-slate-500 mt-1">
          AgentGuard AI Planner dynamically synthesizes steps and validates each via machine-checkable evidence.
        </p>
      </div>

      {/* Execution Options Section */}
      <div className="pt-2 border-t border-slate-800/80 space-y-4">
        <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500">
          Execution Parameters
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {/* Dry Run Toggle */}
          <div className="flex items-center justify-between p-2.5 rounded-md border border-slate-800 bg-[#070b14]">
            <div>
              <span className="text-xs font-medium text-slate-300 block">Dry-Run Mode</span>
              <span className="text-[11px] text-slate-500 block">Plan only, skip execution</span>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={dryRun}
              onClick={() => setDryRun(!dryRun)}
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                dryRun ? "bg-blue-600" : "bg-slate-700"
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-lg ring-0 transition duration-200 ease-in-out ${
                  dryRun ? "translate-x-4" : "translate-x-0"
                }`}
              />
            </button>
          </div>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="p-3 rounded-md bg-rose-950/40 border border-rose-800/80 text-rose-300 text-xs flex items-start gap-2">
          <HelpCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex items-center justify-end gap-3 pt-2">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white bg-slate-800/50 hover:bg-slate-800 rounded-md border border-slate-700 transition-colors"
          >
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={loading}
          className="w-full sm:w-auto px-5 py-2.5 text-xs font-semibold uppercase tracking-wider text-white bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed rounded-md border border-blue-500/50 transition-colors flex items-center justify-center gap-2 shadow-sm"
        >
          {loading ? (
            <>
              <RotateCw className="w-3.5 h-3.5 animate-spin" />
              <span>Dispatching Engine...</span>
            </>
          ) : (
            <>
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>Run AgentGuard Workflow</span>
            </>
          )}
        </button>
      </div>
    </form>
  );
}

