'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  Fingerprint,
  Landmark,
  Loader2,
  LockKeyhole,
  RotateCcw,
  Send,
  ShieldCheck,
  Smartphone,
  UserRound,
  WalletCards,
  XCircle,
} from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';

type BankingStep = 'login' | 'transfer-details' | 'confirmation' | 'processing';

interface Account {
  id: string;
  name: string;
  balance: number;
}

interface UserInfo {
  id: string;
  name: string;
  kyc_status: string;
  risk_category: string;
  fraud_ratio?: number;
}

interface FraudResult {
  status: 'pending' | 'approved' | 'blocked' | 'escalate';
  score: number;
  decision?: string;
  message?: string;
}

export interface Scenario {
  id: number;
  name: string;
  description: string;
  expected_decision?: string;
  transaction: {
    transaction_id: string;
    sender_id: string;
    sender_name?: string;
    receiver_id: string;
    receiver_name?: string;
    amount: number;
    currency?: string;
    transaction_type?: string;
    device_id?: string;
    ip_address?: string;
    auth_method?: string;
    sender_balance_before?: number | null;
    sender_balance_after?: number | null;
    description?: string;
    expected_is_fraud?: boolean | null;
  };
}

export interface BankingTransactionData {
  accountId: string;
  amount: string;
  recipientId: string;
  description: string;
  selectedAccount?: Account;
  deviceId?: string;
  ipAddress?: string;
  authMethod?: string;
  senderBalanceBefore?: number | null;
  senderBalanceAfter?: number | null;
  expectedIsFraud?: boolean | null;
  scenarioName?: string;
  currency?: string;
}

interface TransferData {
  amount: string;
  recipientId: string;
  description: string;
  deviceId: string;
  ipAddress: string;
  authMethod: string;
  senderBalanceBefore: number | null;
  senderBalanceAfter: number | null;
  expectedIsFraud: boolean | null;
  scenarioName: string;
  currency: string;
}

interface BankingAppProps {
  scenarios?: Scenario[];
  onTransactionSubmit?: (data: BankingTransactionData) => void;
  onReset?: () => void;
  fraudResult?: FraudResult | null;
  currentPhase?: string;
  onBiometricDone?: () => void;
}

const emptyTransfer: TransferData = {
  amount: '',
  recipientId: '',
  description: '',
  deviceId: '',
  ipAddress: '',
  authMethod: 'SMART_OTP',
  senderBalanceBefore: null,
  senderBalanceAfter: null,
  expectedIsFraud: null,
  scenarioName: '',
  currency: 'VND',
};

const formatAmount = (value: number | string | null | undefined, currency = 'VND') => {
  const amount = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(amount)) return `0 ${currency}`;
  return `${Number(amount).toLocaleString('en-US', { maximumFractionDigits: 2 })} ${currency}`;
};

const riskClasses: Record<string, string> = {
  low: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  medium: 'border-amber-200 bg-amber-50 text-amber-700',
  high: 'border-red-200 bg-red-50 text-red-700',
  critical: 'border-red-300 bg-red-100 text-red-800',
};

const statusCopy: Record<string, string> = {
  approved: 'Transaction approved',
  blocked: 'Transaction blocked',
  escalate: 'Verification required',
  pending: 'Processing transfer',
};

