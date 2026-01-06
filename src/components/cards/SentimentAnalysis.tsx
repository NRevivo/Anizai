import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import type { SentimentDataPoint } from '../../types';

interface SentimentAnalysisProps {
    data: SentimentDataPoint[];
}

export function SentimentAnalysis({ data }: SentimentAnalysisProps) {
    const CustomTooltip = ({ active, payload }: any) => {
        if (active && payload && payload.length) {
            return (
                <div className="bg-white border border-gray-200 rounded-lg p-3 shadow-sm">
                    <p className="text-sm font-medium text-gray-900 mb-2">{payload[0].payload.date}</p>
                    {payload.map((entry: any, index: number) => (
                        <p key={index} className="text-sm" style={{ color: entry.color }}>
                            {entry.name}: {entry.value}%
                        </p>
                    ))}
                </div>
            );
        }
        return null;
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle>Sentiment Analysis</CardTitle>
                <CardDescription>Expert vs public sentiment trends over time</CardDescription>
            </CardHeader>
            <CardContent className="pt-4">
                <ResponsiveContainer width="100%" height={250}>
                    <AreaChart data={data} margin={{ top: 5, right: 5, left: 0, bottom: 5 }}>
                        <defs>
                            <linearGradient id="expertGradient" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#14b8a6" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="#14b8a6" stopOpacity={0} />
                            </linearGradient>
                            <linearGradient id="publicGradient" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#a855f7" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="#a855f7" stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                        <XAxis
                            dataKey="date"
                            tick={{ fontSize: 12, fill: '#6b7280' }}
                            axisLine={{ stroke: '#e5e7eb' }}
                        />
                        <YAxis
                            domain={[0, 100]}
                            tick={{ fontSize: 12, fill: '#6b7280' }}
                            axisLine={{ stroke: '#e5e7eb' }}
                        />
                        <Tooltip content={<CustomTooltip />} />
                        <Legend iconType="circle" />
                        <Area
                            type="monotone"
                            dataKey="expertSentiment"
                            stroke="#14b8a6"
                            strokeWidth={2}
                            fill="url(#expertGradient)"
                            name="Expert Sentiment"
                        />
                        <Area
                            type="monotone"
                            dataKey="publicSentiment"
                            stroke="#a855f7"
                            strokeWidth={2}
                            fill="url(#publicGradient)"
                            name="Public Sentiment"
                        />
                    </AreaChart>
                </ResponsiveContainer>
                <div className="mt-6 grid grid-cols-2 gap-4">
                    <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                        <p className="text-xs text-gray-500 font-medium mb-1">Expert Sentiment</p>
                        <p className="text-2xl font-semibold bg-gradient-to-r from-anizai-teal-600 to-anizai-teal-500 bg-clip-text text-transparent">
                            {data[data.length - 1]?.expertSentiment}%
                        </p>
                        <p className="text-xs text-gray-500 mt-1">Latest reading</p>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                        <p className="text-xs text-gray-500 font-medium mb-1">Public Sentiment</p>
                        <p className="text-2xl font-semibold bg-gradient-to-r from-anizai-purple-600 to-anizai-purple-500 bg-clip-text text-transparent">
                            {data[data.length - 1]?.publicSentiment}%
                        </p>
                        <p className="text-xs text-gray-500 mt-1">Latest reading</p>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
