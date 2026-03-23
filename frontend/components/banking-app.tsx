'use client';

import { useState, useEffect } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { Lock, CheckCircle2, XCircle } from 'lucide-react';

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
}

interface FraudResult {
  status: 'pending' | 'approved' | 'blocked' | 'escalate';
  score: number;
  decision?: string;
  message?: string;
}

interface BankingAppProps {
  onTransactionSubmit?: (data: {
    accountId: string;
    amount: string;
    recipientId: string;
    description: string;
  }) => void;
  onReset?: () => void;
  fraudResult?: FraudResult | null;
  currentPhase?: string;
  onBiometricDone?: () => void;
}

export function BankingApp({ onTransactionSubmit, onReset, fraudResult, currentPhase, onBiometricDone }: BankingAppProps) {
  const [step, setStep] = useState<BankingStep>('login');
  const [credentials, setCredentials] = useState({ username: '', password: '' });
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [transferData, setTransferData] = useState({
    amount: '',
    recipientId: '',
    description: '',
  });
  const [biometricVerified, setBiometricVerified] = useState(false);
  const [needsBiometric, setNeedsBiometric] = useState(false);
  const [otpCode, setOtpCode] = useState('');
  const [otpVerified, setOtpVerified] = useState(false);

  const handleLogin = async () => {
    if (!credentials.username || !credentials.password) return;

    setIsLoggingIn(true);
    setLoginError(null);

    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: credentials.username,
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

      if (data.accounts && data.accounts.length > 0) {
        const acc = data.accounts[0];
        setSelectedAccount({
          id: acc.id,
          name: acc.name,
          balance: acc.balance,
        });
      }

      setStep('transfer-details');
    } catch {
      setLoginError('Cannot connect to server. Make sure the backend is running.');
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleTransferSubmit = () => {
    if (transferData.amount && transferData.recipientId) {
      setStep('confirmation');
    }
  };

  const handleConfirm = () => {
    if (selectedAccount && onTransactionSubmit) {
      onTransactionSubmit({
        accountId: selectedAccount.id,
        amount: transferData.amount,
        recipientId: transferData.recipientId,
        description: transferData.description,
      });
    }
    setStep('processing');
  };

  const handleOtpSubmit = () => {
    if (otpCode.length === 6) {
      setOtpVerified(true);
      setBiometricVerified(true);
      onBiometricDone?.();
    }
  };

  const handleBackHome = () => {
    setStep('login');
    setCredentials({ username: '', password: '' });
    setTransferData({ amount: '', recipientId: '', description: '' });
    setBiometricVerified(false);
    setNeedsBiometric(false);
    setOtpCode('');
    setOtpVerified(false);
    setUserInfo(null);
    setLoginError(null);
    setSelectedAccount(null);
    if (onReset) onReset();
  };

  // Trigger biometric/OTP when fraud result is escalate
  useEffect(() => {
    if (fraudResult && fraudResult.status === 'escalate' && step === 'processing' && !biometricVerified && !otpVerified) {
      setNeedsBiometric(true);
    }
  }, [fraudResult, step, biometricVerified, otpVerified]);

  // Derive whether we should show biometric screen (avoids blank frame)
  const showBiometric = needsBiometric || (fraudResult?.status === 'escalate' && step === 'processing' && !biometricVerified && !otpVerified);

  const kycBadge = (status: string) => {
    if (status === 'verified') return <span className="text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full font-medium">Verified</span>;
    if (status === 'pending') return <span className="text-[10px] bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded-full font-medium">Pending</span>;
    return <span className="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded-full font-medium">{status}</span>;
  };

  const riskBadge = (category: string) => {
    const colors: Record<string, string> = {
      low: 'bg-green-100 text-green-700',
      medium: 'bg-yellow-100 text-yellow-700',
      high: 'bg-red-100 text-red-700',
      critical: 'bg-red-200 text-red-800',
    };
    return <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${colors[category] || 'bg-gray-100 text-gray-600'}`}>{category}</span>;
  };

  return (
    <div className="flex h-full flex-col items-center justify-start p-4">
      {/* Phone Frame */}
      <div className="mx-auto rounded-3xl border-8 border-gray-900 bg-white shadow-2xl overflow-hidden flex flex-col" style={{ width: '320px', maxHeight: '650px' }}>
        {/* Phone Status Bar */}
        <div className="bg-gradient-to-r from-blue-900 to-blue-800 text-white px-4 py-3 text-center text-xs font-semibold">
          <div className="flex items-center justify-between px-2">
            <span>9:41</span>
            <span>SecureBank</span>
            <span>■ ▌ ▌</span>
          </div>
        </div>

        {/* Phone Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {/* Login Screen */}
          {step === 'login' && (
            <div className="space-y-5 pt-8">
              <div className="text-center mb-8">
                <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-blue-700 rounded-full mx-auto mb-3 flex items-center justify-center">
                  <span className="text-white font-bold text-lg">$</span>
                </div>
                <h3 className="text-xl font-bold text-gray-900">Welcome Back</h3>
                <p className="text-xs text-gray-500 mt-1">Secure Banking at Your Fingertips</p>
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-700 block mb-2">Username</label>
                <Input
                  placeholder="Enter account ID"
                  value={credentials.username}
                  onChange={(e) => setCredentials({ ...credentials, username: e.target.value })}
                  className="text-sm border-gray-300 focus:border-blue-500 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-700 block mb-2">Password</label>
                <Input
                  placeholder="Enter password"
                  type="password"
                  value={credentials.password}
                  onChange={(e) => setCredentials({ ...credentials, password: e.target.value })}
                  className="text-sm border-gray-300 focus:border-blue-500 focus:ring-blue-500"
                />
              </div>
              {loginError && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-2 text-xs text-red-700 font-medium">
                  {loginError}
                </div>
              )}
              <Button
                onClick={handleLogin}
                disabled={isLoggingIn}
                className="w-full bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-700 hover:to-blue-800 text-white text-sm font-semibold py-2 rounded-lg disabled:opacity-50"
              >
                {isLoggingIn ? 'Signing in...' : 'Sign In'}
              </Button>
              <p className="text-center text-xs text-gray-500">IDs: ACC_001, ACC_002, ACC_007, ACC_050, ACC_666, MULE_001-003</p>
            </div>
          )}

          {/* Transfer Details Screen */}
          {step === 'transfer-details' && (
            <div className="space-y-4 pt-2">
              <div className="mb-4">
                <h3 className="text-xl font-bold text-gray-900">New Transfer</h3>
                {selectedAccount && (
                  <Card className="mt-3 p-3 bg-gradient-to-r from-blue-50 to-blue-100 border-blue-200">
                    <div className="text-xs text-gray-700">
                      <div className="text-gray-600 mb-1">From Account</div>
                      <div className="font-semibold text-gray-900">{selectedAccount.name}</div>
                      <div className="text-blue-600 font-bold mt-1">${selectedAccount.balance.toLocaleString()}</div>
                    </div>
                  </Card>
                )}
                {userInfo && (
                  <Card className="mt-2 p-2.5 bg-slate-50 border-slate-200">
                    <div className="flex items-center justify-between text-xs">
                      <div>
                        <div className="font-semibold text-gray-900">{userInfo.name}</div>
                        <div className="text-gray-500 text-[10px]">{userInfo.id}</div>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <div className="flex items-center gap-1">
                          <span className="text-[10px] text-gray-500">KYC:</span>
                          {kycBadge(userInfo.kyc_status)}
                        </div>
                        <div className="flex items-center gap-1">
                          <span className="text-[10px] text-gray-500">Risk:</span>
                          {riskBadge(userInfo.risk_category)}
                        </div>
                      </div>
                    </div>
                  </Card>
                )}
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-700 block mb-2">Recipient ID</label>
                <Input
                  placeholder="e.g., ACC_002"
                  value={transferData.recipientId}
                  onChange={(e) => setTransferData({ ...transferData, recipientId: e.target.value })}
                  className="text-sm"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-700 block mb-2">Amount (USD)</label>
                <div className="relative">
                  <span className="absolute left-3 top-2.5 text-gray-600 font-semibold">$</span>
                  <Input
                    placeholder="0.00"
                    type="number"
                    value={transferData.amount}
                    onChange={(e) => setTransferData({ ...transferData, amount: e.target.value })}
                    className="text-sm pl-7"
                  />
                </div>
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-700 block mb-2">Description</label>
                <Input
                  placeholder="Add a note..."
                  value={transferData.description}
                  onChange={(e) => setTransferData({ ...transferData, description: e.target.value })}
                  className="text-sm"
                />
              </div>
              <Button onClick={handleTransferSubmit} className="w-full bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-700 hover:to-blue-800 text-white text-sm font-semibold py-2 rounded-lg mt-2">
                Continue
              </Button>
            </div>
          )}

          {/* Confirmation Screen */}
          {step === 'confirmation' && (
            <div className="space-y-4 pt-2">
              <h3 className="text-xl font-bold text-gray-900 mb-4">Review & Confirm</h3>
              <Card className="p-4 bg-gradient-to-br from-slate-50 to-slate-100 border-slate-200 rounded-lg">
                <div className="space-y-3 text-sm">
                  <div>
                    <div className="text-xs text-gray-600 mb-1">From</div>
                    <div className="font-semibold text-gray-900">{selectedAccount?.name}</div>
                  </div>
                  <div className="border-t border-slate-300 pt-3">
                    <div className="text-xs text-gray-600 mb-1">To</div>
                    <div className="font-semibold text-gray-900">{transferData.recipientId}</div>
                  </div>
                  <div className="border-t border-slate-300 pt-3">
                    <div className="text-xs text-gray-600 mb-1">Amount</div>
                    <div className="text-2xl font-bold text-blue-600">${Number(transferData.amount).toLocaleString()}</div>
                  </div>
                </div>
              </Card>
              <Button onClick={handleConfirm} className="w-full bg-gradient-to-r from-green-600 to-green-700 hover:from-green-700 hover:to-green-800 text-white text-sm font-semibold py-2 rounded-lg">
                Confirm & Send
              </Button>
              <Button
                onClick={() => setStep('transfer-details')}
                className="w-full bg-gray-100 hover:bg-gray-200 text-gray-800 text-sm font-semibold py-2 rounded-lg"
              >
                Cancel
              </Button>
            </div>
          )}

          {/* Processing Screen - waiting for result */}
          {step === 'processing' && !showBiometric && !fraudResult && (
            <div className="space-y-5 text-center pt-12">
              <div className="flex justify-center">
                <div className="animate-spin">
                  <div className="w-12 h-12 border-4 border-blue-200 border-t-blue-600 rounded-full"></div>
                </div>
              </div>
              <div>
                <h3 className="text-lg font-bold text-gray-900 mb-2">Processing Transfer</h3>
                <p className="text-xs text-gray-600 mb-3">
                  ${Number(transferData.amount).toLocaleString()} to {transferData.recipientId}
                </p>
                <div className="inline-block bg-blue-50 border border-blue-200 px-3 py-1 rounded-full">
                  <p className="text-xs text-blue-700 font-medium">
                    {currentPhase || 'Starting pipeline...'}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Biometric Authentication Screen */}
          {step === 'processing' && showBiometric && !otpVerified && (
            <div className="space-y-5 text-center pt-8">
              <div className="mb-6">
                <div className="w-16 h-16 bg-yellow-100 rounded-full mx-auto mb-4 flex items-center justify-center">
                  <Lock className="w-8 h-8 text-yellow-600" />
                </div>
                <h3 className="text-lg font-bold text-gray-900 mb-2">Security Verification</h3>
                <p className="text-xs text-gray-600">Additional verification required</p>
              </div>

              <Card className="p-4 bg-yellow-50 border-yellow-200">
                <p className="text-xs text-yellow-800 font-medium">
                  Suspicious activity detected. Please verify your identity to continue.
                </p>
              </Card>

              <div className="space-y-3 pt-4">
                <div className="relative">
                  <p className="text-xs text-gray-600 mb-2">Enter OTP code sent to your phone</p>
                  <Input
                    placeholder="000000"
                    type="text"
                    maxLength={6}
                    value={otpCode}
                    onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ''))}
                    onKeyDown={(e) => e.key === 'Enter' && handleOtpSubmit()}
                    className="text-center text-sm font-mono tracking-widest"
                  />
                </div>
                <Button
                  onClick={handleOtpSubmit}
                  disabled={otpCode.length !== 6}
                  className="w-full bg-gray-300 text-gray-600 text-sm font-semibold py-2 rounded-lg disabled:opacity-50"
                >
                  Verify OTP
                </Button>
              </div>
            </div>
          )}

          {/* OTP Verified - Waiting for Pipeline to finish */}
          {step === 'processing' && showBiometric && biometricVerified && fraudResult?.status === 'escalate' && (
            <div className="space-y-5 text-center pt-12">
              <div className="flex justify-center mb-4">
                <CheckCircle2 className="w-12 h-12 text-green-500" />
              </div>
              <div>
                <h3 className="text-lg font-bold text-gray-900 mb-2">Identity Verified</h3>
                <p className="text-xs text-gray-600 mb-3">Waiting for pipeline to complete...</p>
                <div className="flex justify-center mb-3">
                  <div className="animate-spin">
                    <div className="w-8 h-8 border-3 border-blue-200 border-t-blue-600 rounded-full"></div>
                  </div>
                </div>
                <div className="inline-block bg-blue-50 border border-blue-200 px-3 py-1 rounded-full">
                  <p className="text-xs text-blue-700 font-medium">
                    {currentPhase || 'Processing...'}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Result Screen — APPROVED (only show if biometric not required, or already verified) */}
          {step === 'processing' && fraudResult && fraudResult.status === 'approved' && (!needsBiometric || biometricVerified) && (
            <div className="space-y-5 text-center pt-8">
              <div className="w-16 h-16 bg-green-100 rounded-full mx-auto mb-4 flex items-center justify-center">
                <CheckCircle2 className="w-8 h-8 text-green-600" />
              </div>
              <h3 className="text-2xl font-bold text-green-600">Transaction Approved</h3>
              <p className="text-xs text-gray-600">Confidence: <span className="font-bold">{fraudResult.score}%</span></p>
              <Card className="p-3 bg-green-50 border-green-200">
                <p className="text-xs font-medium text-green-800">
                  ${Number(transferData.amount).toLocaleString()} has been transferred to {transferData.recipientId}
                </p>
              </Card>
              {fraudResult.message && (
                <p className="text-xs text-gray-500 italic">{fraudResult.message}</p>
              )}
              <Button onClick={handleBackHome} className="w-full bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-700 hover:to-blue-800 text-white text-sm font-semibold py-2 rounded-lg mt-4">
                Back to Home
              </Button>
            </div>
          )}

          {/* Result Screen — BLOCKED (only show if biometric not required, or already verified) */}
          {step === 'processing' && fraudResult && fraudResult.status === 'blocked' && (!needsBiometric || biometricVerified) && (
            <div className="space-y-5 text-center pt-8">
              <div className="w-16 h-16 bg-red-100 rounded-full mx-auto mb-4 flex items-center justify-center">
                <XCircle className="w-8 h-8 text-red-600" />
              </div>
              <h3 className="text-2xl font-bold text-red-600">Transaction Blocked</h3>
              <p className="text-xs text-gray-600">Confidence: <span className="font-bold">{fraudResult.score}%</span></p>
              <Card className="p-3 bg-red-50 border-red-200">
                <p className="text-xs font-medium text-red-800">
                  Transaction declined — fraud detected.
                </p>
              </Card>
              {fraudResult.message && (
                <p className="text-xs text-gray-500 italic">{fraudResult.message}</p>
              )}
              <Button onClick={handleBackHome} className="w-full bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-700 hover:to-blue-800 text-white text-sm font-semibold py-2 rounded-lg mt-4">
                Back to Home
              </Button>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
