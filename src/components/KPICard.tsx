"use client";

import { TrendingUp, TrendingDown, LucideIcon } from "lucide-react";

interface KPICardProps {
  title: string;
  value: string;
  change?: number;
  subtitle?: string;
  icon: LucideIcon;
  color: string;
}

export default function KPICard({ title, value, change, subtitle, icon: Icon, color }: KPICardProps) {
  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/50 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">{title}</p>
          <p className="text-2xl font-bold text-slate-800 dark:text-white">{value}</p>
          {change !== undefined && (
            <div className={`flex items-center gap-1 text-xs font-semibold ${change >= 0 ? "text-emerald-600" : "text-red-500"}`}>
              {change >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
              <span>{change >= 0 ? "+" : ""}{change}%</span>
              <span className="text-slate-400 dark:text-slate-500 font-normal">vs last period</span>
            </div>
          )}
          {subtitle && <p className="text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
        </div>
        <div className={`p-2.5 rounded-lg ${color}`}>
          <Icon size={20} className="text-white" />
        </div>
      </div>
    </div>
  );
}
