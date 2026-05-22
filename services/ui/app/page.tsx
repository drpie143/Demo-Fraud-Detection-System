'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, Database, ShieldCheck, Signal } from 'lucide-react';
import { BankingApp, type Scenario } from '@/components/banking-app';
import { FraudDetectionPipeline } from '@/components/fraud-detection-pipeline';

interface TransactionData {
  accountId: string;
  amount: string;
  recipientId: string;
  description: string;
  selectedAccount: { id: string; name: string; balance: number };
  deviceId?: string;
  ipAddress?: string;
  authMethod?: string;
  senderBalanceBefore?: number | null;
  senderBalanceAfter?: number | null;
  expectedIsFraud?: boolean | null;
  scenarioName?: string;
  currency?: string;
}

interface FraudResult {
  status: 'pending' | 'approved' | 'blocked' | 'escalate';
  score: number;
  decision?: string;
  message?: string;
}

interface PipelineEvent {
  event: string;
  data?: Record<string, unknown>;
  _seq?: number;
}

interface ApiResult {
  transaction_id?: string;
  decision?: string;
  message?: string;
  phase1?: {
    risk_score?: number;
    risk_level?: string;
    triggered_rules?: Array<{ severity: string; rule: string; detail: string }>;
    sender_flags?: { account_id: string; is_whitelisted: boolean; is_blacklisted: boolean; risk_score?: number };
    receiver_flags?: { account_id: string; is_whitelisted: boolean; is_blacklisted: boolean; risk_score?: number };
  };
  risk_level?: string;
  investigation?: { steps: number; evidence_count: number; confidence: number };
  report?: string | { detailed_analysis?: string; summary?: string };
  detail?: { decision?: string; confidence?: number; reasoning?: string; actions?: string[] | string; recommended_actions?: string[] | string };
}

const API_BASE = '';

