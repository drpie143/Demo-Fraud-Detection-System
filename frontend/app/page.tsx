'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { BankingApp } from '@/components/banking-app';
import { FraudDetectionPipeline } from '@/components/fraud-detection-pipeline';

interface TransactionData {
  accountId: string;
  amount: string;
  recipientId: string;
  description: string;
  selectedAccount: { id: string; name: string; balance: number };
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
  detail?: { decision?: string; confidence?: number; reasoning?: string; recommended_actions?: string[] | string };
}

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

export default function Home() {
  const [isProcessing, setIsProcessing] = useState(false);
  const [transactionData, setTransactionData] = useState<TransactionData | null>(null);
  const [fraudResult, setFraudResult] = useState<FraudResult | null>(null);
  const [apiResult, setApiResult] = useState<ApiResult | null>(null);
  const [pipelineEvents, setPipelineEvents] = useState<PipelineEvent[]>([]);
  const [currentPhase, setCurrentPhase] = useState<string>('');
  const [pendingFinalResult, setPendingFinalResult] = useState<FraudResult | null>(null);
  const [pendingApiResult, setPendingApiResult] = useState<ApiResult | null>(null);
  const [biometricDone, setBiometricDone] = useState(false);
  const [pipelineComplete, setPipelineComplete] = useState(false);
  const [pipelineError, setPipelineError] = useState<string | null>(null);
  const escalatedRef = useRef(false);
  const seqRef = useRef(0);

  const pushEvent = useCallback((event: string, data?: Record<string, unknown>) => {
    seqRef.current += 1;
    setPipelineEvents((prev) => [...prev, { event, data, _seq: seqRef.current }]);
  }, []);

  const handleTransactionSubmit = (data: {
    accountId: string;
    amount: string;
    recipientId: string;
    description: string;
  }) => {
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
    setTransactionData({
      ...data,
      selectedAccount: { id: data.accountId, name: data.accountId, balance: 0 },
    });
    setIsProcessing(true);
  };

  const handleReset = () => {
    setIsProcessing(false);
    setTransactionData(null);
    setFraudResult(null);
    setApiResult(null);
    setPipelineEvents([]);
    setCurrentPhase('');
    setPendingFinalResult(null);
    setPendingApiResult(null);
    setBiometricDone(false);
    setPipelineComplete(false);
    setPipelineError(null);
    escalatedRef.current = false;
    seqRef.current = 0;
  };

  // SSE Streaming: read events from backend
  useEffect(() => {
    if (!isProcessing || !transactionData) return;

    const abortController = new AbortController();

    const runStream = async () => {
      try {
        // Call backend directly to avoid Next.js proxy buffering SSE
        const res = await fetch(`${BACKEND_URL}/api/fraud-detection`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account_id: transactionData.selectedAccount?.id || transactionData.accountId,
            amount: parseFloat(transactionData.amount),
            recipient_id: transactionData.recipientId,
            description: transactionData.description || '',
            timestamp: new Date().toISOString(),
          }),
          signal: abortController.signal,
        });

        if (!res.ok) {
          let errorMessage = 'Server error';
          try {
            const errorData = await res.json();
            errorMessage = errorData?.detail || errorData?.message || 'Server error';
          } catch {
            errorMessage = `Server returned ${res.status}`;
          }
          console.error('[Stream] API error:', errorMessage);
          setPipelineError(errorMessage);
          setFraudResult({ status: 'blocked', score: 100, message: errorMessage });
          setIsProcessing(false);
          return;
        }

        const reader = res.body?.getReader();
        if (!reader) {
          console.error('[Stream] No reader available');
          return;
        }

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
              const eventName = payload.event;
              const eventData = payload.data;

              console.log('[Stream Event]', eventName, eventData);

              // Push every event to the queue so pipeline sees all of them
              pushEvent(eventName, eventData);

              // Track current phase for phone spinner feedback
              if (eventName === 'phase1_start') setCurrentPhase('Phase 1: Screening rules...');
              if (eventName === 'phase1_done') {
                setCurrentPhase('Phase 1 complete');
                const riskLevel = (eventData?.risk_level || '').toLowerCase();
                setApiResult((prev) => ({
                  ...prev,
                  phase1: eventData?.phase1,
                  risk_level: eventData?.risk_level,
                }));

                // Trigger biometric/OTP for non-green transactions
                if (riskLevel === 'yellow' || riskLevel === 'red') {
                  escalatedRef.current = true;
                  setFraudResult({
                    status: 'escalate',
                    score: 0,
                    decision: 'escalate',
                    message: 'Additional verification required — transaction under review',
                  });
                }
              }

              if (eventName === 'phase2_start') {
                setCurrentPhase('Phase 2: Investigation...');
                // Ensure escalated flag is set (may already be set from phase1_done)
                escalatedRef.current = true;
              }

              if (eventName === 'phase2_progress') {
                const agent = eventData?.agent || '';
                const status = eventData?.status || '';
                setCurrentPhase(`Phase 2: ${agent} ${status}`);
              }

              if (eventName === 'phase2_done') {
                setCurrentPhase('Phase 2 complete');
                setApiResult((prev) => ({
                  ...prev,
                  investigation: eventData?.investigation,
                }));
              }

              if (eventName === 'phase3_start') {
                setCurrentPhase('Phase 3: Detective deciding...');
              }

              if (eventName === 'phase3_progress') {
                const agent = eventData?.agent || '';
                const status = eventData?.status || '';
                setCurrentPhase(`Phase 3: ${agent} ${status}`);
              }

              if (eventName === 'phase3_done') {
                setCurrentPhase('Phase 3 complete');
                setApiResult((prev) => ({
                  ...prev,
                  detail: eventData?.detail,
                  report: eventData?.report,
                }));
              }

              if (eventName === 'complete') {
                const decision = (eventData?.decision || '').toLowerCase();
                let status: 'approved' | 'escalate' | 'blocked' = 'blocked';
                if (decision === 'allow') status = 'approved';
                else if (decision === 'escalate') status = 'escalate';

                const finalResult: FraudResult = {
                  status,
                  score: Math.round((eventData?.investigation?.confidence || 0) * 100),
                  decision: eventData?.decision,
                  message: eventData?.message,
                };

                setIsProcessing(false);

                if (escalatedRef.current) {
                  // Store pending, don't show yet — wait for OTP + pipeline animation
                  setPendingFinalResult(finalResult);
                  setPendingApiResult(eventData as ApiResult);
                  // Delay so right panel animation finishes before result shows
                  setTimeout(() => setPipelineComplete(true), 2000);
                } else {
                  // No OTP needed — show result directly
                  setApiResult(eventData as ApiResult);
                  setFraudResult(finalResult);
                }
              }

              if (eventName === 'error') {
                const errorMsg = eventData?.message || 'Pipeline error occurred';
                console.error('[Stream] Pipeline error:', errorMsg);
                setPipelineError(errorMsg);
                setFraudResult({
                  status: 'escalate',
                  score: 0,
                  message: errorMsg
                });
              }
            } catch (parseErr) {
              console.warn('[Stream] Parse error:', parseErr, trimmed);
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return;
        const errorMsg = err instanceof Error ? err.message : 'Connection failed';
        console.error('[Stream] Connection error:', err);
        setPipelineError(`Connection error: ${errorMsg}`);
        setFraudResult({ status: 'blocked', score: 0, message: `Connection error: ${errorMsg}` });
        setIsProcessing(false);
      }
    };

    runStream();

    return () => abortController.abort();
  }, [isProcessing, transactionData, pushEvent]);

  // Apply final result ONLY when BOTH conditions are met:
  //   1. OTP verified (biometricDone)
  //   2. Pipeline finished + animation delay done (pipelineComplete)
  useEffect(() => {
    if (biometricDone && pipelineComplete && pendingFinalResult) {
      if (pendingApiResult) setApiResult(pendingApiResult);
      setFraudResult(pendingFinalResult);
      setPendingFinalResult(null);
      setPendingApiResult(null);
    }
  }, [biometricDone, pipelineComplete, pendingFinalResult, pendingApiResult]);

  const handleBiometricDone = () => {
    setBiometricDone(true);
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 p-4">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8 text-center">
          <h1 className="text-4xl font-bold text-white mb-2">Fraud Detection System</h1>
          <p className="text-gray-300">Banking Transaction Pipeline Visualization</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="flex items-start justify-center">
            <BankingApp
              onTransactionSubmit={handleTransactionSubmit}
              onReset={handleReset}
              fraudResult={fraudResult}
              currentPhase={currentPhase}
              onBiometricDone={handleBiometricDone}
            />
          </div>

          <div className="bg-white rounded-lg shadow-2xl p-6 max-h-[700px] overflow-y-auto">
            <h2 className="text-2xl font-bold text-gray-900 mb-6">Fraud Detection Pipeline</h2>
            <FraudDetectionPipeline
              isProcessing={isProcessing}
              transactionData={transactionData}
              apiResult={apiResult}
              pipelineEvents={pipelineEvents}
            />
          </div>
        </div>

        <div className="mt-8 bg-blue-900/20 border border-blue-500/30 rounded-lg p-6 text-gray-100">
          <h3 className="font-semibold text-lg mb-3">How to use:</h3>
          <ol className="list-decimal list-inside space-y-2 text-sm">
            <li>Log in with an Account ID (any password)</li>
            <li>Enter Recipient ID + transfer amount</li>
            <li>Confirm the transaction — Pipeline runs in real-time</li>
            <li>Watch each Phase on the right panel</li>
          </ol>
        </div>
      </div>
    </main>
  );
}
