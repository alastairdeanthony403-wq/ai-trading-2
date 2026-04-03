import axios from 'axios';

const API_BASE = 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE,
});

export const fetchStockHistory = (ticker) => api.get(`/stock/${ticker}`);
export const fetchAIAnalysis = (ticker) => api.get(`/analysis/${ticker}`);

export default api;
