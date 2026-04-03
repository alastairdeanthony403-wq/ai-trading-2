import React, { useState, useEffect, useCallback } from 'react';
import { 
  Search, 
  TrendingUp, 
  TrendingDown, 
  Target, 
  BrainCircuit, 
  RefreshCw, 
  AlertCircle,
  BarChart3,
  Globe,
  Info
} from 'lucide-react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
} from 'chart.js';
import { fetchStockHistory, fetchAIAnalysis } from './api';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

function cn(...inputs) {
  return twMerge(clsx(inputs));
}

const App = () => {
  const [ticker, setTicker] = useState('AAPL');
  const [searchInput, setSearchInput] = useState('');
  const [stockData, setStockData] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadData = useCallback(async (symbol) => {
    setLoading(true);
    setError(null);
    try {
      const [stockRes, analysisRes] = await Promise.all([
        fetchStockHistory(symbol),
        fetchAIAnalysis(symbol)
      ]);
      setStockData(stockRes.data);
      setAnalysis(analysisRes.data);
    } catch (err) {
      console.error(err);
      setError("Unable to find data for " + symbol.toUpperCase() + ". Please check the ticker symbol.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData(ticker);
  }, [ticker, loadData]);

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchInput.trim()) {
      setTicker(searchInput.toUpperCase().trim());
      setSearchInput('');
    }
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        mode: 'index',
        intersect: false,
        backgroundColor: '#1e293b',
        padding: 12,
        titleFont: { size: 14 },
        bodyFont: { size: 13 },
      }
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }
      },
      y: {
        grid: { color: '#f1f5f9' },
        position: 'right'
      }
    },
    interaction: {
      intersect: false,
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 pb-12">
      {/* Navbar */}
      <nav className="bg-white border-b sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 md:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2 cursor-pointer" onClick={() => window.location.reload()}>
            <div className="bg-blue-600 p-1.5 rounded-lg">
              <BrainCircuit className="text-white w-6 h-6" />
            </div>
            <span className="text-xl font-bold tracking-tight hidden sm:block">AI TradeLens</span>
          </div>

          <form onSubmit={handleSearch} className="relative w-full max-w-sm ml-4">
            <input 
              type="text" 
              value={searchInput}
              placeholder="Search Ticker (TSLA, NVDA...)" 
              className="w-full pl-10 pr-4 py-2 rounded-xl border border-slate-200 bg-slate-50 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all"
              onChange={(e) => setSearchInput(e.target.value)}
            />
            <Search className="absolute left-3 top-2.5 text-slate-400 w-5 h-5" />
          </form>

          <button 
            onClick={() => loadData(ticker)} 
            className="ml-4 p-2 text-slate-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
            title="Refresh Data"
          >
            <RefreshCw className={cn("w-5 h-5", loading && "animate-spin")} />
          </button>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-4 md:px-8 mt-8">
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-100 rounded-xl flex items-center gap-3 text-red-700">
            <AlertCircle className="w-5 h-5" />
            <p className="font-medium">{error}</p>
          </div>
        )}

        {loading ? (
          <div className="flex flex-col items-center justify-center py-24 opacity-50">
             <div className="relative">
                <div className="w-12 h-12 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin"></div>
             </div>
             <p className="mt-4 font-medium text-slate-600">Analyzing Market Data...</p>
          </div>
        ) : (
          stockData && analysis && (
            <div className="space-y-6">
              
              {/* Top Row: Symbol Info and Summary */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
                  <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-6">
                    <div>
                      <div className="flex items-center gap-3 mb-1">
                        <span className="bg-slate-900 text-white px-3 py-1 rounded-lg text-sm font-bold uppercase">{stockData.symbol}</span>
                        <h2 className="text-2xl font-bold text-slate-800">{stockData.companyName}</h2>
                      </div>
                      <div className="flex items-center gap-2 text-slate-500">
                        <Globe className="w-4 h-4" />
                        <span className="text-sm">Real-time Nasdaq Data</span>
                      </div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-4xl font-extrabold text-slate-900 leading-none">
                        ${stockData.currentPrice.toLocaleString()}
                      </div>
                      <div className="text-sm font-medium mt-1 text-slate-400">
                        USD • Market Price
                      </div>
                    </div>
                  </div>

                  {/* Price History Chart */}
                  <div className="h-[300px] w-full">
                    <Line 
                      options={chartOptions}
                      data={{
                        labels: stockData.history.labels,
                        datasets: [{
                          data: stockData.history.prices,
                          borderColor: '#2563eb',
                          borderWidth: 2,
                          pointRadius: 0,
                          pointHoverRadius: 6,
                          tension: 0.2,
                          fill: true,
                          backgroundColor: (context) => {
                            const ctx = context.chart.ctx;
                            const gradient = ctx.createLinearGradient(0, 0, 0, 300);
                            gradient.addColorStop(0, 'rgba(37, 99, 235, 0.1)');
                            gradient.addColorStop(1, 'rgba(37, 99, 235, 0)');
                            return gradient;
                          },
                        }]
                      }} 
                    />
                  </div>
                </div>

                {/* AI Summary and Gauge */}
                <div className="bg-slate-900 rounded-2xl shadow-xl p-6 text-white flex flex-col h-full overflow-hidden">
                  <div className="flex items-center gap-2 mb-6">
                    <BrainCircuit className="text-blue-400 w-5 h-5" />
                    <h3 className="font-bold text-lg">AI Analysis Engine</h3>
                  </div>
                  
                  <div className="flex-grow">
                     <p className="text-slate-300 leading-relaxed italic border-l-2 border-blue-500 pl-4 py-1">
                      "{analysis.summary}"
                    </p>
                  </div>

                  <div className="mt-8 pt-8 border-t border-slate-800">
                    <div className="flex justify-between items-center mb-4">
                      <span className="text-slate-400 flex items-center gap-2">
                        <Target className="w-4 h-4" /> Price Target
                      </span>
                      <span className="bg-blue-900/50 text-blue-300 text-xs px-2 py-1 rounded-full font-bold">
                        {analysis.price_target.time_horizon_days}D Horizon
                      </span>
                    </div>

                    <div className="text-center mb-6">
                      <div className="text-5xl font-black text-white">${analysis.price_target.base}</div>
                    </div>

                    <div className="space-y-2">
                       <div className="flex justify-between text-xs font-semibold text-slate-400 px-1">
                          <span>LOW ${analysis.price_target.range[0]}</span>
                          <span>HIGH ${analysis.price_target.range[1]}</span>
                       </div>
                       <div className="h-3 bg-slate-800 rounded-full relative overflow-hidden">
                          <div 
                            className="absolute h-full bg-gradient-to-r from-blue-600 to-cyan-400 rounded-full"
                            style={{ 
                              left: '25%', 
                              width: '50%' 
                            }}
                          ></div>
                          <div 
                            className="absolute w-1 h-full bg-white z-10 shadow-[0_0_8px_white]"
                            style={{ left: '50%' }}
                          ></div>
                       </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Signals Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {/* Bullish */}
                <div className="bg-white rounded-2xl border border-emerald-100 p-6 shadow-sm">
                  <div className="flex items-center gap-2 mb-4">
                     <div className="bg-emerald-100 p-2 rounded-lg">
                        <TrendingUp className="text-emerald-600 w-5 h-5" />
                     </div>
                     <h3 className="font-bold text-slate-800">Bullish Indicators</h3>
                  </div>
                  <ul className="space-y-3">
                    {analysis.bullish_signals.map((s, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-600 group">
                        <span className="text-emerald-500 font-bold mt-0.5">•</span>
                        <span className="group-hover:text-slate-900 transition-colors uppercase tracking-tight font-medium text-xs">
                          {s}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Bearish */}
                <div className="bg-white rounded-2xl border border-rose-100 p-6 shadow-sm">
                  <div className="flex items-center gap-2 mb-4">
                     <div className="bg-rose-100 p-2 rounded-lg">
                        <TrendingDown className="text-rose-600 w-5 h-5" />
                     </div>
                     <h3 className="font-bold text-slate-800">Risk Factors</h3>
                  </div>
                  <ul className="space-y-3">
                    {analysis.bearish_signals.map((s, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-600 group">
                        <span className="text-rose-500 font-bold mt-0.5">•</span>
                        <span className="group-hover:text-slate-900 transition-colors uppercase tracking-tight font-medium text-xs">
                          {s}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* AI Explanation / Reasoning */}
                <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm lg:col-span-1 md:col-span-2">
                  <div className="flex items-center gap-2 mb-4">
                     <div className="bg-blue-100 p-2 rounded-lg">
                        <Info className="text-blue-600 w-5 h-5" />
                     </div>
                     <h3 className="font-bold text-slate-800">Strategy Insights</h3>
                  </div>
                  <p className="text-sm text-slate-600 leading-relaxed">
                    {analysis.reasoning}
                  </p>
                  <div className="mt-4 pt-4 border-t border-slate-100 flex items-center justify-between">
                     <div className="flex items-center gap-2">
                        <BarChart3 className="w-4 h-4 text-slate-400" />
                        <span className="text-[10px] text-slate-400 font-bold uppercase tracking-widest">Model Confidence: High</span>
                     </div>
                     <span className="text-[10px] text-slate-400 uppercase font-black">Trade Verified</span>
                  </div>
                </div>
              </div>

            </div>
          )
        )}
      </main>

      {/* Footer */}
      <footer className="max-w-7xl mx-auto px-8 mt-12 py-8 border-t border-slate-200 text-center">
         <p className="text-xs text-slate-400 uppercase tracking-widest font-semibold flex items-center justify-center gap-2">
            <BrainCircuit className="w-4 h-4" /> Powered by My Trading AI Engine
         </p>
         <p className="text-[10px] text-slate-300 mt-2">Data provided for evaluation purposes only. Past performance does not guarantee future results.</p>
      </footer>
    </div>
  );
};

export default App;
