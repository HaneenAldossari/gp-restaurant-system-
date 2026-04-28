"use client";

import TopBar from "@/components/TopBar";
import { useTheme } from "@/components/ThemeProvider";
import { Sun, Moon, User, Lock, Palette } from "lucide-react";

export default function SettingsPage() {
  const { theme, toggleTheme } = useTheme();

  return (
    <div>
      <TopBar title="Settings" />
      <div className="p-6 space-y-6 max-w-2xl">
        {/* Profile */}
        <div className="bg-white dark:bg-slate-800 rounded-xl p-6 shadow-sm border border-slate-100 dark:border-slate-700/50">
          <div className="flex items-center gap-3 mb-5">
            <User size={18} className="text-slate-500" />
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Profile</h3>
          </div>
          <div className="flex items-center gap-5 mb-5">
            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-xl font-bold">
              H
            </div>
            <div>
              <p className="font-semibold text-slate-800 dark:text-white">Haneen Aldossari</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">haneen@restaurant.com</p>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 mt-1 rounded-full text-[10px] font-medium bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400">
                Admin
              </span>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 dark:text-slate-400 block mb-1">Full Name</label>
              <input defaultValue="Haneen Aldossari" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-200" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 dark:text-slate-400 block mb-1">Email</label>
              <input defaultValue="haneen@restaurant.com" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-200" />
            </div>
          </div>
          <button className="mt-4 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors">
            Save Changes
          </button>
        </div>

        {/* Password */}
        <div className="bg-white dark:bg-slate-800 rounded-xl p-6 shadow-sm border border-slate-100 dark:border-slate-700/50">
          <div className="flex items-center gap-3 mb-5">
            <Lock size={18} className="text-slate-500" />
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Change Password</h3>
          </div>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-slate-500 dark:text-slate-400 block mb-1">Current Password</label>
              <input type="password" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-200" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 block mb-1">New Password</label>
                <input type="password" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-200" />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 block mb-1">Confirm Password</label>
                <input type="password" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-200" />
              </div>
            </div>
          </div>
          <button className="mt-4 px-4 py-2 bg-slate-800 dark:bg-slate-600 text-white text-sm font-medium rounded-lg hover:bg-slate-700 dark:hover:bg-slate-500 transition-colors">
            Update Password
          </button>
        </div>

        {/* Appearance */}
        <div className="bg-white dark:bg-slate-800 rounded-xl p-6 shadow-sm border border-slate-100 dark:border-slate-700/50">
          <div className="flex items-center gap-3 mb-5">
            <Palette size={18} className="text-slate-500" />
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Appearance</h3>
          </div>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">Theme</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">Switch between light and dark mode</p>
            </div>
            <button
              onClick={toggleTheme}
              className={`relative w-14 h-7 rounded-full transition-colors ${
                theme === "dark" ? "bg-indigo-600" : "bg-slate-300"
              }`}
            >
              <div
                className={`absolute top-0.5 w-6 h-6 rounded-full bg-white shadow-sm flex items-center justify-center transition-transform ${
                  theme === "dark" ? "translate-x-7" : "translate-x-0.5"
                }`}
              >
                {theme === "dark" ? <Moon size={12} className="text-indigo-600" /> : <Sun size={12} className="text-amber-500" />}
              </div>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
