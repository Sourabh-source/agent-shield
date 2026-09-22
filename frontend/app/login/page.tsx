"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import {
  ShieldCheck,
  Mail,
  KeyRound,
  RotateCw,
  AlertCircle,
  CheckCircle2,
  ArrowRight,
  ArrowLeft,
} from "lucide-react";

export default function LoginPage() {
  const router = useRouter();

  // Step state: "email" | "otp"
  const [step, setStep] = useState<"email" | "otp">("email");

  // Form values
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");

  // Loading states
  const [isSending, setIsSending] = useState(false);
  const [isVerifying, setIsVerifying] = useState(false);
  const [isResending, setIsResending] = useState(false);

  // Validation & Error states
  const [emailError, setEmailError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [otpError, setOtpError] = useState<string | null>(null);
  const [resendMessage, setResendMessage] = useState<string | null>(null);

  // 60-second resend countdown
  const [resendTimer, setResendTimer] = useState(0);

  // References for auto-focusing
  const otpInputRef = useRef<HTMLInputElement>(null);

  // Countdown timer effect
  useEffect(() => {
    if (step !== "otp" || resendTimer <= 0) return;

    const interval = setInterval(() => {
      setResendTimer((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);

    return () => clearInterval(interval);
  }, [step, resendTimer]);

  // Focus OTP input when switching to OTP step
  useEffect(() => {
    if (step === "otp") {
      setTimeout(() => {
        otpInputRef.current?.focus();
      }, 100);
    }
  }, [step]);

  // Client-side email validation
  const validateEmail = (value: string): boolean => {
    const trimmed = value.trim();
    if (!trimmed) {
      setEmailError("Email address is required.");
      return false;
    }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(trimmed)) {
      setEmailError("Please enter a valid email address (e.g. name@company.com).");
      return false;
    }
    setEmailError(null);
    return true;
  };

  // Step 1: Send OTP handler
  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setApiError(null);
    setResendMessage(null);

    const trimmedEmail = email.trim();
    if (!validateEmail(trimmedEmail)) {
      return;
    }
    if (trimmedEmail !== email) {
      setEmail(trimmedEmail);
    }

    setIsSending(true);

    try {
      const { error } = await supabase.auth.signInWithOtp({
        email
      });

      if (error) {
        setApiError(error.message || "Failed to send verification code. Please try again.");
      } else {
        setStep("otp");
        setOtp("");
        setOtpError(null);
        setResendTimer(60);
        setResendMessage(`We sent a 6-digit verification code to ${email}`);
      }
    } catch (err: unknown) {
      setApiError(err instanceof Error ? err.message : "An unexpected error occurred.");
    } finally {
      setIsSending(false);
    }
  };

  // Step 2: Verify OTP handler
  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setOtpError(null);

    const trimmedOtp = otp.trim();
    if (trimmedOtp.length !== 6) {
      setOtpError("Please enter all 6 digits of the verification code.");
      return;
    }
    if (trimmedOtp !== otp) {
      setOtp(trimmedOtp);
    }

    setIsVerifying(true);

    try {
      const { data, error } = await supabase.auth.verifyOtp({
        email,
        token: otp,
        type: "email"
      });

      if (error) {
        setOtpError(
          error.message || "Invalid or expired verification code. Please check and try again."
        );
      } else if (data.session || data.user) {
        router.push("/");
      }
    } catch (err: unknown) {
      setOtpError(err instanceof Error ? err.message : "Verification failed. Please try again.");
    } finally {
      setIsVerifying(false);
    }
  };

  // Resend OTP handler
  const handleResendOtp = async () => {
    if (resendTimer > 0 || isResending) return;

    setIsResending(true);
    setOtpError(null);
    setResendMessage(null);

    try {
      const { error } = await supabase.auth.signInWithOtp({
        email
      });

      if (error) {
        setOtpError(error.message || "Failed to resend code. Please try again.");
      } else {
        setResendTimer(60);
        setResendMessage("A new 6-digit code has been sent to your email.");
      }
    } catch (err: unknown) {
      setOtpError(err instanceof Error ? err.message : "Failed to resend code.");
    } finally {
      setIsResending(false);
    }
  };

  // Reset to email step
  const handleBackToEmail = () => {
    setStep("email");
    setOtp("");
    setOtpError(null);
    setApiError(null);
    setResendMessage(null);
  };

  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-140px)] py-12 px-4 sm:px-6">
      <div className="w-full max-w-md">
        {/* Card Header & Brand */}
        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-lg bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400 mx-auto mb-3 shadow-lg shadow-blue-950/40">
            <ShieldCheck className="w-6 h-6 text-blue-400" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center justify-center gap-2">
            <span>AgentGuard Gateway</span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Zero-Trust Autonomous Software Verification Engine
          </p>
          <div className="mt-2 inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-800/80 border border-slate-700/60 text-[10px] font-mono text-slate-400 uppercase tracking-widest">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span>SECURE OTP AUTHENTICATION</span>
          </div>
        </div>

        {/* Auth Card Container */}
        <div className="rounded-lg border border-slate-800 bg-[#0e1422] p-6 sm:p-8 shadow-2xl relative overflow-hidden">
          {/* Subtle top accent bar */}
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-blue-500 via-sky-400 to-indigo-500" />

          {step === "email" ? (
            /* ================= STEP 1: EMAIL INPUT ================= */
            <form onSubmit={handleSendOtp} className="space-y-5">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label
                    htmlFor="email-input"
                    className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5 font-mono"
                  >
                    <Mail className="w-3.5 h-3.5 text-blue-400" />
                    <span>Email Identity</span>
                  </label>
                  <span className="text-[11px] text-slate-500 font-mono">Passwordless</span>
                </div>

                <div className="relative">
                  <input
                    id="email-input"
                    type="email"
                    required
                    autoFocus
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      if (emailError) setEmailError(null);
                      if (apiError) setApiError(null);
                    }}
                    placeholder="engineer@company.com"
                    className={`w-full bg-[#070b14] border ${
                      emailError ? "border-rose-500/80" : "border-slate-700/80"
                    } rounded-md px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-600 font-mono focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors`}
                  />
                </div>

                {emailError && (
                  <p className="mt-1.5 text-xs text-rose-400 flex items-center gap-1">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                    <span>{emailError}</span>
                  </p>
                )}
              </div>

              {/* API Error Banner */}
              {apiError && (
                <div className="p-3 rounded-md bg-rose-950/40 border border-rose-800/80 text-rose-300 text-xs flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                  <span>{apiError}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={isSending}
                className="w-full py-2.5 px-4 text-xs font-semibold uppercase tracking-wider text-white bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed rounded-md border border-blue-500/50 transition-colors flex items-center justify-center gap-2 shadow-sm cursor-pointer"
              >
                {isSending ? (
                  <>
                    <RotateCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Sending OTP...</span>
                  </>
                ) : (
                  <>
                    <span>Send OTP</span>
                    <ArrowRight className="w-3.5 h-3.5" />
                  </>
                )}
              </button>

              <div className="pt-2 border-t border-slate-800/80 text-center">
                <p className="text-[11px] text-slate-500 font-mono">
                  A 6-digit one-time passcode will be delivered to your inbox.
                </p>
              </div>
            </form>
          ) : (
            /* ================= STEP 2: 6-DIGIT OTP INPUT ================= */
            <form onSubmit={handleVerifyOtp} className="space-y-5">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label
                    htmlFor="otp-input"
                    className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5 font-mono"
                  >
                    <KeyRound className="w-3.5 h-3.5 text-blue-400" />
                    <span>Verification Code</span>
                  </label>
                  <span className="text-[11px] text-slate-500 font-mono">6 Digits</span>
                </div>

                <div className="relative">
                  <input
                    ref={otpInputRef}
                    id="otp-input"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    value={otp}
                    onChange={(e) => {
                      const val = e.target.value.replace(/\D/g, "").slice(0, 6);
                      setOtp(val);
                      if (otpError) setOtpError(null);
                    }}
                    placeholder="000000"
                    className={`w-full bg-[#070b14] border ${
                      otpError ? "border-rose-500/80" : "border-slate-700/80"
                    } rounded-md py-3 px-3 text-center text-2xl font-bold tracking-[0.4em] text-slate-100 placeholder-slate-700 font-mono focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors`}
                  />
                </div>
              </div>

              {/* Resend success notice */}
              {resendMessage && !otpError && (
                <div className="p-3 rounded-md bg-emerald-950/30 border border-emerald-800/60 text-emerald-300 text-xs flex items-start gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                  <span className="font-mono text-[11px]">{resendMessage}</span>
                </div>
              )}

              {/* OTP Error Banner */}
              {otpError && (
                <div className="p-3 rounded-md bg-rose-950/40 border border-rose-800/80 text-rose-300 text-xs flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                  <span>{otpError}</span>
                </div>
              )}

              {/* Verify & Login Button */}
              <button
                type="submit"
                disabled={otp.length !== 6 || isVerifying}
                className="w-full py-2.5 px-4 text-xs font-semibold uppercase tracking-wider text-white bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed rounded-md border border-blue-500/50 transition-colors flex items-center justify-center gap-2 shadow-sm cursor-pointer"
              >
                {isVerifying ? (
                  <>
                    <RotateCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Verifying Code...</span>
                  </>
                ) : (
                  <>
                    <KeyRound className="w-3.5 h-3.5" />
                    <span>Verify & Login</span>
                  </>
                )}
              </button>

              {/* Resend countdown & Email edit controls */}
              <div className="pt-2 border-t border-slate-800/80 flex flex-col items-center gap-2">
                <div className="text-xs text-slate-400 font-mono">
                  {resendTimer > 0 ? (
                    <span className="text-slate-500">
                      Resend code in <strong className="text-slate-300 font-mono">{resendTimer}s</strong>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={handleResendOtp}
                      disabled={isResending}
                      className="text-blue-400 hover:text-blue-300 hover:underline font-mono inline-flex items-center gap-1.5 transition-colors cursor-pointer"
                    >
                      {isResending ? (
                        <>
                          <RotateCw className="w-3 h-3 animate-spin" />
                          <span>Resending code...</span>
                        </>
                      ) : (
                        <span>Resend OTP</span>
                      )}
                    </button>
                  )}
                </div>

                <button
                  type="button"
                  onClick={handleBackToEmail}
                  className="text-[11px] text-slate-500 hover:text-slate-300 inline-flex items-center gap-1 transition-colors cursor-pointer mt-1"
                >
                  <ArrowLeft className="w-3 h-3" />
                  <span>Use a different email</span>
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Security Footer Notice */}
        <div className="text-center mt-6">
          <p className="text-[11px] text-slate-600 font-mono">
            Protected by AgentGuard Zero-Trust • Session tokens encrypted
          </p>
        </div>
      </div>
    </div>
  );
}
