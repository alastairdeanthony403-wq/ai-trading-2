from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
from typing import List, Dict, Any
import random

app = FastAPI(title="StockAI Analysis API")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/stock/{ticker}")
async def get_stock_data(ticker: str):
    """
    Fetches real-time price history for the given ticker using yfinance.
    """
    try:
        stock = yf.Ticker(ticker)
        # Fetch 6 months of daily data for a good chart view
        hist = stock.history(period="6mo")
        
        if hist.empty:
            raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found or no data available.")
        
        # Info for current price and basic stats
        info = stock.info
        current_price = info.get('regularMarketPrice') or info.get('currentPrice') or hist['Close'].iloc[-1]
        
        data = {
            "symbol": ticker.upper(),
            "companyName": info.get('longName', ticker.upper()),
            "currentPrice": round(float(current_price), 2),
            "currency": info.get('currency', 'USD'),
            "history": {
                "labels": hist.index.strftime('%Y-%m-%d').tolist(),
                "prices": [round(float(p), 2) for p in hist['Close'].tolist()]
            }
        }
        return data
    except Exception as e:
        print(f"Error fetching stock data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analysis/{ticker}")
async def get_ai_analysis(ticker: str):
    """
    Simulates integration with your custom AI trading analysis system.
    This replaces the inference call to your local or remote AI engine.
    """
    t = ticker.upper()
    
    # Mock dynamic analysis based on ticker name for demonstration
    # In a real app, this function would call your AI model
    bullish_pool = [
        "Strong quarterly earnings growth",
        "50-day SMA crossing above 200-day SMA",
        "Positive sentiment in sector ETFs",
        "RSI suggests oversold conditions",
        "Increased institutional buying",
        "Expanding profit margins year-over-year"
    ]
    bearish_pool = [
        "High relative valuation vs peers",
        "Macroeconomic headwinds in supply chain",
        "Regulatory scrutiny increasing",
        "Resistance at 52-week highs",
        "Inside selling reported in SEC filings"
    ]

    # Generate a deterministic but ticker-specific mock analysis
    random.seed(t) 
    num_bullish = random.randint(2, 4)
    num_bearish = random.randint(1, 2)
    
    # Base price calculation (mocking a price target)
    try:
        stock = yf.Ticker(t)
        price = stock.info.get('regularMarketPrice') or stock.info.get('currentPrice') or 150.0
    except:
        price = 150.0

    base_target = round(price * random.uniform(0.9, 1.2), 2)
    range_low = round(base_target * 0.92, 2)
    range_high = round(base_target * 1.08, 2)

    mock_analysis = {
        "ticker": t,
        "summary": f"{t} displaying {'strong bullish' if base_target > price else 'slight consolidation'} momentum with key support levels holding.",
        "bullish_signals": random.sample(bullish_pool, num_bullish),
        "bearish_signals": random.sample(bearish_pool, num_bearish),
        "price_target": { 
            "base": base_target, 
            "range": [range_low, range_high], 
            "time_horizon_days": random.choice([30, 60, 90, 180]) 
        },
        "reasoning": f"Our proprietary AI model has analyzed {t}'s historical volatility and current fundamental indicators. The surge in specific volume clusters suggests that smart money is accumulating near current price levels. While broader market volatility remains a factor, the technical setup for {t} provides a compelling risk/reward ratio for the mid-term horizon."
    }
    return mock_analysis

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
