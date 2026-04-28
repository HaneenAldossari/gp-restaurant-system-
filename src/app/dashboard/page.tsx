"use client";

import TopBar from "@/components/TopBar";
import KPICard from "@/components/KPICard";
import {
  DollarSign,
  ShoppingCart,
  Receipt,
  Crown,
  AlertTriangle,
  Calendar,
  TrendingUp,
  Download,
} from "lucide-react";
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { useState, useMemo } from "react";
import { dashboardData } from "@/lib/data";

export default function Dashboard() {
  const [granularity, setGranularity] = useState<"daily" | "monthly">("daily");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-12-31");
  const [category, setCategory] = useState("All");

  // Filter daily data by date range and optionally by category
  // (category filtering is at the sales-by-category level; daily is total)
  const filteredDaily = useMemo(() =>
    dashboardData.dailyRevenue.filter(
      (d) => d.date >= startDate && d.date <= endDate
    ), [startDate, endDate]
  );

  const filteredMonthly = useMemo(() => {
    const start = startDate.slice(0, 7);
    const end = endDate.slice(0, 7);
    return dashboardData.monthlyRevenue.filter(
      (d) => d.month >= start && d.month <= end
    );
  }, [startDate, endDate]);

  // Compute KPIs from filtered data
  const kpis = useMemo(() => {
    const totalRevenue = filteredDaily.reduce((s, d) => s + d.revenue, 0);
    const totalOrders = filteredDaily.reduce((s, d) => s + d.orders, 0);
    const avgOrder = totalOrders ? Math.round(totalRevenue / totalOrders * 100) / 100 : 0;
    const numDays = filteredDaily.length || 1;
    const avgDaily = Math.round(totalRevenue / numDays * 100) / 100;
    const lastGrowth = filteredMonthly.length ? filteredMonthly[filteredMonthly.length - 1].growth : 0;
    return {
      ...dashboardData.kpis,
      totalRevenue,
      totalOrders,
      avgOrderValue: avgOrder,
      avgDailyRevenue: avgDaily,
      lastGrowth,
    };
  }, [filteredDaily, filteredMonthly]);

  const { salesByCategory, topByRevenue, topByQty, heatmapData, categories } = dashboardData;

  return (
    <div>
      <TopBar title="Sales Dashboard" />
      <div className="p-6 space-y-6">
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200"
          />
          <span className="text-slate-400">to</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200"
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200"
          >
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
          <div className="ml-auto flex items-center gap-2">
            <div className="flex bg-slate-100 dark:bg-slate-800 rounded-lg p-0.5">
            {(["daily", "monthly"] as const).map((g) => (
              <button
                key={g}
                onClick={() => setGranularity(g)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                  granularity === g
                    ? "bg-white dark:bg-slate-700 text-slate-800 dark:text-white shadow-sm"
                    : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
                }`}
              >
                {g.charAt(0).toUpperCase() + g.slice(1)}
              </button>
            ))}
            </div>
            <button className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors shadow-sm">
              <Download size={15} />
              PDF
            </button>
          </div>
        </div>

        {/* KPI Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <KPICard title="Total Revenue" value={`SAR ${kpis.totalRevenue.toLocaleString()}`} change={kpis.lastGrowth} icon={DollarSign} color="bg-indigo-500" />
          <KPICard title="Total Orders" value={kpis.totalOrders.toLocaleString()} change={4.2} icon={ShoppingCart} color="bg-emerald-500" />
          <KPICard title="Avg Order Value" value={`SAR ${kpis.avgOrderValue.toFixed(2)}`} change={2.1} icon={Receipt} color="bg-amber-500" />
          <KPICard title="Avg Daily Revenue" value={`SAR ${kpis.avgDailyRevenue.toLocaleString()}`} change={kpis.lastGrowth} icon={TrendingUp} color="bg-purple-500" />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <KPICard title="Best Seller" value={kpis.bestSeller.name} subtitle={`${kpis.bestSeller.qty} units sold`} icon={Crown} color="bg-emerald-500" />
          <KPICard title="Worst Seller" value={kpis.worstSeller.name} subtitle={`${kpis.worstSeller.qty} units sold`} icon={AlertTriangle} color="bg-red-500" />
          <KPICard title="Busiest Day" value={kpis.busiestDay.name} subtitle={`Avg SAR ${kpis.busiestDay.avgRevenue.toLocaleString()} revenue`} icon={Calendar} color="bg-blue-500" />
        </div>

        {/* Charts Row 1 */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Revenue Trend</h3>
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={granularity === "daily" ? filteredDaily : filteredMonthly}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey={granularity === "daily" ? "date" : "month"} tick={{ fontSize: 11 }} stroke="#94a3b8" />
                <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" tickFormatter={(v) => `SAR ${(v/1000).toFixed(0)}k`} />
                <Tooltip
                  contentStyle={{ borderRadius: "8px", border: "none", boxShadow: "0 4px 12px rgba(0,0,0,0.1)" }}
                  formatter={(value: unknown) => [`SAR ${Number(value).toLocaleString()}`, "Revenue"]}
                />
                <Line type="monotone" dataKey="revenue" stroke="#6366f1" strokeWidth={2.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Sales by Category</h3>
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie data={salesByCategory} cx="50%" cy="50%" innerRadius={60} outerRadius={95} paddingAngle={4} dataKey="value" label={({ name, percent }: { name?: string; percent?: number }) => `${name ?? ""} ${((percent ?? 0) * 100).toFixed(0)}%`} labelLine={false} style={{ fontSize: 10 }}>
                  {salesByCategory.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip formatter={(value: unknown) => [`SAR ${Number(value).toLocaleString()}`, "Revenue"]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Charts Row 2 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Top 10 Items by Revenue</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={[...topByRevenue]} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11 }} stroke="#94a3b8" tickFormatter={(v) => `SAR ${(v/1000).toFixed(0)}k`} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} stroke="#94a3b8" width={100} />
                <Tooltip formatter={(value: unknown) => [`SAR ${Number(value).toLocaleString()}`, "Revenue"]} />
                <Bar dataKey="revenue" fill="#6366f1" radius={[0, 4, 4, 0]} barSize={18} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Top 10 Items by Quantity</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={[...topByQty]} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11 }} stroke="#94a3b8" />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} stroke="#94a3b8" width={100} />
                <Tooltip formatter={(value: unknown) => [Number(value).toLocaleString(), "Qty Sold"]} />
                <Bar dataKey="qtySold" fill="#10b981" radius={[0, 4, 4, 0]} barSize={18} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Heatmap */}
        <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Sales Heatmap — Day vs Hour</h3>
          <div className="overflow-x-auto">
            <div className="inline-grid gap-1" style={{ gridTemplateColumns: `80px repeat(14, 1fr)` }}>
              <div />
              {["9AM","10AM","11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM","7PM","8PM","9PM","10PM"].map((h) => (
                <div key={h} className="text-[10px] text-center text-slate-500 dark:text-slate-400 font-medium py-1">{h}</div>
              ))}
              {["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].map((day) => (
                <>
                  <div key={day} className="text-xs text-slate-600 dark:text-slate-300 font-medium flex items-center">{day}</div>
                  {heatmapData.filter((d) => d.day === day).map((cell, i) => {
                    const maxVal = Math.max(...heatmapData.map((h) => h.value), 1);
                    const intensity = Math.min(cell.value / maxVal, 1);
                    return (
                      <div
                        key={`${day}-${i}`}
                        className="w-full aspect-square rounded-sm cursor-pointer"
                        style={{ backgroundColor: `rgba(99, 102, 241, ${0.1 + intensity * 0.85})` }}
                        title={`${cell.day} ${cell.hour}: ${cell.value} orders`}
                      />
                    );
                  })}
                </>
              ))}
            </div>
          </div>
        </div>

        {/* Monthly Revenue with Growth */}
        <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Monthly Revenue Growth</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={[...filteredMonthly]}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="month" tick={{ fontSize: 12 }} stroke="#94a3b8" />
              <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" tickFormatter={(v) => `SAR ${(v/1000).toFixed(0)}k`} />
              <Tooltip formatter={(value: unknown) => [`SAR ${Number(value).toLocaleString()}`, "Revenue"]} />
              {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
              <Bar dataKey="revenue" fill="#8b5cf6" radius={[6, 6, 0, 0]} barSize={50} label={(props: any) => {
                const x = Number(props.x ?? 0);
                const y = Number(props.y ?? 0);
                const w = Number(props.width ?? 0);
                const idx = Number(props.index ?? 0);
                const g = filteredMonthly[idx]?.growth ?? 0;
                return g !== 0 ? (
                  <text x={x + w / 2} y={y - 8} textAnchor="middle" fontSize={11} fontWeight={600} fill={g > 0 ? "#10b981" : "#ef4444"}>
                    {g > 0 ? "+" : ""}{g}%
                  </text>
                ) : null;
              }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
