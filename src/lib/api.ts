const API_BASE = "http://localhost:8000";

async function fetchAPI<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ── Dashboard ──
export interface DashboardData {
  kpis: {
    totalRevenue: number;
    totalOrders: number;
    avgOrderValue: number;
    avgDailyRevenue: number;
    bestSeller: { name: string; qty: number };
    worstSeller: { name: string; qty: number };
    busiestDay: { name: string; avgRevenue: number };
  };
  dailyRevenue: { date: string; revenue: number; orders: number }[];
  monthlyRevenue: { month: string; revenue: number; growth: number }[];
  salesByCategory: { name: string; value: number; color: string }[];
  topByRevenue: { name: string; revenue: number }[];
  topByQty: { name: string; qtySold: number }[];
  heatmapData: { day: string; hour: string; value: number }[];
  categories: string[];
}

export function fetchDashboard(params?: {
  start_date?: string;
  end_date?: string;
  category?: string;
}): Promise<DashboardData> {
  const qs = new URLSearchParams();
  if (params?.start_date) qs.set("start_date", params.start_date);
  if (params?.end_date) qs.set("end_date", params.end_date);
  if (params?.category && params.category !== "All") qs.set("category", params.category);
  const query = qs.toString();
  return fetchAPI(`/api/dashboard${query ? `?${query}` : ""}`);
}

// ── Menu Engineering ──
export interface MenuEngineeringData {
  items: {
    name: string;
    category: string;
    qtySold: number;
    revenue: number;
    totalCost: number;
    price: number;
    cost: number;
    profit: number;
    profitMargin: number;
    popularity: number;
    classification: "Star" | "Plowhorse" | "Puzzle" | "Dog";
  }[];
  quadrants: Record<string, { count: number; revenue: number; items: string[] }>;
  avgPopularity: number;
  avgMargin: number;
}

export function fetchMenuEngineering(params?: {
  start_date?: string;
  end_date?: string;
}): Promise<MenuEngineeringData> {
  const qs = new URLSearchParams();
  if (params?.start_date) qs.set("start_date", params.start_date);
  if (params?.end_date) qs.set("end_date", params.end_date);
  const query = qs.toString();
  return fetchAPI(`/api/menu-engineering${query ? `?${query}` : ""}`);
}

// ── Forecasting ──
export interface ForecastData {
  chartData: {
    date: string;
    actual: number | null;
    predicted: number | null;
    upper: number | null;
    lower: number | null;
  }[];
  valueLabel: string;
  summary: {
    avgPredicted: number;
    peakDay: string;
    highest: { date: string; value: number };
    lowest: { date: string; value: number };
    range: { low: number; high: number };
  };
  weeklyPattern: { day: string; avg: number }[];
  breakdown: { name: string; predicted: number; change: number; itemCount?: number }[];
  insights: { trend: string; weekendLift: number; peakDay: string };
  model: string;
}

export function fetchForecast(params: {
  scope: "total" | "category" | "item";
  target?: string;
  period: number;
}): Promise<ForecastData> {
  const qs = new URLSearchParams();
  qs.set("scope", params.scope);
  if (params.target) qs.set("target", params.target);
  qs.set("period", String(params.period));
  return fetchAPI(`/api/forecast?${qs.toString()}`);
}

// ── Categories & Products ──
export function fetchCategories(): Promise<{ categories: string[] }> {
  return fetchAPI("/api/categories");
}

export function fetchProducts(category?: string): Promise<{
  products: { name: string; category: string; qtySold: number; revenue: number }[];
}> {
  const qs = category && category !== "All" ? `?category=${encodeURIComponent(category)}` : "";
  return fetchAPI(`/api/products${qs}`);
}

// ── Upload ──
export async function uploadFile(file: File): Promise<{
  success: boolean;
  fileName?: string;
  records?: number;
  duplicates?: number;
  missingValues?: number;
  invalidRows?: number;
  error?: string;
}> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body: formData,
  });
  return res.json();
}
