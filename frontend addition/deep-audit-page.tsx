"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, Filter, Download, MessageSquare, Shield, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";

interface LedgerRow {
  id: string;
  account_code: string;
  transaction_date: string;
  debit: number;
  credit: number;
  description: string;
  gstin: string;
  hsn_code: string;
  reconciliation_status: string;
  anomaly_flag: boolean;
}

interface AuditLog {
  id: string;
  action: string;
  resource_type: string;
  timestamp: string;
  user_id: string;
  ip_address: string;
}

export default function DeepAuditConsole() {
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());

  const { data: ledgerData, isLoading } = useQuery<LedgerRow[]>({
    queryKey: ["deep-audit-ledger", statusFilter],
    queryFn: async () => {
      const res = await api.get(`/ledger/entries/demo-client?status=${statusFilter !== "all" ? statusFilter : ""}`);
      return res.data.data;
    },
  });

  const { data: auditLogs } = useQuery<AuditLog[]>({
    queryKey: ["audit-trail"],
    queryFn: async () => {
      const res = await api.get("/audit/logs?limit=50");
      return res.data.data;
    },
  });

  const toggleRow = (id: string) => {
    const next = new Set(selectedRows);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedRows(next);
  };

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-900">Deep-Audit Console</h1>
              <p className="text-gray-600 mt-1">Enterprise ledger review, ESG analysis, and AI-assisted audit</p>
            </div>
            <div className="flex gap-2">
              <button className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700">
                <MessageSquare className="w-4 h-4" />
                Chat with Data
              </button>
              <button className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200">
                <Download className="w-4 h-4" />
                Export
              </button>
            </div>
          </div>
        </header>

        {/* Filters */}
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <div className="flex flex-wrap gap-4 items-center">
            <div className="flex-1 min-w-[300px]">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search by description, GSTIN, HSN code..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Filter className="w-4 h-4 text-gray-500" />
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="all">All Status</option>
                <option value="pending">Pending</option>
                <option value="matched">Matched</option>
                <option value="mismatched">Mismatched</option>
              </select>
            </div>
          </div>
        </div>

        {/* Data Grid */}
        <div className="bg-white rounded-lg shadow overflow-hidden mb-8">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left">
                    <input
                      type="checkbox"
                      className="rounded border-gray-300"
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedRows(new Set(ledgerData?.map(r => r.id)));
                        } else {
                          setSelectedRows(new Set());
                        }
                      }}
                    />
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Date</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Account</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Description</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-700">Debit</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-700">Credit</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">GSTIN</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">HSN</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Status</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Risk</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {isLoading ? (
                  <tr><td colSpan={10} className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
                ) : ledgerData?.map((row) => (
                  <tr
                    key={row.id}
                    className={`hover:bg-gray-50 ${row.anomaly_flag ? "bg-red-50" : ""} ${selectedRows.has(row.id) ? "bg-blue-50" : ""}`}
                  >
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selectedRows.has(row.id)}
                        onChange={() => toggleRow(row.id)}
                        className="rounded border-gray-300"
                      />
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{row.transaction_date}</td>
                    <td className="px-4 py-3 font-mono text-xs">{row.account_code}</td>
                    <td className="px-4 py-3 max-w-xs truncate">{row.description}</td>
                    <td className="px-4 py-3 text-right font-mono">{row.debit > 0 ? `₹${row.debit.toFixed(2)}` : "-"}</td>
                    <td className="px-4 py-3 text-right font-mono">{row.credit > 0 ? `₹${row.credit.toFixed(2)}` : "-"}</td>
                    <td className="px-4 py-3 font-mono text-xs">{row.gstin || "-"}</td>
                    <td className="px-4 py-3 font-mono text-xs">{row.hsn_code || "-"}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${
                        row.reconciliation_status === "matched" ? "bg-green-100 text-green-700" :
                        row.reconciliation_status === "mismatched" ? "bg-red-100 text-red-700" :
                        "bg-yellow-100 text-yellow-700"
                      }`}>
                        {row.reconciliation_status === "matched" && <Shield className="w-3 h-3" />}
                        {row.reconciliation_status === "mismatched" && <AlertTriangle className="w-3 h-3" />}
                        {row.reconciliation_status}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {row.anomaly_flag ? (
                        <span className="inline-flex items-center gap-1 px-2 py-1 bg-red-100 text-red-700 rounded text-xs">
                          <AlertTriangle className="w-3 h-3" />
                          Flagged
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400">-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selectedRows.size > 0 && (
            <div className="px-4 py-3 bg-blue-50 border-t border-blue-100 flex items-center justify-between">
              <span className="text-sm text-blue-700">{selectedRows.size} rows selected</span>
              <div className="flex gap-2">
                <button className="px-3 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700">
                  Bulk Approve
                </button>
                <button className="px-3 py-1 bg-red-600 text-white rounded text-sm hover:bg-red-700">
                  Flag for Review
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Audit Trail */}
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-semibold mb-4">Immutable Audit Trail</h3>
          <div className="space-y-3">
            {auditLogs?.map((log) => (
              <div key={log.id} className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg text-sm">
                <div className="w-2 h-2 rounded-full bg-blue-500" />
                <div className="flex-1">
                  <span className="font-medium text-gray-700">{log.action}</span>
                  <span className="text-gray-500 mx-2">on</span>
                  <span className="font-medium text-gray-700">{log.resource_type}</span>
                </div>
                <div className="text-gray-500 text-xs">
                  {new Date(log.timestamp).toLocaleString()}
                </div>
                <div className="text-gray-500 text-xs font-mono">
                  {log.ip_address}
                </div>
              </div>
            )) || (
              <p className="text-gray-500 text-center py-4">No audit logs available</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