export function BankingApp({
  scenarios = [],
  onTransactionSubmit,
  onReset,
  fraudResult,
  currentPhase,
  onBiometricDone,
}: BankingAppProps) {
  const [step, setStep] = useState<BankingStep>('login');
  const [credentials, setCredentials] = useState({ username: '', password: '' });
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>('');
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [transferData, setTransferData] = useState<TransferData>(emptyTransfer);
  const [needsBiometric, setNeedsBiometric] = useState(false);
  const [biometricVerified, setBiometricVerified] = useState(false);
  const [otpCode, setOtpCode] = useState('');

  const quickScenarios = useMemo(() => scenarios.slice(0, 4), [scenarios]);
  const selectedScenario = useMemo(
    () => scenarios.find((scenario) => String(scenario.id) === selectedScenarioId),
    [scenarios, selectedScenarioId],
  );

  const applyScenario = (scenario: Scenario) => {
    const tx = scenario.transaction;
    const balance = tx.sender_balance_before ?? selectedAccount?.balance ?? 0;
    const riskCategory = tx.expected_is_fraud ? 'critical' : tx.amount >= 1_000_000 ? 'medium' : 'low';

    setSelectedScenarioId(String(scenario.id));
    setSelectedAccount({
      id: tx.sender_id,
      name: tx.sender_name || tx.sender_id,
      balance,
    });
    setUserInfo((prev) => ({
      id: tx.sender_id,
      name: tx.sender_name || prev?.name || tx.sender_id,
      kyc_status: prev?.kyc_status || 'verified',
      risk_category: riskCategory,
      fraud_ratio: tx.expected_is_fraud ? 1 : 0,
    }));
    setTransferData({
      amount: String(tx.amount),
      recipientId: tx.receiver_id,
      description: tx.description || scenario.description || '',
      deviceId: tx.device_id || '',
      ipAddress: tx.ip_address || '',
      authMethod: tx.auth_method || 'SMART_OTP',
      senderBalanceBefore: tx.sender_balance_before ?? null,
      senderBalanceAfter: tx.sender_balance_after ?? null,
      expectedIsFraud: tx.expected_is_fraud ?? null,
      scenarioName: scenario.name,
      currency: tx.currency || 'VND',
    });
    setCredentials((prev) => ({ username: tx.sender_id, password: prev.password || 'demo' }));
  };

  const handleScenarioSelect = (value: string) => {
    const scenario = scenarios.find((item) => String(item.id) === value);
    if (scenario) applyScenario(scenario);
  };

  const handleLogin = async () => {
    if (!credentials.username || !credentials.password) return;

    setIsLoggingIn(true);
    setLoginError(null);

    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: credentials.username.trim(),
          password: credentials.password,
        }),
      });

      if (!res.ok) {
        const errorData = await res.json();
        setLoginError(errorData.detail || 'Login failed');
        return;
      }

      const data = await res.json();
      setUserInfo(data.user);

      if (data.accounts?.length > 0) {
        const acc = data.accounts[0];
        setSelectedAccount({
          id: acc.id,
          name: acc.name,
          balance: acc.balance,
        });
      } else {
        setSelectedAccount({
          id: credentials.username.trim(),
          name: credentials.username.trim(),
          balance: 0,
        });
      }

      const matchingScenario = scenarios.find((scenario) => scenario.transaction.sender_id === credentials.username.trim());
      if (matchingScenario) applyScenario(matchingScenario);

      setStep('transfer-details');
    } catch {
      setLoginError('Cannot connect to backend API.');
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleTransferSubmit = () => {
    const amount = Number(transferData.amount);
    if (selectedAccount && transferData.recipientId && Number.isFinite(amount) && amount > 0) {
      setStep('confirmation');
    }
  };

  const handleConfirm = () => {
    if (!selectedAccount || !onTransactionSubmit) return;

    onTransactionSubmit({
      accountId: selectedAccount.id,
      amount: transferData.amount,
      recipientId: transferData.recipientId,
      description: transferData.description,
      selectedAccount,
      deviceId: transferData.deviceId,
      ipAddress: transferData.ipAddress,
      authMethod: transferData.authMethod,
      senderBalanceBefore: transferData.senderBalanceBefore,
      senderBalanceAfter: transferData.senderBalanceAfter,
      expectedIsFraud: transferData.expectedIsFraud,
      scenarioName: transferData.scenarioName,
      currency: transferData.currency,
    });
    setStep('processing');
  };

  const handleOtpSubmit = () => {
    if (otpCode.length !== 6) return;
    setBiometricVerified(true);
    onBiometricDone?.();
  };

  const handleBackHome = () => {
    setStep('login');
    setCredentials({ username: '', password: '' });
    setSelectedScenarioId('');
    setSelectedAccount(null);
    setUserInfo(null);
    setTransferData(emptyTransfer);
    setNeedsBiometric(false);
    setBiometricVerified(false);
    setOtpCode('');
    setLoginError(null);
    onReset?.();
  };

  useEffect(() => {
    if (fraudResult?.status === 'escalate' && step === 'processing' && !biometricVerified) {
      setNeedsBiometric(true);
    }
  }, [fraudResult, step, biometricVerified]);

  const showBiometric = needsBiometric && !biometricVerified && fraudResult?.status === 'escalate';
  const resultReady = step === 'processing' && fraudResult && fraudResult.status !== 'escalate' && (!needsBiometric || biometricVerified);
  const riskClass = userInfo ? riskClasses[userInfo.risk_category] || riskClasses.low : riskClasses.low;

  return (
    <div className="flex h-full justify-center">
      <div className="flex h-[720px] w-full max-w-[350px] flex-col overflow-hidden rounded-[2rem] border-[10px] border-slate-900 bg-slate-100 shadow-2xl shadow-black/40">
        <div className="flex h-12 items-center justify-between bg-slate-950 px-5 text-[11px] font-semibold text-white">
          <span>9:41</span>
          <span className="flex items-center gap-1.5">
            <Landmark className="h-3.5 w-3.5 text-cyan-300" />
            SecureBank
          </span>
          <span className="text-cyan-200">5G</span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50 p-4 text-slate-950">
          {step === 'login' && (
            <div className="space-y-5 pt-4">
              <div className="space-y-2">
                <div className="flex h-11 w-11 items-center justify-center rounded-md bg-slate-950 text-cyan-300">
                  <ShieldCheck className="h-6 w-6" />
                </div>
                <div>
                  <h2 className="text-2xl font-semibold tracking-tight">Secure login</h2>
                  <p className="text-xs text-slate-500">Use a real sender ID from the dataset.</p>
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-xs font-semibold text-slate-700">Account ID</label>
                <Input
                  placeholder={quickScenarios[0]?.transaction.sender_id || 'Dataset account ID'}
                  value={credentials.username}
                  onChange={(event) => setCredentials({ ...credentials, username: event.target.value })}
                  className="h-10 border-slate-300 bg-white text-sm"
                />

                <label className="block text-xs font-semibold text-slate-700">Password</label>
                <Input
                  type="password"
                  placeholder="demo"
                  value={credentials.password}
                  onChange={(event) => setCredentials({ ...credentials, password: event.target.value })}
                  onKeyDown={(event) => event.key === 'Enter' && handleLogin()}
                  className="h-10 border-slate-300 bg-white text-sm"
                />
              </div>

              {quickScenarios.length > 0 && (
                <div className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Dataset shortcuts</div>
                  <div className="grid gap-2">
                    {quickScenarios.map((scenario) => (
                      <button
                        key={scenario.id}
                        type="button"
                        onClick={() => applyScenario(scenario)}
                        className="rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-xs shadow-sm transition hover:border-cyan-300 hover:bg-cyan-50"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold text-slate-900">{scenario.name}</span>
                          <span className={scenario.expected_decision === 'block' ? 'font-semibold text-red-600' : 'font-semibold text-emerald-600'}>
                            {scenario.expected_decision || 'review'}
                          </span>
                        </div>
                        <div className="mt-1 truncate font-mono text-[11px] text-slate-500">{scenario.transaction.sender_id}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {loginError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs font-medium text-red-700">
                  {loginError}
                </div>
              )}

              <Button
                onClick={handleLogin}
                disabled={isLoggingIn || !credentials.username || !credentials.password}
                className="h-10 w-full bg-slate-950 text-white hover:bg-slate-800"
              >
                {isLoggingIn ? <Loader2 className="h-4 w-4 animate-spin" /> : <LockKeyhole className="h-4 w-4" />}
                Sign in
              </Button>
            </div>
          )}

          {step === 'transfer-details' && (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-xl font-semibold tracking-tight">New transfer</h2>
                  <p className="text-xs text-slate-500">Scenario-backed banking simulator.</p>
                </div>
                <button
                  type="button"
                  onClick={handleBackHome}
                  className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-100"
                  aria-label="Reset banking simulator"
                >
                  <RotateCcw className="h-4 w-4" />
                </button>
              </div>

              <Card className="gap-0 rounded-md border-slate-200 bg-white p-0 shadow-sm">
                <div className="rounded-t-md bg-slate-950 px-4 py-3 text-white">
                  <div className="flex items-center justify-between text-xs text-slate-300">
                    <span>Primary balance</span>
                    <WalletCards className="h-4 w-4 text-cyan-300" />
                  </div>
                  <div className="mt-2 text-2xl font-semibold">{formatAmount(selectedAccount?.balance, transferData.currency)}</div>
                </div>
                <div className="space-y-3 p-4">
                  <div className="flex items-start gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-md bg-cyan-50 text-cyan-700">
                      <UserRound className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold">{selectedAccount?.name || userInfo?.name || 'Dataset account'}</div>
                      <div className="break-all font-mono text-[11px] text-slate-500">{selectedAccount?.id || userInfo?.id}</div>
                    </div>
                  </div>
                  {userInfo && (
                    <div className="flex flex-wrap gap-2 text-[11px]">
                      <span className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 font-medium text-emerald-700">
                        <BadgeCheck className="h-3 w-3" />
                        KYC {userInfo.kyc_status}
                      </span>
                      <span className={`inline-flex items-center rounded-md border px-2 py-1 font-medium ${riskClass}`}>
                        Risk {userInfo.risk_category}
                      </span>
                    </div>
                  )}
                </div>
              </Card>

              {scenarios.length > 0 && (
                <div className="space-y-2">
                  <label className="text-xs font-semibold text-slate-700">Transaction scenario</label>
                  <select
                    value={selectedScenarioId}
                    onChange={(event) => handleScenarioSelect(event.target.value)}
                    className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm shadow-sm outline-none focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                  >
                    <option value="">Manual transaction</option>
                    {scenarios.map((scenario) => (
                      <option key={scenario.id} value={scenario.id}>
                        {scenario.name} - {scenario.transaction.sender_id}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="grid gap-3">
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-slate-700">Recipient ID</label>
                  <Input
                    placeholder="C1409103719"
                    value={transferData.recipientId}
                    onChange={(event) => setTransferData({ ...transferData, recipientId: event.target.value })}
                    className="h-10 border-slate-300 bg-white font-mono text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-slate-700">Amount</label>
                  <Input
                    type="number"
                    placeholder="144.88"
                    value={transferData.amount}
                    onChange={(event) => setTransferData({ ...transferData, amount: event.target.value })}
                    className="h-10 border-slate-300 bg-white text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-slate-700">Description</label>
                  <Input
                    placeholder="Dataset transfer"
                    value={transferData.description}
                    onChange={(event) => setTransferData({ ...transferData, description: event.target.value })}
                    className="h-10 border-slate-300 bg-white text-sm"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-600">
                <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
                  <div className="mb-1 flex items-center gap-1 font-semibold text-slate-800">
                    <Smartphone className="h-3.5 w-3.5 text-cyan-700" />
                    Device
                  </div>
                  <div className="truncate font-mono">{transferData.deviceId || 'dataset/default'}</div>
                </div>
                <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
                  <div className="mb-1 flex items-center gap-1 font-semibold text-slate-800">
                    <Fingerprint className="h-3.5 w-3.5 text-cyan-700" />
                    Auth
                  </div>
                  <div className="truncate">{transferData.authMethod || 'SMART_OTP'}</div>
                </div>
              </div>

              {selectedScenario && (
                <div className="rounded-md border border-slate-200 bg-slate-100 px-3 py-2 text-[11px] text-slate-600">
                  Expected demo outcome:{' '}
                  <span className={selectedScenario.expected_decision === 'block' ? 'font-semibold text-red-600' : 'font-semibold text-emerald-600'}>
                    {selectedScenario.expected_decision?.toUpperCase()}
                  </span>
                </div>
              )}

              <Button onClick={handleTransferSubmit} className="h-10 w-full bg-cyan-700 text-white hover:bg-cyan-800">
                <Send className="h-4 w-4" />
                Review transfer
              </Button>
            </div>
          )}

          {step === 'confirmation' && (
            <div className="space-y-4">
              <div>
                <h2 className="text-xl font-semibold tracking-tight">Review transfer</h2>
                <p className="text-xs text-slate-500">Confirm before fraud screening starts.</p>
              </div>

              <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
                <div className="grid gap-4 text-sm">
                  <div>
                    <div className="text-xs font-medium text-slate-500">From</div>
                    <div className="mt-1 break-all font-mono font-semibold text-slate-950">{selectedAccount?.id}</div>
                  </div>
                  <div>
                    <div className="text-xs font-medium text-slate-500">To</div>
                    <div className="mt-1 break-all font-mono font-semibold text-slate-950">{transferData.recipientId}</div>
                  </div>
                  <div className="rounded-md bg-slate-950 px-3 py-3 text-white">
                    <div className="text-xs text-slate-300">Amount</div>
                    <div className="mt-1 text-2xl font-semibold">{formatAmount(transferData.amount, transferData.currency)}</div>
                  </div>
                  {transferData.scenarioName && (
                    <div>
                      <div className="text-xs font-medium text-slate-500">Scenario</div>
                      <div className="mt-1 text-sm font-semibold">{transferData.scenarioName}</div>
                    </div>
                  )}
                </div>
              </div>

              <Button onClick={handleConfirm} className="h-10 w-full bg-emerald-700 text-white hover:bg-emerald-800">
                <ShieldCheck className="h-4 w-4" />
                Confirm and screen
              </Button>
              <Button
                onClick={() => setStep('transfer-details')}
                variant="outline"
                className="h-10 w-full border-slate-300 bg-white text-slate-800 hover:bg-slate-100"
              >
                Edit details
              </Button>
            </div>
          )}

          {step === 'processing' && !showBiometric && !resultReady && (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <Loader2 className="h-12 w-12 animate-spin text-cyan-700" />
              <div>
                <h2 className="text-xl font-semibold">Screening transfer</h2>
                <p className="mt-1 text-xs text-slate-500">
                  {formatAmount(transferData.amount, transferData.currency)} to {transferData.recipientId}
                </p>
              </div>
              <div className="rounded-md border border-cyan-200 bg-cyan-50 px-3 py-2 text-xs font-medium text-cyan-800">
                {currentPhase || 'Starting pipeline'}
              </div>
            </div>
          )}

          {step === 'processing' && showBiometric && (
            <div className="space-y-5 pt-6 text-center">
              <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-md bg-amber-100 text-amber-700">
                <LockKeyhole className="h-8 w-8" />
              </div>
              <div>
                <h2 className="text-xl font-semibold">Verify identity</h2>
                <p className="mt-1 text-xs text-slate-500">The investigation needs one more customer signal.</p>
              </div>
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-3 text-xs font-medium text-amber-800">
                Enter any six-digit OTP to release the final decision.
              </div>
              <Input
                placeholder="000000"
                maxLength={6}
                value={otpCode}
                onChange={(event) => setOtpCode(event.target.value.replace(/\D/g, ''))}
                onKeyDown={(event) => event.key === 'Enter' && handleOtpSubmit()}
                className="h-11 border-slate-300 bg-white text-center font-mono text-lg tracking-[0.3em]"
              />
              <Button
                onClick={handleOtpSubmit}
                disabled={otpCode.length !== 6}
                className="h-10 w-full bg-slate-950 text-white hover:bg-slate-800"
              >
                <Fingerprint className="h-4 w-4" />
                Verify OTP
              </Button>
            </div>
          )}

          {step === 'processing' && biometricVerified && fraudResult?.status === 'escalate' && (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <CheckCircle2 className="h-12 w-12 text-emerald-600" />
              <div>
                <h2 className="text-xl font-semibold">Identity verified</h2>
                <p className="mt-1 text-xs text-slate-500">Waiting for the detective decision.</p>
              </div>
              <Loader2 className="h-7 w-7 animate-spin text-cyan-700" />
            </div>
          )}

          {resultReady && fraudResult && (
            <div className="space-y-5 pt-6 text-center">
              <div
                className={`mx-auto flex h-16 w-16 items-center justify-center rounded-md ${
                  fraudResult.status === 'approved' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'
                }`}
              >
                {fraudResult.status === 'approved' ? <CheckCircle2 className="h-9 w-9" /> : <XCircle className="h-9 w-9" />}
              </div>
              <div>
                <h2 className={`text-2xl font-semibold ${fraudResult.status === 'approved' ? 'text-emerald-700' : 'text-red-700'}`}>
                  {statusCopy[fraudResult.status]}
                </h2>
                <p className="mt-1 text-xs text-slate-500">Confidence {fraudResult.score}%</p>
              </div>
              <div
                className={`rounded-md border px-3 py-3 text-xs font-medium ${
                  fraudResult.status === 'approved'
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                    : 'border-red-200 bg-red-50 text-red-800'
                }`}
              >
                {fraudResult.status === 'approved'
                  ? `${formatAmount(transferData.amount, transferData.currency)} transferred to ${transferData.recipientId}`
                  : fraudResult.message || 'Fraud controls blocked this transfer.'}
              </div>
              {fraudResult.status === 'blocked' && (
                <div className="flex items-start gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-xs text-slate-600">
                  <AlertTriangle className="mt-0.5 h-4 w-4 flex-none text-red-600" />
                  <span>Review the investigation console for rules, evidence, and recommended actions.</span>
                </div>
              )}
              <Button onClick={handleBackHome} className="h-10 w-full bg-slate-950 text-white hover:bg-slate-800">
                Back to login
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
