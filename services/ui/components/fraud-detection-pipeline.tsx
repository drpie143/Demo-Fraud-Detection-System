'use client';

import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  FileText,
  Gauge,
  Radio,
  Shield,
  ShieldAlert,
  XCircle,
  Zap,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface AgentProgress {
  agent?: string;
  status?: string;
  step?: number;
  tasks?: number;
  evidence_count?: number;
  result?: string;
}

interface Phase1Data {
  risk_score?: number;
  risk_level?: string;
  triggered_rules?: Array<{ severity?: string; rule?: string; detail?: string }>;
  sender_flags?: {
    account_id?: string;
    is_whitelisted?: boolean;
    is_blacklisted?: boolean;
    risk_score?: number;
  };
  receiver_flags?: {
    account_id?: string;
    is_whitelisted?: boolean;
    is_blacklisted?: boolean;
    risk_score?: number;
  };
}

interface InvestigationData {
  steps?: number;
  evidence_count?: number;
  confidence?: number;
}

interface DetailData {
  decision?: string;
  confidence?: number;
  reasoning?: string;
  actions?: string[] | string;
  recommended_actions?: string[] | string;
}

interface ApiResult {
  transaction_id?: string;
  decision?: string;
  message?: string;
  phase1?: Phase1Data;
  risk_level?: string;
  investigation?: InvestigationData;
  report?: string | { detailed_analysis?: string; summary?: string };
  detail?: DetailData;
}

interface PipelineEvent {
  event: string;
  data?: Record<string, unknown>;
  _seq?: number;
}

interface TransactionData {
  amount: string;
  recipientId: string;
  description?: string;
  scenarioName?: string;
  deviceId?: string;
  ipAddress?: string;
  authMethod?: string;
  currency?: string;
  expectedIsFraud?: boolean | null;
  selectedAccount?: { id: string; name: string; balance: number };
}

interface FraudDetectionPipelineProps {
  isProcessing: boolean;
  transactionData?: TransactionData | null;
  apiResult?: ApiResult | null;
  pipelineEvents?: PipelineEvent[];
  pipelineEvent?: PipelineEvent | null;
}

type PhaseStatus = 'idle' | 'processing' | 'completed';

const decisionClasses: Record<string, string> = {
  allow: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  block: 'border-red-500/40 bg-red-500/10 text-red-200',
  escalate: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
};

const ruleSeverityClasses: Record<string, string> = {
  critical: 'border-red-500/50 bg-red-500/15 text-red-100',
  high: 'border-orange-500/50 bg-orange-500/15 text-orange-100',
  medium: 'border-amber-500/50 bg-amber-500/15 text-amber-100',
  low: 'border-cyan-500/50 bg-cyan-500/15 text-cyan-100',
};

const formatAmount = (value: string, currency = 'VND') => {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return `0 ${currency}`;
  return `${amount.toLocaleString('en-US', { maximumFractionDigits: 2 })} ${currency}`;
};

const formatScore = (value?: number) => {
  if (value === undefined || value === null) return '0';
  return Math.round(value * 100).toString();
};

const hasEvent = (events: PipelineEvent[], name: string) => events.some((event) => event.event === name);

const phaseStatus = (events: PipelineEvent[], start: string, done: string, hasData: boolean): PhaseStatus => {
  if (hasData || hasEvent(events, done) || hasEvent(events, 'complete')) return 'completed';
  if (hasEvent(events, start)) return 'processing';
  return 'idle';
};

const asAgentProgress = (data?: Record<string, unknown>): AgentProgress => ({
  agent: typeof data?.agent === 'string' ? data.agent : undefined,
  status: typeof data?.status === 'string' ? data.status : undefined,
  step: typeof data?.step === 'number' ? data.step : undefined,
  tasks: typeof data?.tasks === 'number' ? data.tasks : undefined,
  evidence_count: typeof data?.evidence_count === 'number' ? data.evidence_count : undefined,
  result: typeof data?.result === 'string' ? data.result : undefined,
});

