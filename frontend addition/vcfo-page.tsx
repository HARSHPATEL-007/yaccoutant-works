"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  AreaChart, Area, BarChart, Bar
} from "recharts";
import { TrendingUp, Flame, DollarSign, Users, Calendar } from "lucide-react";
import { api } from "@/lib/api";
import { format, addMonths } from "date-fns";

interface CashFlowData {
  month: string;
  revenue: number;
  burn: number;
  runway: number;
  cash_balance: number;
}

interface ESOPGrant {
  id: string;
  employee_id: string;
  grant_date: string;
  vesting_schedule: string;
  fair_market_value: number;
  strike_price: number;
}

export default function VCFOBoard() {
  const [projectionMonths, setProjectionMonths] = useState(12);

  const { data: cashFlow } = useQuery<CashFlowData[]>({
    queryKey: ["cash-flow", projectionMonths],
    queryFn: async () => {
      const res = await api.get(`/vcfo/cash-flow?months=${projectionMonths}`);
      return res.data.data;
    },
    placeholderData: Array.from({ length: 12 }, (_, i) => ({
      month: format(addMonths(new Date(), i), "MMM yyyy"),
      revenue: 45 + Math.random() * 15,
      burn: 38 + Math.random() * 8,
      runway: 18 - i * 0.8,
      cash_balance: 500 - i * 25 + Math.random() * 10
    }))
  });

  const { data: esopGrants } = useQuery<ESOPGrant[]>({
    queryKey: ["esop-grants"],
    queryFn: async () => {
      const res = await api.get("/valuation/esop-grants");
      return res.data.data;
    },
  });

  const currentCash = cashFlow?.[0]?.cash_balance || 500;
  const monthlyBurn = cashFlow?.[0]?.burn || 40;
  const runwayMonths = Math.floor(currentCash / monthlyBurn);

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-900">Virtual CFO Dashboard</h1>
              <p className="text-gray-600 mt-1">Startup financial intelligence & runway projection</p>
            </div>
            <div className="flex items-center gap-2">
              <Calendar className="w-4 h-4 text-gray-500" />
              <select
                value={projectionMonths}
                onChange={(e) => setProjectionMonths(Number(e.target.value))}
                className="border border-gray-300 rounded-md px-3 py-1 text-sm"
              >
                <option value={6}>6 Months</option>
                <option value={12}>12 Months</option>
                <option value={24}>24 Months</option>
              </select>
            </div>
          </div>
        </header>

        {/* KPI Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <KPICard
            title="Cash Balance"
            value={`₹${currentCash.toFixed(1)} Cr`}
            trend="+2.3%"
            icon={<DollarSign className="w-5 h-5 text-green-600" />}
            color="bg-green-50"
          />
          <KPICard
            title="Monthly Burn"
            value={`₹${monthlyBurn.toFixed(1)} Cr`}
            trend="-5%"
            icon={<Flame className="w-5 h-5 text-red-600" />}
            color="bg-red-50"
          />
          <KPICard
            title="Runway"
            value={`${runwayMonths} months`}
            trend={runwayMonths < 6 ? "CRITICAL" : "Stable"}
            icon={<TrendingUp className="w-5 h-5 text-blue-600" />}
            color={runwayMonths < 6 ? "bg-orange-50" : "bg-blue-50"}
          />
          <KPICard
            title="ESOP Pool"
            value={`${esopGrants?.length || 12} grants`}
            trend="Active"
            icon={<Users className="w-5 h-5 text-purple-600" />}
            color="bg-purple-50"
          />
        </div>

        {/* Charts */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          <div className="bg-white rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold mb-4">Cash Flow Projection</h3>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={cashFlow}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Area type="monotone" dataKey="cash_balance" stroke="#2563eb" fill="#3b82f6" fillOpacity={0.2} name="Cash Balance (₹ Cr)" />
                <Area type="monotone" dataKey="runway" stroke="#16a34a" fill="#22c55e" fillOpacity={0.1} name="Runway (months)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-white rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold mb-4">Revenue vs Burn</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={cashFlow}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="revenue" fill="#22c55e" name="Revenue (₹ Cr)" />
                <Bar dataKey="burn" fill="#ef4444" name="Burn (₹ Cr)" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* ESOP Cap Table */}
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-semibold mb-4">ESOP Grant Tracking</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Grant ID</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Employee</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Grant Date</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Vesting</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Strike Price</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">FMV (409A)</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-700">Spread</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {esopGrants?.map((grant) => (
                  <tr key={grant.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-mono text-xs">{grant.id.slice(0, 8)}</td>
                    <td className="px-4 py-3">{grant.employee_id}</td>
                    <td className="px-4 py-3">{format(new Date(grant.grant_date), "dd MMM yyyy")}</td>
                    <td className="px-4 py-3">{grant.vesting_schedule}</td>
                    <td className="px-4 py-3">₹{grant.strike_price}</td>
                    <td className="px-4 py-3">₹{grant.fair_market_value}</td>
                    <td className="px-4 py-3 text-green-600">
                      ₹{(grant.fair_market_value - grant.strike_price).toFixed(2)}
                    </td>
                  </tr>
                )) || (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                      No ESOP grants found. Use the valuation engine to create 409A valuations.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function KPICard({ title, value, trend, icon, color }: {
  title: string;
  value: string;
  trend: string;
  icon: React.ReactNode;
  color: string;
}) {
  const isCritical = trend === "CRITICAL";
  return (
    <div className={`${color} rounded-lg p-4`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-gray-600">{title}</span>
        {icon}
      </div>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      <p className={`text-xs mt-1 font-medium ${isCritical ? "text-red-600" : "text-gray-600"}`}>
        {trend}
      </p>
    </div>
  );
}
