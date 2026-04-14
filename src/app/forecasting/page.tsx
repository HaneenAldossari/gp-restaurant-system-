"use client";

import TopBar from "@/components/TopBar";
import { useState, useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Area, AreaChart, Line,
} from "recharts";
import {
  TrendingUp, TrendingDown, Calendar, Zap, Sun, ShoppingBag,
  ArrowUpRight, Coffee, CakeSlice, Sandwich,
  DollarSign, FileDown, Layers, Tag,
} from "lucide-react";
import {
  totalForecast, categoryForecastsMap, itemForecastsMap,
  productsList, categoriesList,
} from "@/lib/data";

const categoryIcons: Record<string, React.ReactNode> = {
  "Espresso Drinks": <Coffee size={16} />,
  "Iced Drinks": <Coffee size={16} />,
  "Filter Coffee": <Coffee size={16} />,
  Sweets: <CakeSlice size={16} />,
  Savory: <Sandwich size={16} />,
};

const categoryColors: Record<string, string> = {
  "Espresso Drinks": "from-indigo-500 to-blue-500",
  "Iced Drinks": "from-cyan-500 to-sky-500",
  "Filter Coffee": "from-violet-500 to-purple-500",
  Sweets: "from-pink-500 to-rose-500",
  Savory: "from-orange-500 to-amber-500",
};

type Scope = "total" | "category" | "item";