const actionList = (detail?: DetailData) => {
  const actions = detail?.actions ?? detail?.recommended_actions;
  if (Array.isArray(actions)) return actions.map(String);
  if (typeof actions === 'string') return actions.split('\n').map((item) => item.trim()).filter(Boolean);
  return [];
};

const reportText = (report?: ApiResult['report']) => {
  if (!report) return '';
  if (typeof report === 'string') return report;
  return report.detailed_analysis || report.summary || '';
};

function StatusIcon({ status, decision }: { status: PhaseStatus; decision?: string }) {
  if (status === 'completed') {
    if (decision === 'block') return <XCircle className="h-4 w-4" />;
    if (decision === 'escalate') return <AlertTriangle className="h-4 w-4" />;
    return <CheckCircle2 className="h-4 w-4" />;
  }
  if (status === 'processing') return <Activity className="h-4 w-4 animate-pulse" />;
  return <Circle className="h-4 w-4" />;
}

function PhaseCard({
  index,
  title,
  subtitle,
  status,
  icon,
  expanded,
  onToggle,
  decision,
  children,
}: {
  index: number;
  title: string;
  subtitle: string;
  status: PhaseStatus;
  icon: React.ReactNode;
  expanded: boolean;
  onToggle: () => void;
  decision?: string;
  children: ReactNode;
}) {
  const active = status !== 'idle';
  return (
    <section
      className={cn(
        'rounded-md border bg-slate-900/70 transition',
        active ? 'border-slate-700 shadow-lg shadow-black/10' : 'border-slate-800 opacity-70',
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-4 px-4 py-4 text-left"
      >
        <div className="flex min-w-0 items-center gap-3">
          <div
            className={cn(
              'flex h-10 w-10 flex-none items-center justify-center rounded-md border',
              status === 'processing' && 'border-cyan-400/40 bg-cyan-400/10 text-cyan-200',
              status === 'idle' && 'border-slate-700 bg-slate-950 text-slate-500',
              status === 'completed' &&
                (decisionClasses[decision || ''] || 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'),
            )}
          >
            {icon}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Phase {index}</span>
              <span className="inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-0.5 text-[11px] font-medium text-slate-300">
                <StatusIcon status={status} decision={decision} />
                {status}
              </span>
            </div>
            <h3 className="mt-1 truncate text-base font-semibold text-white">{title}</h3>
            <p className="text-xs text-slate-400">{subtitle}</p>
          </div>
        </div>
        <ChevronDown className={cn('h-4 w-4 flex-none text-slate-400 transition', expanded && 'rotate-180')} />
      </button>

      {expanded && <div className="border-t border-slate-800 px-4 py-4">{children}</div>}
    </section>
  );
}

export function FraudDetectionPipeline({
  isProcessing,
  transactionData,
  apiResult,
  pipelineEvents = [],
}: FraudDetectionPipelineProps) {
  const [expandedPhases, setExpandedPhases] = useState<Record<number, boolean>>({ 1: true, 2: true, 3: true });

  const phase1Data = apiResult?.phase1;
  const investigationData = apiResult?.investigation;
  const detailData = apiResult?.detail;
  const decision = (apiResult?.decision || detailData?.decision || '').toLowerCase();
  const riskLevel = (apiResult?.risk_level || phase1Data?.risk_level || 'idle').toLowerCase();
  const riskScore = phase1Data?.risk_score ?? 0;
  const scorePct = Number(formatScore(riskScore));
  const report = reportText(apiResult?.report);
  const actions = actionList(detailData);

  const phase2Agents = useMemo(
    () => pipelineEvents.filter((event) => event.event === 'phase2_progress').map((event) => asAgentProgress(event.data)),
    [pipelineEvents],
  );

  const phase3Agents = useMemo(
    () => pipelineEvents.filter((event) => event.event === 'phase3_progress').map((event) => asAgentProgress(event.data)),
    [pipelineEvents],
  );

  const phase1 = phaseStatus(pipelineEvents, 'phase1_start', 'phase1_done', Boolean(phase1Data));
  const phase2 = phaseStatus(pipelineEvents, 'phase2_start', 'phase2_done', Boolean(investigationData));
  const phase3 = phaseStatus(pipelineEvents, 'phase3_start', 'phase3_done', Boolean(detailData || apiResult?.decision));

  const togglePhase = (index: number) => {
    setExpandedPhases((prev) => ({ ...prev, [index]: !prev[index] }));
  };

  return (
    <div className="flex h-full min-h-[680px] flex-col gap-4 text-slate-100">
      <header className="flex flex-col gap-3 rounded-md border border-slate-800 bg-slate-950 px-4 py-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">
            <Radio className="h-4 w-4" />
            Live investigation stream
          </div>
          <h2 className="mt-1 text-xl font-semibold tracking-tight">Operations console</h2>
          <p className="mt-1 text-xs text-slate-400">Rule engine, fallback agents, evidence report, and final detective decision.</p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2">
            <div className="text-slate-500">Events</div>
            <div className="mt-1 text-lg font-semibold text-white">{pipelineEvents.length}</div>
          </div>
          <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2">
            <div className="text-slate-500">Risk</div>
            <div className="mt-1 text-lg font-semibold uppercase text-white">{riskLevel}</div>
          </div>
          <div className={cn('rounded-md border px-3 py-2', decisionClasses[decision] || 'border-slate-800 bg-slate-900')}>
            <div className="text-slate-500">Decision</div>
            <div className="mt-1 text-lg font-semibold uppercase">{decision || (isProcessing ? 'running' : 'idle')}</div>
          </div>
        </div>
      </header>

      {transactionData ? (
        <section className="grid gap-3 rounded-md border border-slate-800 bg-slate-950/80 p-4 md:grid-cols-[1.2fr_0.8fr]">
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-3">
              <div className="text-xs font-medium text-slate-500">Sender</div>
              <div className="mt-1 break-all font-mono text-sm font-semibold text-white">{transactionData.selectedAccount?.id || 'unknown'}</div>
              <div className="mt-1 truncate text-xs text-slate-500">{transactionData.selectedAccount?.name}</div>
            </div>
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-3">
              <div className="text-xs font-medium text-slate-500">Receiver</div>
              <div className="mt-1 break-all font-mono text-sm font-semibold text-white">{transactionData.recipientId}</div>
              <div className="mt-1 truncate text-xs text-slate-500">{transactionData.scenarioName || 'manual transaction'}</div>
            </div>
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-3">
              <div className="text-xs font-medium text-slate-500">Amount</div>
              <div className="mt-1 text-lg font-semibold text-white">{formatAmount(transactionData.amount, transactionData.currency || 'VND')}</div>
              <div className="mt-1 text-xs text-slate-500">{transactionData.authMethod || 'SMART_OTP'}</div>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 md:grid-cols-1">
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-xs">
              <span className="text-slate-500">Device </span>
              <span className="font-mono text-slate-200">{transactionData.deviceId || 'dataset/default'}</span>
            </div>
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-xs">
              <span className="text-slate-500">IP </span>
              <span className="font-mono text-slate-200">{transactionData.ipAddress || 'dataset/default'}</span>
            </div>
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-xs">
              <span className="text-slate-500">Expected </span>
              <span className={transactionData.expectedIsFraud ? 'font-semibold text-red-300' : 'font-semibold text-emerald-300'}>
                {transactionData.expectedIsFraud === null || transactionData.expectedIsFraud === undefined
                  ? 'unknown'
                  : transactionData.expectedIsFraud
                    ? 'fraud'
                    : 'legit'}
              </span>
            </div>
          </div>
        </section>
      ) : (
        <section className="flex min-h-36 items-center justify-center rounded-md border border-dashed border-slate-800 bg-slate-950/60 text-center">
          <div>
            <Shield className="mx-auto h-10 w-10 text-slate-700" />
            <p className="mt-3 text-sm font-medium text-slate-400">Log in and submit a dataset transaction to start the stream.</p>
          </div>
        </section>
      )}

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <div className="space-y-4">
          <PhaseCard
            index={1}
            title="Rule screening"
            subtitle="Redis flags, dataset thresholds, velocity, blacklist, and balance drain checks."
            status={phase1}
            icon={<Zap className="h-5 w-5" />}
            expanded={expandedPhases[1] ?? true}
            onToggle={() => togglePhase(1)}
            decision={riskLevel === 'red' ? 'block' : riskLevel === 'yellow' ? 'escalate' : riskLevel === 'green' ? 'allow' : undefined}
          >
            <div className="space-y-4">
              <div className="grid gap-3 md:grid-cols-[160px_minmax(0,1fr)]">
                <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-slate-400">
                    <Gauge className="h-4 w-4 text-cyan-300" />
                    Risk score
                  </div>
                  <div className="mt-2 text-3xl font-semibold text-white">{formatScore(riskScore)}</div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className={cn(
                        'h-full rounded-full',
                        scorePct >= 80 ? 'bg-red-500' : scorePct >= 45 ? 'bg-amber-400' : 'bg-emerald-400',
                      )}
                      style={{ width: `${Math.min(100, Math.max(0, scorePct))}%` }}
                    />
                  </div>
                </div>

                <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Triggered rules</div>
                  {phase1Data?.triggered_rules?.length ? (
                    <div className="flex flex-wrap gap-2">
                      {phase1Data.triggered_rules.map((rule, index) => {
                        const severity = (rule.severity || 'low').toLowerCase();
                        return (
                          <div
                            key={`${rule.rule || 'rule'}-${index}`}
                            className={cn('max-w-full rounded-md border px-2.5 py-2 text-xs', ruleSeverityClasses[severity] || ruleSeverityClasses.low)}
                          >
                            <div className="font-semibold uppercase">{rule.rule || 'RULE'}</div>
                            <div className="mt-1 text-[11px] opacity-90">{rule.detail || severity}</div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
                      No blocking rule triggered yet.
                    </div>
                  )}
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                {[phase1Data?.sender_flags, phase1Data?.receiver_flags].map((flags, index) => (
                  <div key={index} className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3 text-xs">
                    <div className="font-semibold text-slate-300">{index === 0 ? 'Sender flags' : 'Receiver flags'}</div>
                    <div className="mt-2 break-all font-mono text-slate-100">{flags?.account_id || 'pending'}</div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
                      <span className={cn('rounded-md border px-2 py-1', flags?.is_whitelisted ? 'border-emerald-500/40 text-emerald-200' : 'border-slate-700 text-slate-400')}>
                        WL {flags?.is_whitelisted ? 'yes' : 'no'}
                      </span>
                      <span className={cn('rounded-md border px-2 py-1', flags?.is_blacklisted ? 'border-red-500/40 text-red-200' : 'border-slate-700 text-slate-400')}>
                        BL {flags?.is_blacklisted ? 'yes' : 'no'}
                      </span>
                      <span className="rounded-md border border-slate-700 px-2 py-1 text-slate-300">
                        {formatScore(flags?.risk_score)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </PhaseCard>

          <PhaseCard
            index={2}
            title="Agentic investigation"
            subtitle="Planner, executor, evidence analysis, and report generation using the same fallback pipeline."
            status={phase2}
            icon={<Brain className="h-5 w-5" />}
            expanded={expandedPhases[2] ?? true}
            onToggle={() => togglePhase(2)}
          >
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3">
                  <div className="text-xs text-slate-500">Steps</div>
                  <div className="mt-1 text-2xl font-semibold text-white">{investigationData?.steps ?? phase2Agents.length}</div>
                </div>
                <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3">
                  <div className="text-xs text-slate-500">Evidence</div>
                  <div className="mt-1 text-2xl font-semibold text-white">{investigationData?.evidence_count ?? 0}</div>
                </div>
                <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3">
                  <div className="text-xs text-slate-500">Confidence</div>
                  <div className="mt-1 text-2xl font-semibold text-white">{formatScore(investigationData?.confidence)}</div>
                </div>
              </div>

              <div className="rounded-md border border-slate-800 bg-slate-950">
                <div className="border-b border-slate-800 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Agent status
                </div>
                <div className="divide-y divide-slate-800">
                  {phase2Agents.length ? (
                    phase2Agents.map((agent, index) => (
                      <div key={`${agent.agent || 'agent'}-${index}`} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                        <div className="min-w-0">
                          <div className="truncate font-semibold text-slate-200">{agent.agent || 'agent'}</div>
                          <div className="text-slate-500">
                            {agent.step ? `step ${agent.step}` : 'stream'} {agent.tasks ? `- ${agent.tasks} tasks` : ''}
                          </div>
                        </div>
                        <span className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 font-medium text-cyan-200">
                          {agent.status || 'running'}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="px-3 py-3 text-xs text-slate-500">Waiting for investigation events.</div>
                  )}
                </div>
              </div>

              {report && (
                <div className="rounded-md border border-slate-800 bg-slate-950">
                  <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <FileText className="h-4 w-4" />
                    Evidence report
                  </div>
                  <div className="max-h-56 overflow-y-auto whitespace-pre-wrap px-3 py-3 text-xs leading-relaxed text-slate-300">{report}</div>
                </div>
              )}
            </div>
          </PhaseCard>

          <PhaseCard
            index={3}
            title="Detective decision"
            subtitle="Final adjudication after rule result, evidence, and customer verification signal."
            status={phase3}
            icon={<ShieldAlert className="h-5 w-5" />}
            expanded={expandedPhases[3] ?? true}
            onToggle={() => togglePhase(3)}
            decision={decision}
          >
            <div className="space-y-4">
              <div className={cn('rounded-md border px-4 py-4', decisionClasses[decision] || 'border-slate-800 bg-slate-950 text-slate-300')}>
                <div className="text-xs font-semibold uppercase tracking-wide opacity-70">Final decision</div>
                <div className="mt-1 text-3xl font-semibold uppercase">{decision || 'pending'}</div>
                {detailData?.confidence !== undefined && (
                  <div className="mt-1 text-sm opacity-80">Confidence {formatScore(detailData.confidence)}%</div>
                )}
              </div>

              {phase3Agents.length > 0 && (
                <div className="grid gap-2">
                  {phase3Agents.map((agent, index) => (
                    <div key={`${agent.agent || 'detective'}-${index}`} className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs">
                      <span className="font-semibold text-slate-300">{agent.agent || 'Detective'}</span>
                      <span className="text-slate-500">{agent.status || agent.result || 'running'}</span>
                    </div>
                  ))}
                </div>
              )}

              {detailData?.reasoning && (
                <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Reasoning</div>
                  <div className="whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{detailData.reasoning}</div>
                </div>
              )}

              {actions.length > 0 && (
                <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Actions</div>
                  <div className="grid gap-2">
                    {actions.map((action, index) => (
                      <div key={`${action}-${index}`} className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-xs text-slate-300">
                        {action}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </PhaseCard>
        </div>

        <aside className="space-y-4">
          <div className="rounded-md border border-slate-800 bg-slate-900/70 p-4">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <Clock3 className="h-4 w-4" />
              Event stream
            </div>
            <div className="mt-3 max-h-[420px] space-y-2 overflow-y-auto pr-1">
              {pipelineEvents.length ? (
                pipelineEvents.slice(-18).reverse().map((event) => (
                  <div key={`${event._seq || 0}-${event.event}`} className="rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-200">{event.event}</span>
                      <span className="text-slate-600">#{event._seq}</span>
                    </div>
                    {event.data && (
                      <pre className="mt-2 max-h-24 overflow-hidden whitespace-pre-wrap break-words rounded-md bg-slate-900 p-2 font-mono text-[10px] leading-relaxed text-slate-400">
                        {JSON.stringify(event.data, null, 2)}
                      </pre>
                    )}
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-dashed border-slate-800 px-3 py-8 text-center text-xs text-slate-500">
                  Stream is idle.
                </div>
              )}
            </div>
          </div>

          {apiResult?.message && (
            <div className="rounded-md border border-slate-800 bg-slate-900/70 p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">API message</div>
              <p className="mt-2 text-sm text-slate-300">{apiResult.message}</p>
            </div>
          )}
        </aside>
      </section>
    </div>
  );
}
