"use client";

import TopBar from "@/components/TopBar";
import { Upload, FileSpreadsheet, CheckCircle, Download, Trash2, AlertCircle } from "lucide-react";
import { useState } from "react";

const uploadHistory = [
  { id: 1, fileName: "Sales_2025.xlsx", date: "2025-12-31", records: 2271, status: "Processed" },
  { id: 2, fileName: "sales_oct_2025.xlsx", date: "2025-10-31", records: 4650, status: "Processed" },
  { id: 3, fileName: "sales_sep_2025.csv", date: "2025-09-30", records: 4310, status: "Processed" },
  { id: 4, fileName: "sales_aug_2025.csv", date: "2025-08-31", records: 4580, status: "Processed" },
];

export default function UploadPage() {
  const [dragOver, setDragOver] = useState(false);
  const [showCleaning, setShowCleaning] = useState(false);

  return (
    <div>
      <TopBar title="Upload Data" />
      <div className="p-6 space-y-6 max-w-5xl">
        {/* Upload Zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); setShowCleaning(true); }}
          className={`relative border-2 border-dashed rounded-2xl p-12 text-center transition-all duration-200 cursor-pointer ${
            dragOver
              ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/30"
              : "border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 hover:border-indigo-400"
          }`}
        >
          <div className="flex flex-col items-center gap-4">
            <div className="w-16 h-16 rounded-2xl bg-indigo-100 dark:bg-indigo-900/40 flex items-center justify-center">
              <Upload size={28} className="text-indigo-600 dark:text-indigo-400" />
            </div>
            <div>
              <p className="text-base font-semibold text-slate-700 dark:text-slate-200">
                Drag & drop your sales file here
              </p>
              <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                Supports CSV and Excel (.xlsx) files
              </p>
            </div>
            <button
              onClick={() => setShowCleaning(true)}
              className="px-5 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors shadow-sm"
            >
              Browse Files
            </button>
          </div>
        </div>

        {/* Required columns */}
        <div className="bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-xl p-4">
          <div className="flex items-start gap-3">
            <AlertCircle size={18} className="text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-blue-800 dark:text-blue-300">Required Columns</p>
              <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">
                Your file must include: <span className="font-semibold">Order Date, Product, Quantity, Unit Price, Total Price</span>
              </p>
              <p className="text-xs text-blue-600 dark:text-blue-400 mt-0.5">
                Optional: Order ID, Order Time, Category, Product Cost
              </p>
              <button className="mt-2 text-xs text-blue-700 dark:text-blue-300 font-medium hover:underline flex items-center gap-1">
                <Download size={12} /> Download sample template
              </button>
            </div>
          </div>
        </div>

        {/* Cleaning Summary */}
        {showCleaning && (
          <div className="bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 rounded-xl p-5 animate-in">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle size={18} className="text-emerald-600" />
              <h3 className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">Data Cleaning Complete</h3>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: "Records Processed", value: "2,271" },
                { label: "Duplicates Removed", value: "0" },
                { label: "Missing Values Filled", value: "0" },
                { label: "Invalid Rows Skipped", value: "0" },
              ].map((item) => (
                <div key={item.label} className="bg-white dark:bg-slate-800 rounded-lg p-3 text-center">
                  <p className="text-lg font-bold text-slate-800 dark:text-white">{item.value}</p>
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">{item.label}</p>
                </div>
              ))}
            </div>
            <p className="text-xs text-emerald-600 dark:text-emerald-400 mt-3">
              Sales_2025.xlsx loaded — 14 products, 9 categories, Jan–Dec 2025.
            </p>
          </div>
        )}

        {/* Upload History */}
        <div className="bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-100 dark:border-slate-700/50 overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-100 dark:border-slate-700/50">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Upload History</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-900/50">
                  <th className="text-left px-5 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">File Name</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Upload Date</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Records</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Status</th>
                  <th className="px-5 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700/50">
                {uploadHistory.map((file) => (
                  <tr key={file.id} className="hover:bg-slate-50 dark:hover:bg-slate-900/30 transition-colors">
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-2">
                        <FileSpreadsheet size={16} className="text-emerald-500" />
                        <span className="font-medium text-slate-700 dark:text-slate-200">{file.fileName}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-slate-500 dark:text-slate-400">{file.date}</td>
                    <td className="px-5 py-3.5 text-slate-500 dark:text-slate-400">{file.records.toLocaleString()}</td>
                    <td className="px-5 py-3.5">
                      <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
                        <CheckCircle size={12} />
                        {file.status}
                      </span>
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <button className="p-1.5 text-slate-400 hover:text-red-500 transition-colors rounded-md hover:bg-red-50 dark:hover:bg-red-950/30">
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
