'use client';

import { useState, useEffect, useRef } from 'react';
import { CheckCircle, AlertCircle, ChevronDown, Zap, Brain, Shield, XCircle } from 'lucide-react';
import { Card } from './ui/card';

interface AgentProgress {
  agent: string;
  status: string;
  step?: number;
  tasks?: number;
  evidence_count?: number;
  result?: string;
}

interface Phase1Data {
  risk_score?: number;
  risk_level?: string;
  triggered_rules?: Array<{ severity: string; rule: string; detail: string }>;
  sender_flags?: {
    account_id: string;
    is_whitelisted: boolean;
    is_blacklisted: boolean;
    risk_score?: number;
  };
  receiver_flags?: {
    account_id: string;
    is_whitelisted: boolean;
    is_blacklisted: boolean;
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

interface FraudDetectionPipelineProps {
  isProcessing: boolean;
  transactionData?: {
    amount: string;
    recipientId: string;
    selectedAccount?: { id: string; name: string; balance: number };
  } | null;
  apiResult?: ApiResult | null;
  pipelineEvents?: PipelineEvent[];
  pipelineEvent?: PipelineEvent | null;
}

type PhaseStatus = 'idle' | 'processing' | 'completed';

export function FraudDetectionPipeline({
  isProcessing,
  transactionData,
  apiResult,
  pipelineEvents,
}: FraudDetectionPipelineProps) {
  const [expandedPhases, setExpandedPhases] = useState<Record<number, boolean>>({});
  const [phase1Status, setPhase1Status] = useState<PhaseStatus>('idle');
  const [phase2Status, setPhase2Status] = useState<PhaseStatus>('idle');
  const [phase3Status, setPhase3Status] = useState<PhaseStatus>('idle');
  const [phase2Agents, setPhase2Agents] = useState<AgentProgress[]>([]);
  const [phase3Agents, setPhase3Agents] = useState<AgentProgress[]>([]);
  const processedCountRef = useRef(0);

  // React to SSE events — process all new events in the array
  useEffect(() => {
    if (!pipelineEvents || pipelineEvents.length <= processedCountRef.current) return;

    for (let i = processedCountRef.current; i < pipelineEvents.length; i++) {
      const { event, data } = pipelineEvents[i];

      switch (event) {
        case 'phase1_start':
          setPhase1Status('processing');
          break;
        case 'phase1_done':
          setPhase1Status('completed');
          setExpandedPhases((prev) => ({ ...prev, 0: true }));
          break;
        case 'phase2_start':
          setPhase2Status('processing');
          break;
        case 'phase2_progress':
          if (data) {
            setPhase2Agents((prev) => {
              const agentData = data as unknown as AgentProgress;
              const existing = prev.findIndex((a) => a.agent === agentData.agent && a.step === agentData.step);
              if (existing >= 0) {
                const updated = [...prev];
                updated[existing] = agentData;
                return updated;
              }
              return [...prev, agentData];
            });
          }
          break;
        case 'phase2_done':
          setPhase2Status('completed');
          setExpandedPhases((prev) => ({ ...prev, 1: true }));
          break;
        case 'phase3_start':
          setPhase3Status('processing');
          break;
        case 'phase3_progress':
          if (data) {
            setPhase3Agents((prev) => {
              const agentData = data as unknown as AgentProgress;
              const existing = prev.findIndex((a) => a.agent === agentData.agent);
              if (existing >= 0) {
                const updated = [...prev];
                updated[existing] = agentData;
                return updated;
              }
              return [...prev, agentData];
            });
          }
          break;
        case 'phase3_done':
          setPhase3Status('completed');
          setExpandedPhases((prev) => ({ ...prev, 2: true }));
          break;
        case 'complete':
          if (phase1Status === 'processing') setPhase1Status('completed');
          break;
      }
    }

    processedCountRef.current = pipelineEvents.length;
  }, [pipelineEvents, phase1Status]);

  // Reset when not processing and no apiResult
  useEffect(() => {
    if (!isProcessing && !apiResult) {
      setPhase1Status('idle');
      setPhase2Status('idle');
      setPhase3Status('idle');
      setPhase2Agents([]);
      setPhase3Agents([]);
      setExpandedPhases({});
      processedCountRef.current = 0;
    }
  }, [isProcessing, apiResult]);

  const togglePhase = (index: number) => {
    setExpandedPhases((prev) => ({ ...prev, [index]: !prev[index] }));
  };

  const decision = apiResult ? (apiResult.decision || '').toLowerCase() : null;
  const phase1Data = apiResult?.phase1;
  const investigationData = apiResult?.investigation;
  const reportData = apiResult?.report;
  const detailData = apiResult?.detail;

  const getStatusClasses = (status: PhaseStatus, decisionColor?: string) => {
    if (status === 'completed') return decisionColor || 'bg-green-50 border-green-200';
    if (status === 'processing') return 'bg-blue-50 border-blue-200';
    return 'bg-gray-50 border-gray-200';
  };

  const getCircleClasses = (status: PhaseStatus) => {
    if (status === 'completed') return 'bg-green-500';
    if (status === 'processing') return 'bg-blue-500 animate-pulse';
    return 'bg-gray-400';
  };

  return (
    <div className="space-y-6 w-full">
      {/* Transaction Info */}
      {transactionData && (
        <div className="p-3 bg-gradient-to-r from-blue-50 to-blue-100 border border-blue-200 rounded-lg">
          <h3 className="font-bold text-xs text-blue-900 mb-2 uppercase tracking-wide">Transaction Details</h3>
          <div className="space-y-1 text-xs text-blue-800">
            <div className="flex justify-between">
              <span>From: <span className="font-semibold">{transactionData.selectedAccount?.id}</span></span>
            </div>
            <div className="flex justify-between">
              <span>To: <span className="font-semibold">{transactionData.recipientId}</span></span>
            </div>
            <div className="flex justify-between">
              <span>Amount: <span className="font-bold text-lg">${Number(transactionData.amount).toLocaleString()}</span></span>
            </div>
          </div>
        </div>
      )}

      <div className="space-y-4">
        {/* ============ PHASE 1 ============ */}
        {phase1Status !== 'idle' && (
          <div className="flex flex-col items-center">
            <div
              onClick={() => togglePhase(0)}
              className={`cursor-pointer transition-all duration-300 rounded-xl border-2 p-4 w-full max-w-xs text-center hover:shadow-lg ${getStatusClasses(phase1Status)} ${expandedPhases[0] ? 'ring-2 ring-blue-400' : ''}`}
            >
              <div className="flex items-center justify-center gap-2 mb-2">
                <div className={`w-12 h-12 rounded-full flex items-center justify-center text-white flex-shrink-0 ${getCircleClasses(phase1Status)}`}>
                  <Zap className="w-6 h-6" />
                </div>
                <div className="text-left">
                  <div className="font-bold text-sm text-gray-900">Phase 1</div>
                  <div className="font-semibold text-xs text-gray-700">Rule-based Screening</div>
                </div>
              </div>
              {phase1Status === 'processing' && (
                <div className="mt-2 pt-2 border-t border-gray-300 text-xs">
                  <span className="text-blue-600 font-bold">Analyzing rules...</span>
                </div>
              )}
              {phase1Status === 'completed' && (
                <div className="mt-2 pt-2 border-t border-gray-300 text-xs">
                  <span className="text-green-600 font-bold">Screening complete</span>
                  {apiResult?.risk_level && (
                    <span className={`ml-2 font-bold ${
                      apiResult.risk_level === 'green' ? 'text-green-600' :
                      apiResult.risk_level === 'red' ? 'text-red-600' : 'text-yellow-600'
                    }`}>
                      ({apiResult.risk_level.toUpperCase()})
                    </span>
                  )}
                  <ChevronDown className={`w-4 h-4 inline ml-1 transition-transform ${expandedPhases[0] ? 'rotate-180' : ''}`} />
                </div>
              )}
            </div>

            {/* Phase 1 Details */}
            {expandedPhases[0] && phase1Data && (
              <div className="mt-3 w-full max-w-xs">
                <Card className="p-3 bg-white border-gray-200 text-xs space-y-2">
                  {phase1Data.risk_score !== undefined && (
                    <div className="flex justify-between">
                      <span className="text-gray-600">Risk Score:</span>
                      <span className={`font-bold ${phase1Data.risk_score > 0.7 ? 'text-red-600' : phase1Data.risk_score > 0.3 ? 'text-yellow-600' : 'text-green-600'}`}>
                        {(phase1Data.risk_score * 100).toFixed(0)}/100
                      </span>
                    </div>
                  )}
                  {phase1Data.risk_level && (
                    <div className="flex justify-between">
                      <span className="text-gray-600">Risk Level:</span>
                      <span className="font-bold">{phase1Data.risk_level}</span>
                    </div>
                  )}
                  {phase1Data.triggered_rules && (
                    <div className="flex justify-between">
                      <span className="text-gray-600">Triggered Rules:</span>
                      <span className="font-bold">{phase1Data.triggered_rules.length}</span>
                    </div>
                  )}
                  {phase1Data.triggered_rules?.map((rule, i) => (
                    <div key={i} className="bg-yellow-50 p-1.5 rounded text-[10px]">
                      <span className="font-bold text-yellow-800">[{rule.severity}]</span>{' '}
                      <span className="text-gray-800">{rule.rule}: {rule.detail}</span>
                    </div>
                  ))}
                  {phase1Data.sender_flags && (
                    <div className="mt-1 pt-1 border-t border-gray-200 text-[10px]">
                      <div className="font-semibold text-gray-600 mb-1">Sender: {phase1Data.sender_flags.account_id}</div>
                      <div>WL: {phase1Data.sender_flags.is_whitelisted ? 'Yes' : 'No'} | BL: {phase1Data.sender_flags.is_blacklisted ? 'Yes' : 'No'} | Risk: {phase1Data.sender_flags.risk_score?.toFixed(2)}</div>
                    </div>
                  )}
                  {phase1Data.receiver_flags && (
                    <div className="text-[10px]">
                      <div className="font-semibold text-gray-600 mb-1">Receiver: {phase1Data.receiver_flags.account_id}</div>
                      <div>WL: {phase1Data.receiver_flags.is_whitelisted ? 'Yes' : 'No'} | BL: {phase1Data.receiver_flags.is_blacklisted ? 'Yes' : 'No'} | Risk: {phase1Data.receiver_flags.risk_score?.toFixed(2)}</div>
                    </div>
                  )}
                </Card>
              </div>
            )}

            {/* Arrow */}
            {(phase2Status !== 'idle' || phase3Status !== 'idle' || (phase1Status === 'completed' && decision)) && (
              <div className="h-6 flex items-center justify-center">
                <div className="w-0.5 h-full bg-gray-300"></div>
              </div>
            )}
          </div>
        )}

        {/* ============ PHASE 2: Investigation ============ */}
        {phase2Status !== 'idle' && (
          <div className="flex flex-col items-center">
            <div
              onClick={() => togglePhase(1)}
              className={`cursor-pointer transition-all duration-300 rounded-xl border-2 p-4 w-full max-w-xs text-center hover:shadow-lg ${getStatusClasses(phase2Status)} ${expandedPhases[1] ? 'ring-2 ring-blue-400' : ''}`}
            >
              <div className="flex items-center justify-center gap-2 mb-2">
                <div className={`w-12 h-12 rounded-full flex items-center justify-center text-white flex-shrink-0 ${getCircleClasses(phase2Status)}`}>
                  <Brain className="w-6 h-6" />
                </div>
                <div className="text-left">
                  <div className="font-bold text-sm text-gray-900">Phase 2</div>
                  <div className="font-semibold text-xs text-gray-700">Agentic Investigation</div>
                </div>
              </div>
              {phase2Status === 'processing' && (
                <div className="mt-2 pt-2 border-t border-gray-300 text-xs">
                  <span className="text-blue-600 font-bold">
                    {phase2Agents.length > 0
                      ? `${phase2Agents[phase2Agents.length - 1]?.agent}: ${phase2Agents[phase2Agents.length - 1]?.status}`
                      : 'Starting investigation...'}
                  </span>
                </div>
              )}
              {phase2Status === 'completed' && (
                <div className="mt-2 pt-2 border-t border-gray-300 text-xs">
                  <span className="text-green-600 font-bold">
                    {investigationData?.steps || 0} steps, {investigationData?.evidence_count || 0} evidence
                  </span>
                  <ChevronDown className={`w-4 h-4 inline ml-1 transition-transform ${expandedPhases[1] ? 'rotate-180' : ''}`} />
                </div>
              )}
            </div>

            {/* Phase 2 Agent Progress */}
            {expandedPhases[1] && phase2Agents.length > 0 && (
              <div className="mt-3 w-full max-w-xs">
                <Card className="p-3 bg-white border-gray-200 text-xs space-y-1.5">
                  {phase2Agents.map((agent, i) => (
                    <div key={i} className="flex items-center justify-between">
                      <span className="text-gray-700 font-medium">{agent.agent}{agent.step ? ` (step ${agent.step})` : ''}</span>
                      <span className={`text-xs font-bold ${
                        agent.status === 'done' ? 'text-green-600' :
                        agent.status === 'sufficient_evidence' ? 'text-green-600' :
                        agent.status === 'need_more' ? 'text-yellow-600' :
                        'text-blue-600'
                      }`}>
                        {agent.status === 'done' ? 'Done' :
                         agent.status === 'sufficient_evidence' ? 'Done' :
                         agent.status === 'need_more' ? 'More' :
                         agent.status}
                      </span>
                    </div>
                  ))}
                  {investigationData?.confidence !== undefined && (
                    <div className="mt-2 pt-2 border-t border-gray-200 flex justify-between">
                      <span className="text-gray-600">Confidence:</span>
                      <span className="font-bold text-blue-600">{(investigationData.confidence * 100).toFixed(0)}%</span>
                    </div>
                  )}
                  {reportData && (
                    <div className="mt-2 pt-2 border-t border-gray-200">
                      <div className="text-gray-600 font-semibold mb-1">Report:</div>
                      <div className="text-gray-800 whitespace-pre-wrap max-h-32 overflow-y-auto bg-gray-50 p-2 rounded text-[10px] leading-relaxed">
                        {typeof reportData === 'string' ? reportData :
                         reportData.detailed_analysis || reportData.summary || JSON.stringify(reportData, null, 2)}
                      </div>
                    </div>
                  )}
                </Card>
              </div>
            )}

            {/* Arrow */}
            {(phase3Status !== 'idle') && (
              <div className="h-6 flex items-center justify-center">
                <div className="w-0.5 h-full bg-gray-300"></div>
              </div>
            )}
          </div>
        )}

        {/* ============ PHASE 3: Detective Decision ============ */}
        {phase3Status !== 'idle' && (
          <div className="flex flex-col items-center">
            <div
              onClick={() => togglePhase(2)}
              className={`cursor-pointer transition-all duration-300 rounded-xl border-2 p-4 w-full max-w-xs text-center hover:shadow-lg ${
                phase3Status === 'completed'
                  ? decision === 'allow' ? 'bg-green-50 border-green-200'
                    : decision === 'block' ? 'bg-red-50 border-red-200'
                    : 'bg-yellow-50 border-yellow-200'
                  : getStatusClasses(phase3Status)
              } ${expandedPhases[2] ? 'ring-2 ring-blue-400' : ''}`}
            >
              <div className="flex items-center justify-center gap-2 mb-2">
                <div className={`w-12 h-12 rounded-full flex items-center justify-center text-white flex-shrink-0 ${
                  phase3Status === 'completed'
                    ? decision === 'allow' ? 'bg-green-500' : decision === 'block' ? 'bg-red-500' : 'bg-yellow-500'
                    : getCircleClasses(phase3Status)
                }`}>
                  <Shield className="w-6 h-6" />
                </div>
                <div className="text-left">
                  <div className="font-bold text-sm text-gray-900">Phase 3</div>
                  <div className="font-semibold text-xs text-gray-700">Detective Decision</div>
                </div>
              </div>
              {phase3Status === 'processing' && (
                <div className="mt-2 pt-2 border-t border-gray-300 text-xs">
                  <span className="text-blue-600 font-bold">
                    {phase3Agents.length > 0
                      ? `${phase3Agents[phase3Agents.length - 1]?.agent}: ${phase3Agents[phase3Agents.length - 1]?.status}`
                      : 'Making decision...'}
                  </span>
                </div>
              )}
              {phase3Status === 'completed' && (
                <div className="mt-2 pt-2 border-t border-gray-300 text-xs">
                  {decision === 'allow' && <span className="text-green-600 font-bold">ALLOW</span>}
                  {decision === 'block' && <span className="text-red-600 font-bold">BLOCK</span>}
                  {decision === 'escalate' && <span className="text-yellow-600 font-bold">ESCALATE</span>}
                  <ChevronDown className={`w-4 h-4 inline ml-1 transition-transform ${expandedPhases[2] ? 'rotate-180' : ''}`} />
                </div>
              )}
            </div>

            {/* Phase 3 Details */}
            {expandedPhases[2] && detailData && (
              <div className="mt-3 w-full max-w-xs">
                <Card className="p-3 bg-white border-gray-200 text-xs space-y-2">
                  {detailData.decision && (
                    <div className="flex justify-between">
                      <span className="text-gray-600">Decision:</span>
                      <span className="font-bold">{detailData.decision}</span>
                    </div>
                  )}
                  {detailData.confidence !== undefined && (
                    <div className="flex justify-between">
                      <span className="text-gray-600">Confidence:</span>
                      <span className="font-bold">{(detailData.confidence * 100).toFixed(0)}%</span>
                    </div>
                  )}
                  {detailData.reasoning && (
                    <div className="mt-2 pt-2 border-t border-gray-200">
                      <div className="text-gray-600 font-semibold mb-1">Reasoning:</div>
                      <div className="text-gray-800 whitespace-pre-wrap max-h-32 overflow-y-auto bg-gray-50 p-2 rounded text-[10px] leading-relaxed">
                        {detailData.reasoning}
                      </div>
                    </div>
                  )}
                  {detailData.recommended_actions && (
                    <div className="mt-2 pt-2 border-t border-gray-200">
                      <div className="text-gray-600 font-semibold mb-1">Actions:</div>
                      <div className="text-gray-800 whitespace-pre-wrap max-h-24 overflow-y-auto bg-gray-50 p-2 rounded text-[10px] leading-relaxed">
                        {Array.isArray(detailData.recommended_actions)
                          ? detailData.recommended_actions.join('\n')
                          : String(detailData.recommended_actions)}
                      </div>
                    </div>
                  )}
                </Card>
              </div>
            )}
          </div>
        )}
      </div>

      {/* FINAL DECISION BOX */}
      {apiResult?.decision && !isProcessing && (
        <div className={`mt-6 p-5 rounded-xl border-2 text-center font-bold ${
          decision === 'allow' ? 'bg-green-100 border-green-300 text-green-900' :
          decision === 'block' ? 'bg-red-100 border-red-300 text-red-900' :
          'bg-yellow-100 border-yellow-300 text-yellow-900'
        }`}>
          <div className="flex items-center justify-center gap-3 mb-2">
            {decision === 'allow' ? <CheckCircle className="w-8 h-8" /> :
             decision === 'block' ? <XCircle className="w-8 h-8" /> :
             <AlertCircle className="w-8 h-8" />}
            <span className="text-2xl">{decision?.toUpperCase()}</span>
          </div>
          {apiResult.message && (
            <div className="text-sm opacity-90 mt-1">{apiResult.message}</div>
          )}
        </div>
      )}

      {/* Idle State */}
      {!isProcessing && !apiResult && phase1Status === 'idle' && (
        <div className="text-center py-12 text-gray-400">
          <Shield className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="text-sm">Log in and create a transaction to start</p>
        </div>
      )}
    </div>
  );
}
