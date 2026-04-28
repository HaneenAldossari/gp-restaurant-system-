// ── Mock Data for Restaurant Sales Analytics Demo ──

export const menuItems = [
  { name: "Espresso", category: "Drinks", price: 13, cost: 3.9, qtySold: 784, revenue: 10192 },
  { name: "Cappuccino", category: "Drinks", price: 16, cost: 5.2, qtySold: 612, revenue: 9792 },
  { name: "Latte", category: "Drinks", price: 18, cost: 6.0, qtySold: 530, revenue: 9540 },
  { name: "Fresh Juice", category: "Drinks", price: 15, cost: 7.5, qtySold: 340, revenue: 5100 },
  { name: "Iced Tea", category: "Drinks", price: 12, cost: 3.0, qtySold: 290, revenue: 3480 },
  { name: "Grilled Chicken", category: "Main Course", price: 45, cost: 18, qtySold: 420, revenue: 18900 },
  { name: "Beef Burger", category: "Main Course", price: 38, cost: 15, qtySold: 385, revenue: 14630 },
  { name: "Pasta Alfredo", category: "Main Course", price: 35, cost: 12, qtySold: 310, revenue: 10850 },
  { name: "Salmon Fillet", category: "Main Course", price: 55, cost: 28, qtySold: 180, revenue: 9900 },
  { name: "Caesar Salad", category: "Appetizers", price: 22, cost: 7.0, qtySold: 260, revenue: 5720 },
  { name: "Garlic Bread", category: "Appetizers", price: 12, cost: 3.5, qtySold: 410, revenue: 4920 },
  { name: "Soup of the Day", category: "Appetizers", price: 14, cost: 4.0, qtySold: 220, revenue: 3080 },
  { name: "Spring Rolls", category: "Appetizers", price: 16, cost: 8.0, qtySold: 150, revenue: 2400 },
  { name: "Tiramisu", category: "Desserts", price: 20, cost: 7.0, qtySold: 195, revenue: 3900 },
  { name: "Cheesecake", category: "Desserts", price: 22, cost: 8.0, qtySold: 170, revenue: 3740 },
  { name: "Chocolate Lava", category: "Desserts", price: 24, cost: 10, qtySold: 130, revenue: 3120 },
  { name: "Ice Cream", category: "Desserts", price: 10, cost: 3.5, qtySold: 350, revenue: 3500 },
  { name: "French Fries", category: "Sides", price: 10, cost: 3.0, qtySold: 520, revenue: 5200 },
  { name: "Onion Rings", category: "Sides", price: 12, cost: 4.5, qtySold: 180, revenue: 2160 },
  { name: "Mozzarella Sticks", category: "Sides", price: 14, cost: 7.5, qtySold: 95, revenue: 1330 },
];

export const categories = ["All", "Drinks", "Main Course", "Appetizers", "Desserts", "Sides"];

// Daily revenue for last 30 days
export const dailyRevenue = Array.from({ length: 30 }, (_, i) => {
  const date = new Date(2025, 10, i + 1);
  const dayOfWeek = date.getDay();
  const isWeekend = dayOfWeek === 5 || dayOfWeek === 6;
  const base = isWeekend ? 5200 : 3600;
  const variance = Math.sin(i * 0.3) * 800 + (Math.random() - 0.5) * 600;
  return {
    date: date.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    revenue: Math.round(base + variance + i * 30),
    orders: Math.round((base + variance) / 35),
  };
});

// Weekly revenue
export const weeklyRevenue = [
  { week: "W1 Nov", revenue: 26800, orders: 765 },
  { week: "W2 Nov", revenue: 28400, orders: 812 },
  { week: "W3 Nov", revenue: 27100, orders: 774 },
  { week: "W4 Nov", revenue: 30200, orders: 863 },
];

// Monthly revenue
export const monthlyRevenue = [
  { month: "Jun", revenue: 98000, growth: 0 },
  { month: "Jul", revenue: 105000, growth: 7.1 },
  { month: "Aug", revenue: 112000, growth: 6.7 },
  { month: "Sep", revenue: 108000, growth: -3.6 },
  { month: "Oct", revenue: 115000, growth: 6.5 },
  { month: "Nov", revenue: 122500, growth: 6.5 },
];

// Sales by category
export const salesByCategory = [
  { name: "Drinks", value: 38104, color: "#6366f1" },
  { name: "Main Course", value: 54280, color: "#f59e0b" },
  { name: "Appetizers", value: 16120, color: "#10b981" },
  { name: "Desserts", value: 14260, color: "#f43f5e" },
  { name: "Sides", value: 8690, color: "#8b5cf6" },
];

