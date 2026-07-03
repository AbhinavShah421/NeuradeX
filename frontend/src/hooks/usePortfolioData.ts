import { useEffect, useState } from 'react';
import apiService from '../services/api';
import { Portfolio, Performance } from '../types';

// Loads the core portfolio/performance/alerts data used across the Portfolio
// page's tabs. Fetches once on mount; call `refetch` to reload on demand.
export function usePortfolioData() {
  const [portfolio,   setPortfolio]   = useState<Portfolio | null>(null);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [loading,     setLoading]     = useState(true);
  const [alerts,      setAlerts]      = useState<any[]>([]);

  const fetchPortfolioData = async () => {
    try {
      setLoading(true);
      const [portRes, perfRes, alertRes] = await Promise.all([
        apiService.getPortfolio(),
        apiService.getPerformance(),
        apiService.getAlerts(),
      ]);
      if (portRes.data)  setPortfolio(portRes.data);
      if (perfRes.data)  setPerformance(perfRes.data);
      if (alertRes.data) setAlerts(alertRes.data as any[]);
    } catch (err) {
      console.error('Error fetching portfolio:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchPortfolioData(); }, []);

  return { portfolio, performance, loading, alerts, refetch: fetchPortfolioData };
}