export default function Home() {
  const [isProcessing, setIsProcessing] = useState(false);
  const [transactionData, setTransactionData] = useState<TransactionData | null>(null);
  const [fraudResult, setFraudResult] = useState<FraudResult | null>(null);
  const [apiResult, setApiResult] = useState<ApiResult | null>(null);
  const [pipelineEvents, setPipelineEvents] = useState<PipelineEvent[]>([]);
  const [currentPhase, setCurrentPhase] = useState('');
  const [pendingFinalResult, setPendingFinalResult] = useState<FraudResult | null>(null);
  const [pendingApiResult, setPendingApiResult] = useState<ApiResult | null>(null);
  const [biometricDone, setBiometricDone] = useState(false);
  const [pipelineComplete, setPipelineComplete] = useState(false);
  const [pipelineError, setPipelineError] = useState<string | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [datasetStatus, setDatasetStatus] = useState('Loading dataset scenarios');
  const escalatedRef = useRef(false);
  const seqRef = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const loadScenarios = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/scenarios`, { signal: controller.signal });
        if (!res.ok) throw new Error(`Scenario API returned ${res.status}`);
        const data = (await res.json()) as Scenario[];
        setScenarios(data);
        setDatasetStatus(`${data.length} dataset scenarios ready`);
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return;
        setDatasetStatus('Scenario API unavailable');
      }
    };
    loadScenarios();
    return () => controller.abort();
  }, []);

  const pushEvent = useCallback((event: string, data?: Record<string, unknown>) => {
    seqRef.current += 1;
    setPipelineEvents((prev) => [...prev, { event, data, _seq: seqRef.current }]);
  }, []);

  const resetPipelineState = () => {
    setFraudResult(null);
    setApiResult(null);
    setPipelineEvents([]);
    seqRef.current = 0;
    setCurrentPhase('');
    setPendingFinalResult(null);
    setPendingApiResult(null);
    setBiometricDone(false);
    setPipelineComplete(false);
    setPipelineError(null);
    escalatedRef.current = false;
  };

  const handleTransactionSubmit = (data: Omit<TransactionData, 'selectedAccount'> & { selectedAccount?: TransactionData['selectedAccount'] }) => {
    resetPipelineState();
    setTransactionData({
      ...data,
      selectedAccount: data.selectedAccount || { id: data.accountId, name: data.accountId, balance: 0 },
    });
    setIsProcessing(true);
  };

  const handleReset = () => {
    setIsProcessing(false);
    setTransactionData(null);
    resetPipelineState();
  };

  useEffect(() => {
    if (!isProcessing || !transactionData) return;

    const abortController = new AbortController();

    const runStream = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/fraud-detection`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account_id: transactionData.selectedAccount?.id || transactionData.accountId,
            amount: Number.parseFloat(transactionData.amount),
            recipient_id: transactionData.recipientId,
            description: transactionData.description || '',
            timestamp: new Date().toISOString(),
            device_id: transactionData.deviceId || '',
            ip_address: transactionData.ipAddress || '',
            auth_method: transactionData.authMethod || '',
            sender_balance_before: transactionData.senderBalanceBefore,
            sender_balance_after: transactionData.senderBalanceAfter,
            currency: transactionData.currency || 'VND',
          }),
          signal: abortController.signal,
        });

        if (!res.ok) {
          let errorMessage = `Server returned ${res.status}`;
          try {
            const errorData = await res.json();
            errorMessage = errorData?.detail || errorData?.message || errorMessage;
          } catch {
            // Keep status-derived message.
          }
          setPipelineError(errorMessage);
          setFraudResult({ status: 'blocked', score: 100, message: errorMessage });
          setIsProcessing(false);
          return;
        }

        const reader = res.body?.getReader();
        if (!reader) return;

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith('data: ')) continue;

            try {
              const payload = JSON.parse(trimmed.slice(6));
              const eventName = payload.event as string;
              const eventData = payload.data as Record<string, unknown> | undefined;

              pushEvent(eventName, eventData);

              if (eventName === 'phase1_start') setCurrentPhase('Screening rules');
              if (eventName === 'phase1_done') {
                const riskLevel = String(eventData?.risk_level || '').toLowerCase();
                setCurrentPhase(`Phase 1 ${riskLevel || 'complete'}`);
                setApiResult((prev) => ({
                  ...prev,
                  phase1: eventData?.phase1 as ApiResult['phase1'],
                  risk_level: eventData?.risk_level as string,
                }));
                if (riskLevel === 'yellow' || riskLevel === 'red') {
                  escalatedRef.current = true;
                  setFraudResult({
                    status: 'escalate',
                    score: 0,
                    decision: 'escalate',
                    message: 'Additional verification required while investigation runs',
                  });
                }
              }

              if (eventName === 'phase2_start') {
                escalatedRef.current = true;
                setCurrentPhase('Agentic investigation');
              }
              if (eventName === 'phase2_progress') setCurrentPhase(`Agent: ${eventData?.agent || ''} ${eventData?.status || ''}`);
              if (eventName === 'phase2_done') {
                setCurrentPhase('Evidence collected');
                setApiResult((prev) => ({ ...prev, investigation: eventData?.investigation as ApiResult['investigation'] }));
              }
              if (eventName === 'phase3_start') setCurrentPhase('Detective deciding');
              if (eventName === 'phase3_progress') setCurrentPhase(`Decision: ${eventData?.agent || ''} ${eventData?.status || ''}`);
              if (eventName === 'phase3_done') {
                setCurrentPhase('Decision complete');
                setApiResult((prev) => ({
                  ...prev,
                  detail: eventData?.detail as ApiResult['detail'],
                  report: eventData?.report as ApiResult['report'],
                }));
              }

              if (eventName === 'complete') {
                const decision = String(eventData?.decision || '').toLowerCase();
                const status = decision === 'allow' ? 'approved' : decision === 'escalate' ? 'escalate' : 'blocked';
                const finalResult: FraudResult = {
                  status,
                  score: Math.round(((eventData?.investigation as { confidence?: number } | undefined)?.confidence || 0) * 100),
                  decision,
                  message: eventData?.message as string,
                };

                setIsProcessing(false);
                if (escalatedRef.current) {
                  setPendingFinalResult(finalResult);
                  setPendingApiResult(eventData as ApiResult);
                  setTimeout(() => setPipelineComplete(true), 900);
                } else {
                  setApiResult(eventData as ApiResult);
                  setFraudResult(finalResult);
                }
              }

              if (eventName === 'error') {
                const errorMsg = String(eventData?.message || 'Pipeline error occurred');
                setPipelineError(errorMsg);
                setFraudResult({ status: 'escalate', score: 0, message: errorMsg });
              }
            } catch (parseErr) {
              console.warn('[Stream] Parse error:', parseErr, trimmed);
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return;
        const errorMsg = err instanceof Error ? err.message : 'Connection failed';
        setPipelineError(`Connection error: ${errorMsg}`);
        setFraudResult({ status: 'blocked', score: 0, message: `Connection error: ${errorMsg}` });
        setIsProcessing(false);
      }
    };

    runStream();
    return () => abortController.abort();
  }, [isProcessing, transactionData, pushEvent]);

  useEffect(() => {
    if (biometricDone && pipelineComplete && pendingFinalResult) {
      if (pendingApiResult) setApiResult(pendingApiResult);
      setFraudResult(pendingFinalResult);
      setPendingFinalResult(null);
      setPendingApiResult(null);
    }
  }, [biometricDone, pipelineComplete, pendingFinalResult, pendingApiResult]);

  const totalEvents = pipelineEvents.length;
  const decision = apiResult?.decision || fraudResult?.decision || '';

  return (
    <main className="min-h-screen bg-[#0b1117] text-slate-100">
      <div className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-5 px-4 py-5">
        <header className="flex flex-col gap-4 rounded-lg border border-slate-800 bg-slate-950/80 px-5 py-4 shadow-2xl shadow-black/20 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">
              <ShieldCheck className="h-4 w-4" />
              Dataset-driven fraud operations
            </div>
            <h1 className="text-2xl font-semibold tracking-tight text-white md:text-3xl">Fraud Detection Console</h1>
            <p className="mt-1 text-sm text-slate-400">Real account IDs from final.csv, streamed through rule screening and agentic investigation.</p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2">
              <div className="flex items-center gap-1 text-slate-400"><Database className="h-3.5 w-3.5" /> Dataset</div>
              <div className="mt-1 font-semibold text-white">{datasetStatus}</div>
            </div>
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2">
              <div className="flex items-center gap-1 text-slate-400"><Signal className="h-3.5 w-3.5" /> Stream</div>
              <div className="mt-1 font-semibold text-white">{totalEvents} events</div>
            </div>
            <div className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2">
              <div className="flex items-center gap-1 text-slate-400"><Activity className="h-3.5 w-3.5" /> Decision</div>
              <div className="mt-1 font-semibold uppercase text-white">{decision || 'idle'}</div>
            </div>
          </div>
        </header>

        {pipelineError && (
          <div className="rounded-md border border-red-900/60 bg-red-950/50 px-4 py-3 text-sm text-red-100">{pipelineError}</div>
        )}

        <section className="grid flex-1 grid-cols-1 gap-5 lg:grid-cols-[390px_minmax(0,1fr)]">
          <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-4">
            <BankingApp
              scenarios={scenarios}
              onTransactionSubmit={handleTransactionSubmit}
              onReset={handleReset}
              fraudResult={fraudResult}
              currentPhase={currentPhase}
              onBiometricDone={() => setBiometricDone(true)}
            />
          </div>

          <div className="min-h-[720px] rounded-lg border border-slate-800 bg-slate-950/70 p-4">
            <FraudDetectionPipeline
              isProcessing={isProcessing}
              transactionData={transactionData}
              apiResult={apiResult}
              pipelineEvents={pipelineEvents}
            />
          </div>
        </section>
      </div>
    </main>
  );
}
