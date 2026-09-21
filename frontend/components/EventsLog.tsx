"use client";

import React, { useState, useMemo } from "react";
import type { WorkflowEvent } from "@/lib/api";
import { formatTimestamp } from "@/lib/api";
import { Terminal, Search, ChevronDown, ChevronRight, Copy, Check } from "lucide-react";

interface EventsLogProps {
  events: WorkflowEvent[];
}

function getEventColor(type: string): string {
  const t = (type || "").toUpperCase();
  if (t.includes("PASSED") || t.includes("COMPLETED") || t.includes("VERIFIED_SUCCESS")) {
    return "text-emerald-400 bg-emerald-950/50 border-emerald-800/80";
  }
  if (t.includes("FAILED") || t.includes("FAILURE") || t.includes("CANCELLED")) {
    return "text-rose-400 bg-rose-950/50 border-rose-800/80";
  }
  if (t.includes("RECOVERY") || t.includes("RETRY")) {
    return "text-yellow-400 bg-yellow-950/50 border-yellow-800/80";
  }
  if (t.includes("VERIFY") || t.includes("COMMAND")) {
    return "text-sky-400 bg-sky-950/50 border-sky-800/80";
  }
  return "text-slate-400 bg-slate-900 border-slate-700/80";
}

export default function EventsLog({ events }: EventsLogProps) {
  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const filteredEvents = useMemo(() => {
    if (!events) return [];
    return [...events].reverse().filter((ev) => {
      if (!query) return true;
      const q = query.toLowerCase();
      return (
        ev.event_type.toLowerCase().includes(q) ||
        (ev.step && ev.step.toLowerCase().includes(q)) ||
        (ev.message && ev.message.toLowerCase().includes(q))
      );
    });
  }, [events, query]);

  const copyEvidence = (id: string, evidence: Record<string, unknown>) => {
    navigator.clipboard.writeText(JSON.stringify(evidence, null, 2));
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (!events || events.length === 0) {
    return (
      <div className="py-12 text-center text-xs text-slate-500 font-mono">
        <Terminal className="w-6 h-6 mx-auto mb-2 text-slate-600" />
        <p>No audit stream events recorded yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3 font-mono text-xs">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3 pb-2 border-b border-slate-800">
        <div className="flex items-center gap-2 text-slate-400 text-[11px] uppercase tracking-wider font-semibold">
          <Terminal className="w-3.5 h-3.5 text-blue-400" />
          <span>Audit Activity Stream</span>
          <span className="text-slate-500">({filteredEvents.length} events)</span>
        </div>

        <div className="relative w-48 sm:w-64">
          <Search className="w-3 h-3 text-slate-500 absolute left-2.5 top-2.5" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search events..."
            className="w-full pl-7 pr-2.5 py-1 bg-[#070b14] border border-slate-800 rounded text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* Terminal Stream List */}
      <div className="rounded-md border border-slate-800 bg-[#050811] divide-y divide-slate-850 max-h-96 overflow-y-auto">
        {filteredEvents.map((ev) => {
          const isExpanded = expandedId === ev.event_id;
          const hasEvidence = ev.evidence && Object.keys(ev.evidence).length > 0;

          return (
            <div key={ev.event_id} className="p-2.5 hover:bg-slate-900/40 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-2.5 min-w-0 flex-1">
                  {/* Timestamp */}
                  <span className="text-slate-500 text-[11px] shrink-0 select-none">
                    {formatTimestamp(ev.timestamp)}
                  </span>

                  {/* Event Type Badge */}
                  <span
                    className={`px-1.5 py-0.5 rounded border text-[10px] uppercase shrink-0 font-semibold ${getEventColor(
                      ev.event_type
                    )}`}
                  >
                    {ev.event_type}
                  </span>

                  {/* Step Name */}
                  {ev.step && (
                    <span className="text-blue-400/90 truncate shrink-0 max-w-[150px] font-sans text-xs">
                      [{ev.step}]
                    </span>
                  )}

                  {/* Message */}
                  <span className="text-slate-300 font-sans text-xs break-all">
                    {ev.message}
                  </span>
                </div>

                {/* Evidence Expand Toggle */}
                {hasEvidence && (
                  <button
                    onClick={() => setExpandedId(isExpanded ? null : ev.event_id)}
                    className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-300 shrink-0 select-none py-0.5 px-1.5 rounded hover:bg-slate-800"
                  >
                    <span>evidence</span>
                    {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                  </button>
                )}
              </div>

              {/* Expandable JSON Evidence */}
              {isExpanded && hasEvidence && (
                <div className="mt-2 pt-2 border-t border-slate-800/80">
                  <div className="flex items-center justify-between pb-1 text-[10px] text-slate-500">
                    <span>Machine Observable Evidence Payload</span>
                    <button
                      onClick={() => copyEvidence(ev.event_id, ev.evidence!)}
                      className="flex items-center gap-1 text-slate-400 hover:text-slate-200"
                    >
                      {copiedId === ev.event_id ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-400" />
                          <span>Copied</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          <span>Copy JSON</span>
                        </>
                      )}
                    </button>
                  </div>
                  <pre className="p-2 rounded bg-[#03050a] text-slate-300 text-[11px] overflow-x-auto max-h-36 whitespace-pre-wrap border border-slate-900">
                    {JSON.stringify(ev.evidence, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}