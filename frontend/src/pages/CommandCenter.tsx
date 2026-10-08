import React, { useEffect, useState } from 'react';
import { api } from '../api/client';
import { StatCard } from '../components/StatCard';
import { Card, CardHeader, CardContent } from '../components/Card';
import { Badge } from '../components/Badge';
import { Shield, AlertTriangle, Activity, Users } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';

export function CommandCenter() {
  const [summary, setSummary] = useState<any>(null);
  const [alerts, setAlerts] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.getSummary(), api.getAlerts()])
      .then(([summaryData, alertsData]) => {
        setSummary(summaryData);
        setAlerts(alertsData);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-text-muted animate-pulse">Initializing Command Center...</div>;
  }

  const riskCounts = summary?.risk?.risk_level_counts || { LOW: 0, MEDIUM: 0, HIGH: 0 };
  const chartData = [
    { name: 'LOW', count: riskCounts.LOW, fill: '#2E8B72' },
    { name: 'MEDIUM', count: riskCounts.MEDIUM, fill: '#E0A43A' },
    { name: 'HIGH', count: riskCounts.HIGH, fill: '#FF2A3D' },
  ];

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Command Center</h1>
          <p className="text-text-muted mt-1">Detection and intelligence overview</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <StatCard title="Monitored Accounts" value={summary?.risk?.total_accounts || 0} icon={Users} />
        <StatCard title="Active Alerts" value={alerts?.count || 0} icon={AlertTriangle} trendUp={true} />
        <StatCard title="High Risk Entities" value={riskCounts.HIGH || 0} icon={Shield} />
        <StatCard title="Payments Analyzed" value={summary?.transaction_count || 0} icon={Activity} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader title="Risk Distribution" subtitle="Account risk levels across the monitored environment" />
          <CardContent className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 20, right: 30, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#25252D" vertical={false} />
                <XAxis dataKey="name" stroke="#8B8B96" tick={{fill: '#8B8B96'}} axisLine={false} tickLine={false} />
                <YAxis stroke="#8B8B96" tick={{fill: '#8B8B96'}} axisLine={false} tickLine={false} />
                <Tooltip 
                  cursor={{fill: '#141419'}} 
                  contentStyle={{backgroundColor: '#0D0D10', borderColor: '#25252D', color: '#F5F5F5'}}
                  itemStyle={{color: '#F5F5F5'}}
                />
                <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={60} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader title="Recent Alerts" subtitle="Highest risk accounts requiring review" />
          <CardContent className="p-0">
            <div className="divide-y divide-border h-80 overflow-y-auto">
              {alerts?.alerts?.slice(0, 5).map((alert: any) => (
                <div key={alert.account_id} className="p-4 flex justify-between items-center hover:bg-elevated/50 transition-colors">
                  <div>
                    <p className="font-medium text-text-primary">{alert.account_id}</p>
                    <p className="text-xs text-text-muted mt-1 font-mono">Score: {alert.risk_score.toFixed(2)}/100</p>
                  </div>
                  <Badge variant={alert.risk_level === 'HIGH' ? 'high' : 'medium'}>
                    {alert.risk_level}
                  </Badge>
                </div>
              ))}
              {(!alerts?.alerts || alerts.alerts.length === 0) && (
                <div className="p-8 text-center text-text-muted">No active alerts found.</div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
