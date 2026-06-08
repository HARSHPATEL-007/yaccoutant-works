"use client";

import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { AlertCircle, CheckCircle, Clock, FileText, Upload } from "lucide-react";
import { api } from "@/lib/api";

interface ReconciliationStats {
  total: number;
  matched: number;
  mismatched: number;
  pending: number;
  total_debit: string;
  total_credit: string;
}

export function ComplianceDashboard() {
  const { data: stats, isLoading } = useQuery<<ReconciliationStats>({
    queryKey: ["reconciliation"],
    queryFn: async () => {
      const res = await api.get("/ledger/reconciliation/demo-client?period=2024-06");
      return res.data.data;
    },
    refetchInterval: 30000,
  });

  if (isLoading) {
    return <div className="animate-pulse h-64 bg-gray-200 rounded-lg" />;
  }

  return (
    <div className="space-y-6">
      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard
          title="Total Entries"
          value={stats?.total ?? 0}
          icon={<FileText className="w-5 h-5 text-blue-600" />}
          color="bg-blue-50"
        />
        <StatCard
          title="Matched"
          value={stats?.matched ?? 0}
          icon={<CheckCircle className="w-5 h-5 text-green-600" />}
          color="bg-green-50"
        />
        <StatCard
          title="Mismatched"
          value={stats?.mismatched ?? 0}
          icon={<AlertCircle className="w-5 h-5 text-red-600" />}
          color="bg-red-50"
        />
        <StatCard
          title="Pending"
          value={stats?.pending ?? 0}
          icon={<Clock className="w-5 h-5 text-yellow-600" />}
          color="bg-yellow-50"
        />
      </div>

      {/* Quick Actions */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
        <div className="flex flex-wrap gap-3">
          <button className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition">
            <Upload className="w-4 h-4" />
            Upload Invoices
          </button>
          <button className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 transition">
            <FileText className="w-4 h-4" />
            Generate CMA Report
          </button>
          <button className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 transition">
            <CheckCircle className="w-4 h-4" />
            GST Reconciliation
          </button>
        </div>
      </div>

      {/* Alert Banner */}
      {stats && stats.mismatched > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 mt-0.5" />
          <div>
            <h3 className="font-semibold text-red-800">Reconciliation Issues Detected</h3>
            <p className="text-red-700 text-sm mt-1">
              {stats.mismatched} entries require manual review. 
              <a href="#" className="underline ml-1">View details →</a>
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ title, value, icon, color }: {
  title: string;
  value: number;
  icon: React.ReactNode;
  color: string;
}) {
  return (
    <div className={`${color} rounded-lg p-4`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-600">{title}</p>
          <p className="text-2xl font-bold mt-1">{value.toLocaleString()}</p>
        </div>
        {icon}
      </div>
    </div>
  );
}