// Heatmap data (day x hour)
export const heatmapData = (() => {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const hours = ["9AM", "10AM", "11AM", "12PM", "1PM", "2PM", "3PM", "4PM", "5PM", "6PM", "7PM", "8PM", "9PM", "10PM"];
  const data: { day: string; hour: string; value: number }[] = [];
  days.forEach((day, di) => {
    hours.forEach((hour, hi) => {
      const isLunch = hi >= 3 && hi <= 5;
      const isDinner = hi >= 8 && hi <= 11;
      const isWeekend = di >= 5;
      let base = 20;
      if (isLunch) base += 50;
      if (isDinner) base += 40;
      if (isWeekend) base += 25;
      data.push({ day, hour, value: Math.round(base + Math.random() * 20) });
    });
  });
  return data;
})();

// Forecast data
export const forecastData = (() => {
  const data = [];
  for (let i = 0; i < 30; i++) {
    const date = new Date(2025, 10, i + 1);
    const dayOfWeek = date.getDay();
    const isWeekend = dayOfWeek === 5 || dayOfWeek === 6;
    const base = isWeekend ? 5200 : 3600;
    const value = Math.round(base + Math.sin(i * 0.3) * 800 + i * 30);
    data.push({
      date: date.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      actual: value,
      predicted: null as number | null,
      upper: null as number | null,
      lower: null as number | null,
    });
  }
  for (let i = 30; i < 60; i++) {
    const date = new Date(2025, 10, i + 1);
    const dayOfWeek = date.getDay();
    const isWeekend = dayOfWeek === 5 || dayOfWeek === 6;
    const base = isWeekend ? 5400 : 3800;
    const predicted = Math.round(base + Math.sin(i * 0.3) * 700 + i * 25);
    data.push({
      date: date.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      actual: null,
      predicted,
      upper: Math.round(predicted * 1.12),
      lower: Math.round(predicted * 0.88),
    });
  }
  return data;
})();

// Weekly pattern
export const weeklyPattern = [
  { day: "Mon", avg: 3200 },
  { day: "Tue", avg: 3400 },
  { day: "Wed", avg: 3100 },
  { day: "Thu", avg: 3600 },
  { day: "Fri", avg: 4800 },
  { day: "Sat", avg: 5500 },
  { day: "Sun", avg: 5100 },
];

// Users for admin panel
export const users = [
  { id: 1, name: "Haneen Aldossari", email: "haneen@restaurant.com", role: "Admin", status: "Active", lastLogin: "2025-12-01" },
  { id: 2, name: "Arwa Alyami", email: "arwa@restaurant.com", role: "Manager", status: "Active", lastLogin: "2025-11-30" },
  { id: 3, name: "Noura Aldossari", email: "noura@restaurant.com", role: "Manager", status: "Active", lastLogin: "2025-11-29" },
  { id: 4, name: "Norah Aljuwayr", email: "norah@restaurant.com", role: "Cashier", status: "Active", lastLogin: "2025-11-28" },
  { id: 5, name: "Ahmed Al-Farsi", email: "ahmed@restaurant.com", role: "Cashier", status: "Inactive", lastLogin: "2025-10-15" },
];

// Upload history
export const uploadHistory = [
  { id: 1, fileName: "sales_nov_2025.csv", date: "2025-11-30", records: 4820, status: "Processed" },
  { id: 2, fileName: "sales_oct_2025.xlsx", date: "2025-10-31", records: 4650, status: "Processed" },
  { id: 3, fileName: "sales_sep_2025.csv", date: "2025-09-30", records: 4310, status: "Processed" },
  { id: 4, fileName: "sales_aug_2025.csv", date: "2025-08-31", records: 4580, status: "Processed" },
];

// Boston matrix classification
export function classifyItems(items: typeof menuItems) {
  const totalQty = items.reduce((sum, i) => sum + i.qtySold, 0);
  const withMetrics = items.map((item) => ({
    ...item,
    popularity: (item.qtySold / totalQty) * 100,
    profitMargin: ((item.price - item.cost) / item.price) * 100,
  }));
  const avgPopularity = withMetrics.reduce((s, i) => s + i.popularity, 0) / withMetrics.length;
  const avgMargin = withMetrics.reduce((s, i) => s + i.profitMargin, 0) / withMetrics.length;
  return withMetrics.map((item) => {
    let classification: "Star" | "Plowhorse" | "Puzzle" | "Dog";
    if (item.popularity >= avgPopularity && item.profitMargin >= avgMargin) classification = "Star";
    else if (item.popularity >= avgPopularity && item.profitMargin < avgMargin) classification = "Plowhorse";
    else if (item.popularity < avgPopularity && item.profitMargin >= avgMargin) classification = "Puzzle";
    else classification = "Dog";
    return { ...item, classification, avgPopularity, avgMargin };
  });
}
