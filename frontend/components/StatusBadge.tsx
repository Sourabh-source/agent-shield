"use client";

import React from "react";
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  RefreshCw,
  Loader2,
  Ban,
  Clock,
  FastForward,
} from "lucide-react";

interface StatusBadgeProps {
  status: string;
  size?: "xs" | "sm" | "md" | "lg";
  pulse?: boolean;
  className?: string;
}

interface StatusConfig {
  label: string;
  dotColor: string;
  badgeClass: string;
  icon: React.ComponentType<{ className?: string }>;
}

const STATUS_MAP: Record<string, StatusConfig> = {
  COMPLETED: {
    label: "VERIFIED SUCCESS",
    dotColor: "bg-emerald-400",
    badgeClass: "bg-emerald-950/50 text-emerald-300 border-emerald-800/60",
    icon: CheckCircle2,
  },
  VERIFIED_SUCCESS: {
    label: "VERIFIED SUCCESS",
    dotColor: "bg-emerald-400",
    badgeClass: "bg-emerald-950/50 text-emerald-300 border-emerald-800/60",
    icon: CheckCircle2,
  },
  SUCCESS: {
    label: "PASS",
    dotColor: "bg-emerald-400",
    badgeClass: "bg-emerald-950/50 text-emerald-300 border-emerald-800/60",
    icon: CheckCircle2,
  },
  VERIFIED_FAILURE: {
    label: "VERIFIED FAILURE",
    dotColor: "bg-rose-400",
    badgeClass: "bg-rose-950/50 text-rose-300 border-rose-800/60",
    icon: XCircle,
  },
  FAILED: {
    label: "FAIL",
    dotColor: "bg-rose-400",
    badgeClass: "bg-rose-950/50 text-rose-300 border-rose-800/60",
    icon: XCircle,
  },
  BUDGET_EXCEEDED: {
    label: "BUDGET EXCEEDED",
    dotColor: "bg-rose-400",
    badgeClass: "bg-rose-950/50 text-rose-300 border-rose-800/60",
    icon: XCircle,
  },
  VERIFICATION_UNAVAILABLE: {
    label: "VERIFICATION UNAVAILABLE",
    dotColor: "bg-amber-400",
    badgeClass: "bg-amber-950/50 text-amber-300 border-amber-800/60",
    icon: AlertTriangle,
  },
  RECOVERING: {
    label: "SELF-HEALING",
    dotColor: "bg-yellow-400",
    badgeClass: "bg-yellow-950/50 text-yellow-300 border-yellow-700/60",
    icon: RefreshCw,
  },
  RUNNING: {
    label: "RUNNING",
    dotColor: "bg-sky-400",
    badgeClass: "bg-sky-950/50 text-sky-300 border-sky-800/60",
    icon: Loader2,
  },
  PLANNING: {
    label: "PLANNING",
    dotColor: "bg-sky-400",
    badgeClass: "bg-sky-950/50 text-sky-300 border-sky-800/60",
    icon: Loader2,
  },
  VERIFYING: {
    label: "VERIFYING",
    dotColor: "bg-blue-400",
    badgeClass: "bg-blue-950/50 text-blue-300 border-blue-800/60",
    icon: Loader2,
  },
  WAITING_FOR_VERIFICATION: {
    label: "EVIDENCE PENDING",
    dotColor: "bg-indigo-400",
    badgeClass: "bg-indigo-950/50 text-indigo-300 border-indigo-800/60",
    icon: Clock,
  },
  PENDING: {
    label: "QUEUED",
    dotColor: "bg-zinc-400",
    badgeClass: "bg-zinc-900 text-zinc-300 border-zinc-700/60",
    icon: Clock,
  },
  CANCEL_REQUESTED: {
    label: "CANCELLING",
    dotColor: "bg-zinc-400",
    badgeClass: "bg-zinc-900 text-zinc-400 border-zinc-700",
    icon: Ban,
  },
  CANCELLED: {
    label: "CANCELLED",
    dotColor: "bg-zinc-500",
    badgeClass: "bg-zinc-900 text-zinc-400 border-zinc-700",
    icon: Ban,
  },
  SKIPPED: {
    label: "SKIPPED",
    dotColor: "bg-zinc-500",
    badgeClass: "bg-zinc-900 text-zinc-400 border-zinc-700",
    icon: FastForward,
  },
};

export default function StatusBadge({
  status,
  size = "md",
  pulse = false,
  className = "",
}: StatusBadgeProps) {
  const normStatus = (status || "").toUpperCase();
  const config = STATUS_MAP[normStatus] || {
    label: status || "UNKNOWN",
    dotColor: "bg-zinc-400",
    badgeClass: "bg-zinc-900 text-zinc-300 border-zinc-700",
    icon: Clock,
  };

  const Icon = config.icon;
  const isSpinning = ["RUNNING", "PLANNING", "VERIFYING", "RECOVERING"].includes(normStatus);

  const sizeClasses = {
    xs: "text-[10px] px-1.5 py-0.5 gap-1 tracking-wider",
    sm: "text-xs px-2 py-0.5 gap-1.5 tracking-wide",
    md: "text-xs px-2.5 py-1 gap-1.5 font-medium tracking-wide",
    lg: "text-sm px-3.5 py-1.5 gap-2 font-semibold tracking-wide",
  };

  const iconSizes = {
    xs: "w-2.5 h-2.5",
    sm: "w-3 h-3",
    md: "w-3.5 h-3.5",
    lg: "w-4 h-4",
  };

  return (
    <span
      className={`inline-flex items-center rounded border font-mono uppercase ${config.badgeClass} ${sizeClasses[size]} ${className}`}
    >
      <Icon className={`${iconSizes[size]} ${isSpinning ? "animate-spin" : ""}`} />
      <span>{config.label}</span>
      {pulse && isSpinning && (
        <span className="relative flex h-1.5 w-1.5 ml-0.5">
          <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${config.dotColor}`} />
          <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${config.dotColor}`} />
        </span>
      )}
    </span>
  );
}

