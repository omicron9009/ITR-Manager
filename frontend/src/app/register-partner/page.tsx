"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function PartnerRegisterPage() {
  const router = useRouter();

  const [formData, setFormData] = useState({
    full_name: "",
    email: "",
    membership_number: "", // Specific to CAs
    firm_name: "",
    password: "",
  });

  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      // Assuming your backend has a specific endpoint for partners
      // If it uses the same endpoint with a role, adjust the body accordingly
      const response = await fetch(
        "http://127.0.0.1:8000/api/v1/partners/register",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(formData),
        },
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || "Registration failed. Please verify your credentials.",
        );
      }

      setIsSuccess(true);
      setTimeout(() => router.push("/login"), 2000);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  if (isSuccess) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
        <div className="max-w-md w-full text-center p-10 bg-white rounded-xl shadow-2xl border border-blue-100">
          <div className="mx-auto flex items-center justify-center h-12 w-12 rounded-full bg-blue-100 mb-4">
            <svg
              className="h-6 w-6 text-blue-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                d="5 13l4 4L19 7"
              />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-slate-900">
            Partner Account Created
          </h2>
          <p className="mt-2 text-slate-600">
            Welcome to the professional panel. Redirecting to login...
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-lg w-full space-y-8 p-10 bg-white rounded-2xl shadow-xl border border-slate-200">
        <div className="text-center">
          <span className="inline-block px-3 py-1 text-xs font-semibold tracking-wider text-blue-700 uppercase bg-blue-100 rounded-full">
            Professional Portal
          </span>
          <h2 className="mt-4 text-3xl font-extrabold text-slate-900">
            Partner Registration
          </h2>
          <p className="mt-2 text-sm text-slate-500">
            Register your CA Practice to manage client ITRs
          </p>
        </div>

        <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
          {error && (
            <div className="bg-red-50 border-l-4 border-red-500 p-4 text-red-700 text-sm animate-pulse">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-semibold text-slate-700">
                Full Name
              </label>
              <input
                name="full_name"
                type="text"
                required
                className="mt-1 block w-full px-4 py-2 bg-slate-50 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:bg-white transition-all outline-none"
                placeholder="CA. Aditi Sharma"
                onChange={handleChange}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-semibold text-slate-700">
                  Membership No.
                </label>
                <input
                  name="membership_number"
                  type="text"
                  required
                  className="mt-1 block w-full px-4 py-2 bg-slate-50 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:bg-white transition-all outline-none"
                  placeholder="123456"
                  onChange={handleChange}
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-slate-700">
                  Firm Name
                </label>
                <input
                  name="firm_name"
                  type="text"
                  required
                  className="mt-1 block w-full px-4 py-2 bg-slate-50 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:bg-white transition-all outline-none"
                  placeholder="Sharma & Associates"
                  onChange={handleChange}
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-semibold text-slate-700">
                Official Email
              </label>
              <input
                name="email"
                type="email"
                required
                className="mt-1 block w-full px-4 py-2 bg-slate-50 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:bg-white transition-all outline-none"
                placeholder="office@cafirm.com"
                onChange={handleChange}
              />
            </div>

            <div>
              <label className="block text-sm font-semibold text-slate-700">
                Password
              </label>
              <input
                name="password"
                type="password"
                required
                className="mt-1 block w-full px-4 py-2 bg-slate-50 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:bg-white transition-all outline-none"
                placeholder="••••••••"
                onChange={handleChange}
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className={`w-full flex justify-center py-3 px-4 border border-transparent text-sm font-bold rounded-lg text-white shadow-md ${
              isLoading
                ? "bg-blue-400 cursor-wait"
                : "bg-blue-600 hover:bg-blue-700 active:scale-[0.98]"
            } transition-all duration-150`}
          >
            {isLoading ? "Verifying Credentials..." : "Register as Partner"}
          </button>
        </form>

        <div className="pt-4 text-center border-t border-slate-100">
          <p className="text-sm text-slate-600">
            Already have a partner account?{" "}
            <Link
              href="/login"
              className="font-bold text-blue-600 hover:text-blue-800"
            >
              Sign in here
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