export default function ForecastingPage() {
  const [scope, setScope] = useState<Scope>("total");
  const [selectedCategory, setSelectedCategory] = useState(categoriesList[0] ?? "");
  const [selectedItem, setSelectedItem] = useState("");
  const [period, setPeriod] = useState("30");
  const [periodMode, setPeriodMode] = useState<"preset" | "custom">("preset");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [showResults, setShowResults] = useState(false);

  // Items for the selected category
  const categoryItems = useMemo(
    () => productsList.filter((i) => i.category === selectedCategory),
    [selectedCategory]
  );

  // When category changes, reset item to first in that category
  const handleCategoryChange = (cat: string) => {
    setSelectedCategory(cat);
    const items = productsList.filter((i) => i.category === cat);
    setSelectedItem(items[0]?.name ?? "");
    setShowResults(false);
  };

  const handleScopeChange = (s: Scope) => {
    setScope(s);
    setShowResults(false);
    if (s === "item" && !selectedItem) {
      const items = productsList.filter((i) => i.category === selectedCategory);
      setSelectedItem(items[0]?.name ?? "");
    }
  };

  // Calculate effective period for custom date range
  const effectivePeriod = useMemo(() => {
    if (periodMode === "custom" && startDate && endDate) {
      const diff = Math.ceil((new Date(endDate).getTime() - new Date(startDate).getTime()) / (1000 * 60 * 60 * 24)) + 1;
      return diff > 0 ? diff : 0;
    }
    return parseInt(period);
  }, [periodMode, startDate, endDate, period]);

  // Period display text
  const periodDisplay = periodMode === "custom" && startDate && endDate
    ? `${startDate} to ${endDate} (${effectivePeriod} days)`
    : `Next ${period} days`;

  // Build label for current selection
  const forecastLabel = scope === "total"
    ? "Total Revenue"
    : scope === "category"
    ? `${selectedCategory} Revenue`
    : selectedItem;

  // Look up forecast data
  const forecastResult = useMemo(() => {
    if (!showResults) return null;
    if (scope === "total") return totalForecast;
    if (scope === "item") return itemForecastsMap[selectedItem] ?? null;
    return categoryForecastsMap[selectedCategory] ?? null;
  }, [showResults, scope, selectedCategory, selectedItem]);

  return (
    <div>
      <TopBar title="Sales Forecasting" />
      <div className="p-6 space-y-6">

        {/* ── Configuration Panel ── */}
        <div className="bg-white dark:bg-slate-800 rounded-2xl shadow-sm border border-slate-100 dark:border-slate-700/50 overflow-hidden">
          {/* Header */}
          <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-700/50 flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white">
              <Zap size={18} />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-800 dark:text-white">Forecast Configuration</h2>
              <p className="text-[11px] text-slate-400 dark:text-slate-500">Prophet + LSTM model &middot; auto-selects the most accurate approach</p>
            </div>
          </div>

          <div className="p-6 space-y-6">
            {/* Row 1: Scope + Period side by side */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

              {/* Forecast Scope */}
              <div>
                <label className="text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase tracking-wider mb-3 block">
                  Forecast Scope
                </label>
                <div className="flex bg-slate-100 dark:bg-slate-900/50 rounded-xl p-1 gap-1">
                  {([
                    { key: "total" as Scope, label: "Total Revenue", icon: DollarSign },
                    { key: "category" as Scope, label: "By Category", icon: Layers },
                    { key: "item" as Scope, label: "By Item", icon: Tag },
                  ]).map((s) => (
                    <button
                      key={s.key}
                      onClick={() => handleScopeChange(s.key)}
                      className={`flex-1 flex items-center justify-center gap-2 px-3 py-2.5 text-xs font-semibold rounded-lg transition-all ${
                        scope === s.key
                          ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-sm"
                          : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300"
                      }`}
                    >
                      <s.icon size={14} />
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Forecast Period */}
              <div>
                <label className="text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase tracking-wider mb-3 block">
                  Forecast Period
                </label>
                <div className="flex bg-slate-100 dark:bg-slate-900/50 rounded-xl p-1 gap-1">
                  {[
                    { value: "7", label: "7 Days", sub: "1 week" },
                    { value: "14", label: "14 Days", sub: "2 weeks" },
                    { value: "30", label: "30 Days", sub: "1 month" },
                  ].map((p) => (
                    <button
                      key={p.value}
                      onClick={() => { setPeriod(p.value); setPeriodMode("preset"); setShowResults(false); }}
                      className={`flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all ${
                        periodMode === "preset" && period === p.value
                          ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-sm"
                          : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300"
                      }`}
                    >
                      {p.label}
                      <span className="block text-[10px] font-normal text-slate-400 dark:text-slate-500 mt-0.5">{p.sub}</span>
                    </button>
                  ))}
                  <button
                    onClick={() => { setPeriodMode("custom"); setShowResults(false); }}
                    className={`flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all ${
                      periodMode === "custom"
                        ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-sm"
                        : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300"
                    }`}
                  >
                    Custom
                    <span className="block text-[10px] font-normal text-slate-400 dark:text-slate-500 mt-0.5">date range</span>
                  </button>
                </div>

                {/* Custom date range inputs */}
                {periodMode === "custom" && (
                  <div className="flex gap-3 mt-3">
                    <div className="flex-1">
                      <label className="text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1 block">Start Date</label>
                      <input
                        type="date"
                        value={startDate}
                        onChange={(e) => { setStartDate(e.target.value); setShowResults(false); }}
                        className="w-full px-3 py-2.5 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50 text-slate-800 dark:text-slate-200 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
                      />
                    </div>
                    <div className="flex-1">
                      <label className="text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1 block">End Date</label>
                      <input
                        type="date"
                        value={endDate}
                        onChange={(e) => { setEndDate(e.target.value); setShowResults(false); }}
                        className="w-full px-3 py-2.5 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50 text-slate-800 dark:text-slate-200 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Row 2: Conditional dropdowns */}
            {scope !== "total" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* Category Dropdown */}
                <div>
                  <label className="text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase tracking-wider mb-2 block">
                    Category
                  </label>
                  <div className="relative">
                    <select
                      value={selectedCategory}
                      onChange={(e) => handleCategoryChange(e.target.value)}
                      className="w-full appearance-none px-4 py-3 pr-10 text-sm font-medium rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50 text-slate-800 dark:text-slate-200 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all"
                    >
                      {categoriesList.map((cat) => {
                        const count = productsList.filter((i) => i.category === cat).length;
                        return (
                          <option key={cat} value={cat}>
                            {cat} ({count} items)
                          </option>
                        );
                      })}
                    </select>
                    <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                      <div className={`w-6 h-6 rounded-md bg-gradient-to-br ${categoryColors[selectedCategory] ?? "from-slate-500 to-slate-600"} flex items-center justify-center text-white`}>
                        {categoryIcons[selectedCategory] ?? <ShoppingBag size={12} />}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Item Dropdown — only for item scope */}
                {scope === "item" && (
                  <div>
                    <label className="text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase tracking-wider mb-2 block">
                      Item
                    </label>
                    <select
                      value={selectedItem}
                      onChange={(e) => { setSelectedItem(e.target.value); setShowResults(false); }}
                      className="w-full appearance-none px-4 py-3 text-sm font-medium rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50 text-slate-800 dark:text-slate-200 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all"
                    >
                      {categoryItems.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            )}

            {/* Generate Button + Selection Summary */}
            <div className="flex items-center gap-4 pt-2">
              <button
                onClick={() => setShowResults(true)}
                className="px-8 py-3 bg-gradient-to-r from-indigo-600 to-purple-600 text-white text-sm font-bold rounded-xl hover:from-indigo-700 hover:to-purple-700 transition-all shadow-lg shadow-indigo-600/20 flex items-center gap-2.5"
              >
                <Zap size={16} />
                Generate Forecast
              </button>
              <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span className="px-2.5 py-1 rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 font-medium">
                  {forecastLabel}
                </span>
                <span>&middot;</span>
                <span>{periodDisplay}</span>
              </div>
            </div>
          </div>
        </div>

        {/* ── Results ── */}
        {showResults && forecastResult && (() => {
          const { chartData, summary, weeklyPattern, breakdown, insights, valueLabel } = forecastResult;
          const isItemForecast = scope === "item";
          const formatValue = isItemForecast
            ? (v: number) => `~${Math.round(v)} units`
            : (v: number) => `SAR ${Math.round(v).toLocaleString()}`;

          return (
          <>
            {/* Export Bar */}
            <div className="flex items-center justify-between bg-white dark:bg-slate-800 rounded-xl px-5 py-3 shadow-sm border border-slate-100 dark:border-slate-700/50">
              <div className="flex items-center gap-3">
                <div className={`w-8 h-8 rounded-lg ${scope === "total" ? "bg-gradient-to-br from-slate-700 to-slate-900" : `bg-gradient-to-br ${categoryColors[selectedCategory] ?? "from-slate-500 to-slate-600"}`} flex items-center justify-center text-white`}>
                  {scope === "total" ? <DollarSign size={14} /> : (categoryIcons[selectedCategory] ?? <ShoppingBag size={14} />)}
                </div>
                <div>
                  <p className="text-sm font-bold text-slate-800 dark:text-white">{forecastLabel}</p>
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">{periodDisplay} forecast</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button className="px-3.5 py-2 text-xs font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/30 rounded-lg hover:bg-emerald-100 dark:hover:bg-emerald-900/50 transition-colors flex items-center gap-1.5">
                  <FileDown size={13} />
                  CSV
                </button>
                <button className="px-3.5 py-2 text-xs font-medium text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/30 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/50 transition-colors flex items-center gap-1.5">
                  <FileDown size={13} />
                  PDF
                </button>
              </div>
            </div>

            {/* Prediction Chart */}
            <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                  {scope === "total" ? "Total Revenue Forecast" : isItemForecast ? `${selectedItem} — Demand Forecast` : `${selectedCategory} — Revenue Forecast`}
                </h3>
                <div className="flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
                  <span className="flex items-center gap-1.5"><span className="w-4 h-0.5 bg-indigo-500 rounded" /> Actual</span>
                  <span className="flex items-center gap-1.5"><span className="w-4 h-0.5 bg-emerald-500 rounded" style={{ borderTop: "2px dashed #10b981", height: 0 }} /> Predicted</span>
                  <span className="flex items-center gap-1.5"><span className="w-4 h-2 bg-emerald-100 dark:bg-emerald-900/30 rounded" /> Confidence</span>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={350}>
                <AreaChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} stroke="#94a3b8" interval={4} />
                  <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" tickFormatter={isItemForecast ? (v) => `${v}` : (v) => `SAR ${(v / 1000).toFixed(0)}k`} />
                  <Tooltip
                    contentStyle={{ borderRadius: "8px", border: "none", boxShadow: "0 4px 12px rgba(0,0,0,0.1)" }}
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    formatter={(value: any, name: any) => {
                      if (value === null || value === undefined) return ["-", String(name)];
                      const label = name === "actual" ? "Actual" : name === "predicted" ? "Predicted" : name === "upper" ? "Best Case" : "Worst Case";
                      return [isItemForecast ? `${Number(value).toLocaleString()} units` : `SAR ${Number(value).toLocaleString()}`, label];
                    }}
                  />
                  <Area type="monotone" dataKey="upper" stroke="none" fill="#10b981" fillOpacity={0.08} />
                  <Area type="monotone" dataKey="lower" stroke="none" fill="#ffffff" fillOpacity={0} />
                  <Line type="monotone" dataKey="actual" stroke="#6366f1" strokeWidth={2.5} dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="predicted" stroke="#10b981" strokeWidth={2.5} strokeDasharray="6 3" dot={false} connectNulls={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Summary Cards */}
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
              {[
                { label: isItemForecast ? "Predicted Avg/Day" : "Expected Avg Daily", value: formatValue(summary.avgPredicted), icon: TrendingUp, color: "text-indigo-600" },
                { label: "Peak Day", value: summary.peakDay, icon: Calendar, color: "text-emerald-600" },
                { label: "Highest Predicted", value: `${summary.highest.date} — ${formatValue(summary.highest.value)}`, icon: ArrowUpRight, color: "text-emerald-600" },
                { label: "Lowest Predicted", value: `${summary.lowest.date} — ${formatValue(summary.lowest.value)}`, icon: TrendingDown, color: "text-red-500" },
                { label: isItemForecast ? "Demand Range" : "Revenue Range", value: `${formatValue(summary.range.low)} — ${formatValue(summary.range.high)}`, icon: Sun, color: "text-amber-500" },
              ].map((card) => (
                <div key={card.label} className="bg-white dark:bg-slate-800 rounded-xl p-4 shadow-sm border border-slate-100 dark:border-slate-700/50">
                  <card.icon size={18} className={`${card.color} mb-2`} />
                  <p className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">{card.label}</p>
                  <p className="text-sm font-bold text-slate-800 dark:text-white mt-1">{card.value}</p>
                </div>
              ))}
            </div>

            {/* Weekly Pattern + Insights */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
                <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Weekly Sales Pattern</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={weeklyPattern}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="day" tick={{ fontSize: 12 }} stroke="#94a3b8" />
                    <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" tickFormatter={isItemForecast ? (v) => `${v}` : (v) => `SAR ${(v / 1000).toFixed(1)}k`} />
                    <Tooltip formatter={(value: unknown) => [isItemForecast ? `${Number(value).toLocaleString()} units` : `SAR ${Number(value).toLocaleString()}`, "Avg"]} />
                    <Bar dataKey="avg" fill="#6366f1" radius={[6, 6, 0, 0]} barSize={36} />
                  </BarChart>
                </ResponsiveContainer>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-3 text-center">
                  Weekends generate <span className="font-semibold text-indigo-600 dark:text-indigo-400">{insights.weekendLift}% more</span> {valueLabel} than weekdays
                </p>
              </div>

              <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50 space-y-4">
                <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Insights & Recommendations</h3>
                <div className="space-y-3">
                  {[
                    { icon: TrendingUp, color: "text-emerald-500", bg: "bg-emerald-50 dark:bg-emerald-950/30", text: `Sales are trending ${insights.trend} based on historical data` },
                    { icon: Sun, color: "text-amber-500", bg: "bg-amber-50 dark:bg-amber-950/30", text: `Weekend sales are ${insights.weekendLift}% higher than weekday average` },
                    { icon: Calendar, color: "text-blue-500", bg: "bg-blue-50 dark:bg-blue-950/30", text: `${insights.peakDay} is consistently the strongest day` },
                  ].map((insight, i) => (
                    <div key={i} className={`flex items-start gap-3 p-3 rounded-lg ${insight.bg}`}>
                      <insight.icon size={16} className={`${insight.color} mt-0.5 flex-shrink-0`} />
                      <p className="text-xs text-slate-700 dark:text-slate-300">{insight.text}</p>
                    </div>
                  ))}
                </div>
                <div className="border-t border-slate-100 dark:border-slate-700/50 pt-4">
                  <h4 className="text-xs font-semibold text-slate-600 dark:text-slate-300 mb-2 flex items-center gap-1.5">
                    <ShoppingBag size={14} /> Recommendations
                  </h4>
                  <ul className="space-y-2">
                    {(scope === "total" ? [
                      "Overall revenue is trending " + insights.trend + " — maintain current marketing strategy",
                      "Mid-week sales are consistently lower — consider a Wednesday special",
                      `${insights.peakDay} staffing should be optimized for peak demand`,
                    ] : scope === "item" ? [
                      `${selectedItem} demand is ${insights.trend} — ensure sufficient stock`,
                      `Consider bundling ${selectedItem} with complementary items`,
                      `Highest demand on ${insights.peakDay}s — optimize staffing accordingly`,
                    ] : [
                      `${selectedCategory} revenue is expected to drop mid-week — consider a Wednesday promotion`,
                      `Top items in ${selectedCategory} are driving most of the growth`,
                      `Weekend demand peaks — consider a ${selectedCategory.toLowerCase()} combo deal`,
                    ]).map((rec, i) => (
                      <li key={i} className="text-xs text-slate-600 dark:text-slate-400 flex items-start gap-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 mt-1.5 flex-shrink-0" />
                        {rec}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            {/* Breakdown */}
            {scope !== "item" && breakdown.length > 0 && (
              <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
                <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-1">
                  {scope === "total" ? "Category Breakdown" : `Item Breakdown — ${selectedCategory}`}
                </h3>
                <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
                  {scope === "total" ? "Predicted revenue by category" : `Predicted demand for each item in ${selectedCategory}`}
                </p>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                  {breakdown.map((item) => (
                    <div key={item.name} className="bg-slate-50 dark:bg-slate-900/50 rounded-lg p-4">
                      <div className="flex items-center gap-2 mb-1">
                        {scope === "total" && (
                          <div className={`w-6 h-6 rounded-md bg-gradient-to-br ${categoryColors[item.name] ?? "from-slate-500 to-slate-600"} flex items-center justify-center text-white`}>
                            {categoryIcons[item.name] ?? <ShoppingBag size={12} />}
                          </div>
                        )}
                        <p className="text-xs font-bold text-slate-700 dark:text-slate-200">{item.name}</p>
                      </div>
                      <p className="text-xl font-bold text-slate-800 dark:text-white mt-1">
                        {scope === "total" ? `SAR ${item.predicted.toLocaleString()}` : `~${item.predicted}`}
                      </p>
                      <p className="text-[10px] text-slate-400">
                        {scope === "total" ? `${item.itemCount ?? 0} items` : "units"} &middot; {periodDisplay}
                      </p>
                      <p className={`text-[11px] font-semibold mt-1 ${item.change >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                        {item.change >= 0 ? "+" : ""}{item.change}% vs current
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
          );
        })()}

        {/* Empty state when no results yet */}
        {!showResults && (
          <div className="bg-white dark:bg-slate-800 rounded-xl p-12 shadow-sm border border-slate-100 dark:border-slate-700/50 text-center">
            <div className="w-16 h-16 rounded-2xl bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center mx-auto mb-4">
              <TrendingUp size={28} className="text-indigo-400" />
            </div>
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-1">Ready to Forecast</h3>
            <p className="text-xs text-slate-400 dark:text-slate-500 max-w-sm mx-auto">
              Configure your forecast scope and period above, then click <span className="font-semibold text-indigo-500">Generate Forecast</span> to see predictions, weekly patterns, and actionable insights.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
