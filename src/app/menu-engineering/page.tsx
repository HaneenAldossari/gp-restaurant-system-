"use client";

import TopBar from "@/components/TopBar";
import { useState, useMemo } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ZAxis,
} from "recharts";
import { Star, TrendingUp, HelpCircle, XCircle, ArrowRight, Download } from "lucide-react";
import { menuEngineeringData } from "@/lib/data";

const classColors: Record<string, string> = {
  Star: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400",
  Plowhorse: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
  Puzzle: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400",
  Dog: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
};

const dotColors: Record<string, string> = {
  Star: "#10b981",
  Plowhorse: "#3b82f6",
  Puzzle: "#f59e0b",
  Dog: "#ef4444",
};

const classIcons: Record<string, React.ReactNode> = {
  Star: <Star size={14} />,
  Plowhorse: <TrendingUp size={14} />,
  Puzzle: <HelpCircle size={14} />,
  Dog: <XCircle size={14} />,
};

type Classification = "Star" | "Plowhorse" | "Puzzle" | "Dog";

export default function MenuEngineering() {
  const classified = menuEngineeringData.items as unknown as {
    name: string; category: string; qtySold: number; revenue: number;
    price: number; cost: number; profitMargin: number; popularity: number;
    classification: Classification;
  }[];
  const avgPop = menuEngineeringData.avgPopularity;
  const avgMargin = menuEngineeringData.avgMargin;

  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [priceAdjust, setPriceAdjust] = useState(0);

  const selectedData = useMemo(
    () => classified.find((i) => i.name === selectedItem),
    [classified, selectedItem]
  );

  const newPrice = selectedData ? selectedData.price + priceAdjust : 0;
  const newMargin = selectedData ? ((newPrice - selectedData.cost) / newPrice) * 100 : 0;
  const demandImpact = priceAdjust > 0
    ? -(priceAdjust / (selectedData?.price || 1)) * 15
    : (Math.abs(priceAdjust) / (selectedData?.price || 1)) * 10;
  const profitChange = selectedData
    ? ((newPrice - selectedData.cost) * selectedData.qtySold * (1 + demandImpact / 100)) -
      ((selectedData.price - selectedData.cost) * selectedData.qtySold)
    : 0;

  const groups = useMemo(() => ({
    Star: classified.filter((i) => i.classification === "Star"),
    Plowhorse: classified.filter((i) => i.classification === "Plowhorse"),
    Puzzle: classified.filter((i) => i.classification === "Puzzle"),
    Dog: classified.filter((i) => i.classification === "Dog"),
  }), [classified]);

  return (
    <div>
      <TopBar title="Menu Engineering" />
      <div className="p-6 space-y-6">
        {/* Export */}
        <div className="flex justify-end gap-2">
          <button className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors shadow-sm">
            <Download size={15} />
            Download PDF
          </button>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {(["Star", "Plowhorse", "Puzzle", "Dog"] as const).map((cls) => {
            const items = groups[cls];
            const totalRev = items.reduce((s, i) => s + i.revenue, 0);
            const descriptions: Record<string, string> = {
              Star: "High popularity & high margin — keep promoting",
              Plowhorse: "High popularity, low margin — consider price increase",
              Puzzle: "Low popularity, high margin — needs more promotion",
              Dog: "Low popularity & low margin — consider removing",
            };
            return (
              <div key={cls} className="bg-white dark:bg-slate-800 rounded-xl p-4 shadow-sm border border-slate-100 dark:border-slate-700/50">
                <div className="flex items-center gap-2 mb-2">
                  <span className={`p-1.5 rounded-md ${classColors[cls]}`}>{classIcons[cls]}</span>
                  <span className="text-sm font-bold text-slate-700 dark:text-slate-200">{cls}s</span>
                  <span className="ml-auto text-xs bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-2 py-0.5 rounded-full font-medium">
                    {items.length} items
                  </span>
                </div>
                <p className="text-lg font-bold text-slate-800 dark:text-white">SAR {totalRev.toLocaleString()}</p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">{descriptions[cls]}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {items.slice(0, 3).map((i) => (
                    <span key={i.name} className="text-[10px] bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-1.5 py-0.5 rounded">
                      {i.name}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {/* Scatter Plot */}
        <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Boston Matrix — Interactive Scatter Plot</h3>
          <ResponsiveContainer width="100%" height={400}>
            <ScatterChart margin={{ top: 20, right: 30, bottom: 20, left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis type="number" dataKey="popularity" name="Popularity %" tick={{ fontSize: 11 }} stroke="#94a3b8" label={{ value: "Popularity %", position: "bottom", fontSize: 12 }} />
              <YAxis type="number" dataKey="profitMargin" name="Profit Margin %" tick={{ fontSize: 11 }} stroke="#94a3b8" label={{ value: "Profit Margin %", angle: -90, position: "left", fontSize: 12 }} />
              <ZAxis type="number" dataKey="revenue" range={[80, 600]} />
              <ReferenceLine x={avgPop} stroke="#94a3b8" strokeDasharray="5 5" />
              <ReferenceLine y={avgMargin} stroke="#94a3b8" strokeDasharray="5 5" />
              <Tooltip
                content={({ payload }) => {
                  if (!payload?.length) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-white dark:bg-slate-800 p-3 rounded-lg shadow-lg border border-slate-200 dark:border-slate-700 text-xs">
                      <p className="font-bold text-sm text-slate-800 dark:text-white">{d.name}</p>
                      <p className="text-slate-500">{d.category}</p>
                      <p className="mt-1">Popularity: {d.popularity.toFixed(1)}%</p>
                      <p>Margin: {d.profitMargin.toFixed(1)}%</p>
                      <p>Revenue: SAR {d.revenue.toLocaleString()}</p>
                      <p className="mt-1 font-semibold" style={{ color: dotColors[d.classification] }}>{d.classification}</p>
                    </div>
                  );
                }}
              />
              {(["Star", "Plowhorse", "Puzzle", "Dog"] as const).map((cls) => (
                <Scatter
                  key={cls}
                  name={cls}
                  data={classified.filter((i) => i.classification === cls)}
                  fill={dotColors[cls]}
                  opacity={0.85}
                />
              ))}
            </ScatterChart>
          </ResponsiveContainer>
          <div className="flex justify-center gap-6 mt-2">
            {(["Star", "Plowhorse", "Puzzle", "Dog"] as const).map((cls) => (
              <div key={cls} className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
                <div className="w-3 h-3 rounded-full" style={{ backgroundColor: dotColors[cls] }} />
                {cls}
              </div>
            ))}
          </div>
        </div>

        {/* Items Table */}
        <div className="bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-100 dark:border-slate-700/50 overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-100 dark:border-slate-700/50 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">All Menu Items</h3>
            <button className="text-xs px-3 py-1.5 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors font-medium">
              Export CSV
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-900/50">
                  {["Item", "Category", "Qty Sold", "Price", "Cost", "Margin %", "Class"].map((h) => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700/50">
                {classified.map((item) => (
                  <tr
                    key={item.name}
                    onClick={() => { setSelectedItem(item.name); setPriceAdjust(0); }}
                    className="hover:bg-slate-50 dark:hover:bg-slate-900/30 cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3 font-medium text-slate-700 dark:text-slate-200">{item.name}</td>
                    <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{item.category}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{item.qtySold}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">SAR {item.price}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">SAR {item.cost}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{item.profitMargin.toFixed(1)}%</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${classColors[item.classification]}`}>
                        {classIcons[item.classification]}
                        {item.classification}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* What-If Price Simulator */}
        <div className="bg-white dark:bg-slate-800 rounded-xl p-6 shadow-sm border border-slate-100 dark:border-slate-700/50">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">What-If Price Simulator</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">Select an item from the table above, then adjust the price to see predicted impact.</p>

          {selectedData ? (
            <div className="space-y-5">
              <div className="flex items-center gap-3">
                <span className="text-base font-bold text-slate-800 dark:text-white">{selectedData.name}</span>
                <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${classColors[selectedData.classification]}`}>
                  {selectedData.classification}
                </span>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-slate-500 dark:text-slate-400">Current: SAR {selectedData.price}</span>
                  <span className="text-sm font-bold text-indigo-600 dark:text-indigo-400">New: SAR {newPrice.toFixed(0)}</span>
                </div>
                <input
                  type="range"
                  min={-Math.floor(selectedData.price * 0.3)}
                  max={Math.floor(selectedData.price * 0.5)}
                  value={priceAdjust}
                  onChange={(e) => setPriceAdjust(Number(e.target.value))}
                  className="w-full accent-indigo-600"
                />
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <div className="bg-slate-50 dark:bg-slate-900/50 rounded-lg p-3">
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">New Margin</p>
                  <p className="text-lg font-bold text-slate-800 dark:text-white">{newMargin.toFixed(1)}%</p>
                  <p className="text-[10px] text-slate-400">was {selectedData.profitMargin.toFixed(1)}%</p>
                </div>
                <div className="bg-slate-50 dark:bg-slate-900/50 rounded-lg p-3">
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">Monthly Profit Impact</p>
                  <p className={`text-lg font-bold ${profitChange >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                    {profitChange >= 0 ? "+" : ""}SAR {Math.abs(profitChange).toFixed(0)}
                  </p>
                </div>
                <div className="bg-slate-50 dark:bg-slate-900/50 rounded-lg p-3">
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">Demand Impact</p>
                  <p className={`text-lg font-bold ${demandImpact >= 0 ? "text-emerald-600" : "text-amber-500"}`}>
                    {demandImpact >= 0 ? "+" : ""}{demandImpact.toFixed(1)}%
                  </p>
                </div>
                <div className="bg-slate-50 dark:bg-slate-900/50 rounded-lg p-3">
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">New Classification</p>
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${classColors[selectedData.classification]}`}>
                      {selectedData.classification}
                    </span>
                    {priceAdjust !== 0 && (
                      <>
                        <ArrowRight size={12} className="text-slate-400" />
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          newMargin > avgMargin ? classColors["Star"] : classColors["Plowhorse"]
                        }`}>
                          {newMargin > avgMargin
                            ? selectedData.popularity > avgPop ? "Star" : "Puzzle"
                            : selectedData.popularity > avgPop ? "Plowhorse" : "Dog"
                          }
                        </span>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {priceAdjust !== 0 && (
                <div className={`rounded-lg p-3 text-xs font-medium ${
                  profitChange >= 0
                    ? "bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-400"
                    : "bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-400"
                }`}>
                  {profitChange >= 0
                    ? `Safe to ${priceAdjust > 0 ? "increase" : "decrease"} — this item has ${selectedData.popularity > avgPop ? "strong demand" : "room for growth"} even at the new price.`
                    : `Warning: This price ${priceAdjust > 0 ? "increase" : "decrease"} may reduce overall profitability. Consider a smaller adjustment.`
                  }
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-10 text-slate-400 dark:text-slate-500">
              <HelpCircle size={32} className="mx-auto mb-2 opacity-50" />
              <p className="text-sm">Click on any item in the table above to simulate price changes</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
