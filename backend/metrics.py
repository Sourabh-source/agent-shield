from prometheus_client import Counter, Histogram, Gauge, Info

# Workflow metrics
workflow_total = Counter(
    'agentguard_workflow_total',
    'Total workflows processed',
    ['status']  # completed, failed, cancelled
)

workflow_duration_seconds = Histogram(
    'agentguard_workflow_duration_seconds',
    'Workflow execution duration',
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]
)

step_verification_result = Counter(
    'agentguard_step_verification_total',
    'Step verification outcomes',
    ['step_type', 'verdict']  # verdict: pass, fail, unverifiable
)

recovery_attempts_total = Counter(
    'agentguard_recovery_attempts_total',
    'Recovery attempts by failure type',
    ['failure_type', 'outcome']  # outcome: success, failed
)

active_workflows = Gauge(
    'agentguard_active_workflows',
    'Currently executing workflows'
)

security_violations = Counter(
    'agentguard_security_violations_total',
    'Security violation detections',
    ['violation_type']  # path_traversal, command_injection, ssrf, auth_failure
)

build_info = Info('agentguard_build', 'Build information')
