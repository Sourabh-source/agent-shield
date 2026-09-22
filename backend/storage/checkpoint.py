import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

from backend.config import settings
from backend.models.workflow import (
    ExecutionResult,
    RecoveryAttempt,
    StepDefinition,
    StepStatus,
    VerificationResult,
    WorkflowEvent,
    WorkflowState,
    WorkflowStatus,
)

logger = logging.getLogger("agentguard.storage.checkpoint")


class SQLiteCheckpointStorage:
    """
    SQLite persistence layer for workflows, steps, events, and recovery history.
    Ensures workflows survive server crashes and can be resumed from checkpoints.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or getattr(settings, "DATABASE_PATH", "./agentguard.db")
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    repository TEXT,
                    task TEXT,
                    current_step TEXT,
                    overall_status TEXT,
                    retries INTEGER,
                    max_retries INTEGER,
                    workspace_path TEXT,
                    verification_status TEXT,
                    final_result TEXT,
                    dry_run INTEGER,
                    demo_failure_mode TEXT,
                    metrics TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    owner_id TEXT
                )
            """)
            try:
                cursor.execute("ALTER TABLE workflows ADD COLUMN owner_id TEXT")
            except Exception:
                pass

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workflow_steps (
                    workflow_id TEXT,
                    id TEXT,
                    type TEXT,
                    name TEXT,
                    tool TEXT,
                    reason TEXT,
                    command TEXT,
                    description TEXT,
                    status TEXT,
                    retries INTEGER,
                    execution_result TEXT,
                    verification_result TEXT,
                    PRIMARY KEY (workflow_id, id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workflow_events (
                    event_id TEXT PRIMARY KEY,
                    workflow_id TEXT,
                    event_type TEXT,
                    step TEXT,
                    step_id TEXT,
                    execution_id TEXT,
                    status TEXT,
                    message TEXT,
                    timestamp TEXT,
                    evidence TEXT,
                    metadata TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recovery_history (
                    recovery_id TEXT PRIMARY KEY,
                    workflow_id TEXT,
                    step_name TEXT,
                    failure_type TEXT,
                    action TEXT,
                    status TEXT,
                    exit_code INTEGER,
                    duration_ms REAL,
                    timestamp TEXT
                )
            """)

            # Performance & query indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(overall_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_wid ON workflow_events(workflow_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_workflow_steps_wid ON workflow_steps(workflow_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recovery_history_wid ON recovery_history(workflow_id)")
            conn.commit()

    def save_workflow(self, state: WorkflowState):
        with self._lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO workflows (
                    workflow_id, repository, task, current_step, overall_status,
                    retries, max_retries, workspace_path, verification_status,
                    final_result, dry_run, demo_failure_mode, metrics, created_at, updated_at, owner_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state.workflow_id,
                state.repository,
                state.task,
                state.current_step,
                state.overall_status.value,
                state.retries,
                state.max_retries,
                state.workspace_path,
                state.verification_status,
                state.final_result,
                1 if state.dry_run else 0,
                None,
                json.dumps(state.metrics),
                state.created_at,
                state.updated_at,
                state.owner_id or "default-owner",
            ))

            # Save steps
            for step in state.steps:
                exec_json = step.execution_result.model_dump_json() if step.execution_result else None
                verif_json = step.verification_result.model_dump_json() if step.verification_result else None
                cursor.execute("""
                    INSERT OR REPLACE INTO workflow_steps (
                        workflow_id, id, type, name, tool, reason, command,
                        description, status, retries, execution_result, verification_result
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    state.workflow_id,
                    step.id,
                    step.type,
                    step.name,
                    step.tool,
                    step.reason,
                    step.command,
                    step.description,
                    step.status.value,
                    step.retries,
                    exec_json,
                    verif_json,
                ))

            # Save events
            for ev in state.events:
                cursor.execute("""
                    INSERT OR IGNORE INTO workflow_events (
                        event_id, workflow_id, event_type, step, step_id,
                        execution_id, status, message, timestamp, evidence, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ev.event_id,
                    ev.workflow_id,
                    ev.event_type,
                    ev.step,
                    ev.step_id,
                    ev.execution_id,
                    ev.status,
                    ev.message,
                    ev.timestamp,
                    json.dumps(ev.evidence) if ev.evidence else None,
                    json.dumps(ev.metadata) if ev.metadata else None,
                ))

            # Save recovery attempts
            for rec in state.recovery_history:
                cursor.execute("""
                    INSERT OR IGNORE INTO recovery_history (
                        recovery_id, workflow_id, step_name, failure_type,
                        action, status, exit_code, duration_ms, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rec.recovery_id,
                    state.workflow_id,
                    rec.step_name,
                    rec.failure_type,
                    rec.action,
                    rec.status,
                    rec.exit_code,
                    rec.duration_ms,
                    rec.timestamp,
                ))

            conn.commit()

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowState]:
        with self._lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,))
            row = cursor.fetchone()
            if not row:
                return None

            # Load steps
            cursor.execute("SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY rowid ASC", (workflow_id,))
            step_rows = cursor.fetchall()
            steps: List[StepDefinition] = []
            for sr in step_rows:
                exec_res = ExecutionResult.model_validate_json(sr["execution_result"]) if sr["execution_result"] else None
                verif_res = VerificationResult.model_validate_json(sr["verification_result"]) if sr["verification_result"] else None
                step = StepDefinition(
                    id=sr["id"],
                    type=sr["type"],
                    name=sr["name"],
                    tool=sr["tool"],
                    reason=sr["reason"],
                    command=sr["command"],
                    description=sr["description"],
                    status=StepStatus(sr["status"]),
                    retries=sr["retries"],
                    execution_result=exec_res,
                    verification_result=verif_res,
                )
                steps.append(step)

            # Load events
            cursor.execute("SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY timestamp ASC", (workflow_id,))
            event_rows = cursor.fetchall()
            events: List[WorkflowEvent] = []
            for er in event_rows:
                ev = WorkflowEvent(
                    event_id=er["event_id"],
                    workflow_id=er["workflow_id"],
                    event_type=er["event_type"],
                    step=er["step"],
                    step_id=er["step_id"],
                    execution_id=er["execution_id"],
                    status=er["status"],
                    message=er["message"],
                    timestamp=er["timestamp"],
                    evidence=json.loads(er["evidence"]) if er["evidence"] else None,
                    metadata=json.loads(er["metadata"]) if er["metadata"] else None,
                )
                events.append(ev)

            # Load recovery history
            cursor.execute("SELECT * FROM recovery_history WHERE workflow_id = ? ORDER BY timestamp ASC", (workflow_id,))
            rec_rows = cursor.fetchall()
            recovery_history: List[RecoveryAttempt] = []
            for rr in rec_rows:
                recovery_history.append(RecoveryAttempt(
                    recovery_id=rr["recovery_id"],
                    step_name=rr["step_name"],
                    failure_type=rr["failure_type"],
                    action=rr["action"],
                    status=rr["status"],
                    exit_code=rr["exit_code"],
                    duration_ms=rr["duration_ms"],
                    timestamp=rr["timestamp"],
                ))

            metrics = json.loads(row["metrics"]) if row["metrics"] else {}

            owner_id = "default-owner"
            try:
                if "owner_id" in row.keys() and row["owner_id"]:
                    owner_id = row["owner_id"]
            except Exception:
                pass

            return WorkflowState(
                workflow_id=row["workflow_id"],
                repository=row["repository"],
                task=row["task"],
                current_step=row["current_step"],
                overall_status=WorkflowStatus(row["overall_status"]),
                steps=steps,
                events=events,
                retries=row["retries"],
                max_retries=row["max_retries"],
                workspace_path=row["workspace_path"],
                verification_status=row["verification_status"],
                final_result=row["final_result"],
                dry_run=bool(row["dry_run"]),
                owner_id=owner_id,
                recovery_history=recovery_history,
                metrics=metrics,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_workflows(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
        owner_id: Optional[str] = None,
    ) -> List[WorkflowState]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                if owner_id and owner_id != "admin":
                    query = "SELECT workflow_id FROM workflows WHERE owner_id = ? ORDER BY created_at DESC"
                    params = [owner_id]
                else:
                    query = "SELECT workflow_id FROM workflows ORDER BY created_at DESC"
                    params = []

                if limit is not None and limit > 0:
                    query += f" LIMIT {int(limit)} OFFSET {int(offset)}"
                cursor.execute(query, params)
                rows = cursor.fetchall()
                w_ids = [r["workflow_id"] for r in rows]

            workflows: List[WorkflowState] = []
            for wid in w_ids:
                wf = self.get_workflow(wid)
                if wf is not None:
                    workflows.append(wf)
            return workflows

    def delete_workflow(self, workflow_id: str) -> bool:
        """Permanently deletes a workflow and all its associated steps, events, and recovery history."""
        with self._lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))
            deleted_count = cursor.rowcount
            cursor.execute("DELETE FROM workflow_steps WHERE workflow_id = ?", (workflow_id,))
            cursor.execute("DELETE FROM workflow_events WHERE workflow_id = ?", (workflow_id,))
            cursor.execute("DELETE FROM recovery_history WHERE workflow_id = ?", (workflow_id,))
            conn.commit()
            return deleted_count > 0

    def delete_by_owner(self, owner_id: str) -> int:
        """Permanently deletes all workflows for a given owner_id."""
        with self._lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT workflow_id FROM workflows WHERE owner_id = ?", (owner_id,))
            w_ids = [r["workflow_id"] for r in cursor.fetchall()]
            
            deleted_count = 0
            for wid in w_ids:
                if self.delete_workflow(wid):
                    deleted_count += 1
            return deleted_count
