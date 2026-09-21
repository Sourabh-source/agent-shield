/**
 * AgentGuard API Client
 * Central service for all backend communication.
 * Backend URL is configurable via NEXT_PUBLIC_API_URL environment variable.
 * SECURITY: No secrets exposed to the browser. Only the API base URL is public.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export interface WorkflowCreateRequest {
  repo_url: string;
  task: string;
  dry_run: boolean;
  demo_failure_mode?: string | null;
}

export interface WorkflowCreateResponse {
  workflow_id: string;
  status: string;
}

export interface StepDefinition {
  id: string;
  type: string;
  name: string;
  tool?: string;
  command?: string;
  description?: string;
  status: string;
  retries: number;
  execution_result?: ExecutionResult | null;
  verification_result?: VerificationResult | null;
}

export interface ExecutionResult {
  workflow_id: string;
  step: string;
  action?: string;
  step_id?: string;
  execution_id: string;
  command: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_ms: number;
  timestamp: string;
  workspace?: string;
  metadata?: Record<string, unknown>;
}

export interface VerificationResult {
  verified: boolean;
  reason?: string;
  recovery_required: boolean;
  recovery_action?: string;
  retry_allowed: boolean;
  recovery_id?: string;
  failure_type?: string;
  metadata?: Record<string, unknown>;
}

export interface WorkflowEvent {
  workflow_id: string;
  event_type: string;
  event_id: string;
  step?: string;
  step_id?: string;
  execution_id?: string;
  status: string;
  message: string;
  timestamp: string;
  evidence?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface RecoveryAttempt {
  recovery_id: string;
  step_name: string;
  failure_type: string;
  action: string;
  status: string;
  exit_code: number;
  duration_ms: number;
  timestamp: string;
}

export interface WorkflowState {
  workflow_id: string;
  repository: string;
  task: string;
  current_step?: string;
  overall_status: string;
  steps: StepDefinition[];
  events: WorkflowEvent[];
  retries: number;
  max_retries: number;
  workspace_path?: string;
  verification_status?: string;
  final_result?: string;
  dry_run: boolean;
  demo_failure_mode?: string;
  recovery_history: RecoveryAttempt[];
  metrics: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FinalReportData {
  workflow_id: string;
  repository: string;
  task: string;
  final_status: string;
  steps_completed: number;
  total_steps: number;
  recoveries: number;
  retries: number;
  duration_seconds: number;
  verification_summary: Record<string, string>;
  recovery_history: Record<string, unknown>[];
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    signal: options?.signal || AbortSignal.timeout(10000),
    ...options,
  });
  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`API ${res.status}: ${errorText}`);
  }
  return res.json();
}

export const api = {
  startWorkflow: (payload: WorkflowCreateRequest): Promise<WorkflowCreateResponse> =>
    request("/workflow/start", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getStatus: (workflowId: string): Promise<WorkflowState> =>
    request(`/workflow/${workflowId}/status`),

  getEvents: (workflowId: string): Promise<WorkflowEvent[]> =>
    request(`/workflow/${workflowId}/events`),

  getReport: (workflowId: string): Promise<FinalReportData> =>
    request(`/workflow/${workflowId}/report`),

  cancelWorkflow: (workflowId: string): Promise<WorkflowState> =>
    request(`/workflow/${workflowId}/cancel`, { method: "POST" }),

  resumeWorkflow: (workflowId: string): Promise<WorkflowState> =>
    request(`/workflow/${workflowId}/resume`, { method: "POST" }),

  listWorkflows: (): Promise<WorkflowState[]> => request("/workflows"),

  health: (): Promise<Record<string, unknown>> => request("/health"),
};

export const TERMINAL_STATUSES = new Set([
  "COMPLETED",
  "VERIFIED_FAILURE",
  "VERIFICATION_UNAVAILABLE",
  "CANCELLED",
  "BUDGET_EXCEEDED",
  "FAILED",
]);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function statusColor(status: string): string {
  switch (status) {
    case "COMPLETED":
    case "VERIFIED_SUCCESS":
    case "SUCCESS":
      return "text-green-400";
    case "RUNNING":
    case "PLANNING":
    case "VERIFYING":
      return "text-blue-400";
    case "RECOVERING":
    case "WAITING_FOR_VERIFICATION":
    case "PENDING":
      return "text-yellow-400";
    case "VERIFIED_FAILURE":
    case "FAILED":
    case "BUDGET_EXCEEDED":
    case "VERIFICATION_UNAVAILABLE":
      return "text-red-400";
    case "CANCELLED":
    case "CANCEL_REQUESTED":
      return "text-gray-400";
    default:
      return "text-gray-300";
  }
}

export function statusBg(status: string): string {
  switch (status) {
    case "COMPLETED":
    case "VERIFIED_SUCCESS":
    case "SUCCESS":
      return "bg-green-900/40 border-green-700";
    case "RUNNING":
    case "PLANNING":
    case "VERIFYING":
      return "bg-blue-900/40 border-blue-700";
    case "RECOVERING":
    case "WAITING_FOR_VERIFICATION":
    case "PENDING":
      return "bg-yellow-900/40 border-yellow-700";
    case "VERIFIED_FAILURE":
    case "FAILED":
    case "BUDGET_EXCEEDED":
    case "VERIFICATION_UNAVAILABLE":
      return "bg-red-900/40 border-red-700";
    case "CANCELLED":
    case "CANCEL_REQUESTED":
      return "bg-gray-800/40 border-gray-600";
    default:
      return "bg-gray-800/40 border-gray-700";
  }
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function formatTimestamp(iso: string): string {
  // Use a deterministic, locale-independent format (HH:MM:SS from the ISO string)
  // to avoid SSR/client hydration mismatches caused by toLocaleTimeString()
  // differing between the Node.js server locale and the browser locale.
  try {
    // ISO 8601: "2026-09-21T10:29:11Z" or "2026-09-21T10:29:11.123Z"
    // Slice the time portion directly — always deterministic, no locale involved.
    const t = new Date(iso).toISOString(); // normalize to UTC ISO
    return t.slice(11, 19); // "HH:MM:SS"
  } catch {
    return iso;
  }
}

export function getWorkflowDuration(w: WorkflowState): string {
  if (
    w.metrics &&
    typeof w.metrics.total_duration_seconds === "number" &&
    w.metrics.total_duration_seconds > 0
  ) {
    return `${w.metrics.total_duration_seconds.toFixed(1)}s`;
  }
  const totalStepMs = (w.steps || []).reduce(
    (acc, s) => acc + (s.execution_result?.duration_ms || 0),
    0
  );
  if (totalStepMs > 0) {
    return `${(totalStepMs / 1000).toFixed(1)}s`;
  }
  if (w.created_at && w.updated_at && w.overall_status !== "PENDING") {
    const diffSec =
      (new Date(w.updated_at).getTime() - new Date(w.created_at).getTime()) / 1000;
    if (diffSec > 0 && diffSec < 86400) {
      return `${diffSec.toFixed(1)}s`;
    }
  }
  return "-";
}
